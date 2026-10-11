// The task pages' chat (component/task_chat) and the clicks on the head image. A click, a typed or spoken turn, a tapped
// answer: each a message from the user; the robot answers in the chat (aloud with 语音讲解 on) and leads on to what is
// still to choose, with the candidates numbered on the head image and answers to tap. A click sets the 任务步骤 step
// framed in blue: ① the object, ② where in the box or the desk point (抓取摆放).
"use strict";

// awaiting: what the robot last asked for, "target" (which object), "place" (where it goes) or "confirm" (is it right).
const chat = { messages: [], timer: null, awaiting: "target" };
const MARKER_LOOK = { pending: "border-blue-500 bg-blue-500/20 animate-pulse", ok: "border-emerald-500 bg-emerald-500/20", fail: "border-red-500 bg-red-500/25" };
const MARKER_BASE = "pointer-events-none absolute size-7 -translate-x-1/2 -translate-y-1/2 rounded-full border-[3px]";
const GREETING = PAGE.place ? "你好！分两步：先选要抓的物体（在左边画面里点它，或者告诉我它的名字），再选放到哪里。"
  : "你好！先选要抓的物体：在左边画面里点它，或者告诉我它的名字。也可以问我「桌上有什么」。";
const PLACE_CHOICES = [{ label: "放进盒子", say: "放进盒子" }, { label: "放到桌面点", say: "放到桌面点" }];
// A choice the backend refused, said in the chat: the head camera's own words (503) as they are, else with how to go on.
const refusal = (error, say) => (error.status === 503 ? error.message : say(error.message.replace(/（[^）]*）/g, "")));

// --- messages ---

// A message: role "user" / "robot"; a robot's may wait (pending: a running clock), carry a note and answers to tap.
function chatAdd(role, text, extra = {}) {
  const message = { role, text, note: extra.note || "", options: extra.options || [], pending: Boolean(extra.pending), started: Date.now() };
  chat.messages.push(message);
  renderChat();
  if (role === "robot" && !message.pending && !extra.silent) say(text);
  return message;
}

function chatResolve(message, text, extra = {}) {
  Object.assign(message, { text, note: extra.note || "", options: extra.options || [], pending: false });
  renderChat();
  say(text);
}

function renderChat() {
  const step = activeStep(task.status);
  byId("target-text").placeholder = step === 2 && placeKind() === "spot" ? "在左边画面里点放置位置，或者打字 / 说话"
    : step === 2 && placeKind() === "box" ? "在左边画面里点盒子里要放的位置，或者打字 / 说话" : "说点什么，或在左边画面里点一下";
  const now = Date.now();
  const last = chat.messages.length - 1;
  byId("chat").innerHTML = chat.messages.map((message, index) => {
    if (message.role === "user") {
      return `<li class="flex justify-end"><p class="max-w-[85%] rounded-xl bg-blue-100 px-3 py-1.5 text-sm text-gray-800">${escapeText(message.text)}</p></li>`;
    }
    const clock = message.pending ? ` <span class="font-mono text-[11px] text-gray-400">${((now - message.started) / 1000).toFixed(1)} s</span>` : "";
    const answers = index === last && message.options.length ? `<div class="mt-2 flex flex-wrap gap-1.5">${message.options.map((option, i) =>
      `<button type="button" data-answer="${i}" class="rounded-full border border-blue-600 bg-white px-3 py-1 text-xs font-semibold text-blue-600 hover:bg-blue-50">${escapeText(option.label)}</button>`).join("")}</div>` : "";
    return `<li class="flex items-start gap-2">
      <span class="mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[9px] font-bold text-white">XL</span>
      <div class="min-w-0 max-w-[85%]">
        <p class="rounded-xl border border-gray-200 bg-white px-3 py-1.5 text-sm ${message.pending ? "text-gray-500" : "text-gray-800"}">${message.pending ? "◌ " : ""}${escapeText(message.text)}${clock}</p>
        ${message.note ? `<p class="mt-0.5 px-1 text-[11px] text-gray-400">${escapeText(message.note)}</p>` : ""}${answers}
      </div></li>`;
  }).join("");
  byId("chat").scrollTop = byId("chat").scrollHeight;
  byId("chat").querySelectorAll("[data-answer]").forEach((button) => button.addEventListener("click", () => answer(chat.messages[last].options[Number(button.dataset.answer)])));
  const waiting = chat.messages.some((message) => message.pending);
  if (waiting && !chat.timer) chat.timer = setInterval(renderChat, 100);
  if (!waiting && chat.timer) { clearInterval(chat.timer); chat.timer = null; }
}

// The robot's answer into its waiting message: the reply, its answers to tap, the candidates on the head image.
function chatShow(reply, result, extra = {}) {
  if (result.awaiting) chat.awaiting = result.awaiting;
  chatResolve(reply, `${extra.prefix || ""}${result.reply}`, { note: extra.note, options: result.options || [] });
  showCandidates(result.candidates);
}

// After the page itself chose (a click, a tapped answer): the robot's next question (is the object right? where does it
// go? the right arm cannot reach it). prefix: what the page's own choice was, said first (好的，放在这里…).
async function chatNext(reply, extra = {}) {
  try {
    chatShow(reply, await api("POST", "/api/task/next", { task: CHAT_TASK }), extra);
  } catch (error) {
    chatResolve(reply, error.message || "没连上对话服务。");
  }
}

// The conversation so far, for the robot to understand the next turn with (an answer like 右边那个).
const chatHistory = () => chat.messages.filter((message) => !message.pending).slice(-8).map(({ role, text }) => ({ role, text }));

// A typed or spoken turn: understood with the conversation before it, then answered (or asked back).
async function sendChat(text, prefix = "") {
  marker(null);
  showCandidates([]);
  const history = chatHistory();
  chatAdd("user", `${prefix}${text}`);
  const reply = chatAdd("robot", "我想一想…", { pending: true });
  try {
    const result = await request("/api/task/chat", { task: CHAT_TASK, text, history, awaiting: chat.awaiting });
    chatShow(reply, result);
    // The user said to start (开始抓吧): that is the go-ahead, no button to press (10-10); the chat checked that
    // everything is chosen (the page's status may be a poll behind).
    if (result.go) runTask((text) => chatAdd("robot", text));
  } catch (error) {
    chatResolve(reply, error.message);
  }
}

// --- the head image ---

// The ring where the click landed: pending while it is being recognised, then green or red; null hides it.
function marker(state, event) {
  const ring = byId("click-marker");
  if (event) {
    const box = ring.parentElement.getBoundingClientRect();
    ring.style.left = `${((event.clientX - box.left) / box.width) * 100}%`;
    ring.style.top = `${((event.clientY - box.top) / box.height) * 100}%`;
  }
  ring.className = state ? `${MARKER_BASE} ${MARKER_LOOK[state]}` : `${MARKER_BASE} hidden`;
}

// The candidates the robot asks about, numbered left to right on the head image (the picture fills its 4:3 frame):
// objects in blue, boxes to put it in (kind "place") in orange.
function showCandidates(candidates) {
  const image = byId("head-image");
  const width = image.naturalWidth || 640;
  const height = image.naturalHeight || 480;
  byId("cand-layer").innerHTML = (candidates || []).map(({ n, box, kind }) => {
    const [x1, y1, x2, y2] = box;
    const [frame, badge] = kind === "place" ? ["border-orange-500", "bg-orange-500"] : ["border-blue-600", "bg-blue-600"];
    return `<div class="absolute rounded border-2 ${frame}" style="left:${x1 / width * 100}%;top:${y1 / height * 100}%;width:${(x2 - x1) / width * 100}%;height:${(y2 - y1) / height * 100}%">
      ${n ? `<span class="absolute -top-3.5 left-1/2 inline-flex size-6 -translate-x-1/2 items-center justify-center rounded-full border-2 border-white ${badge} text-xs font-bold text-white">${n}</span>` : ""}</div>`;
  }).join("");
}

// A click on the head image, as a pixel of the picture under the object-contain letterbox; null outside it.
function imagePoint(image, event) {
  const rect = image.getBoundingClientRect();
  const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
  const x = (event.clientX - rect.left - (rect.width - image.naturalWidth * scale) / 2) / scale;
  const y = (event.clientY - rect.top - (rect.height - image.naturalHeight * scale) / 2) / scale;
  const inside = x >= 0 && y >= 0 && x < image.naturalWidth && y < image.naturalHeight;
  return inside ? [Math.round(x), Math.round(y)] : null;
}

// A click sets the step framed in blue; with all chosen it moves where it goes (抓取摆放), never the object (a click on the
// box was taken for the object, 10-10): that is 第 1 步's 重新选. 物体抓取 has the object only: a click chooses it again.
function onHeadClick(point, event) {
  const step = activeStep(task.status);
  const kind = placeKind();
  if (step === 1 || !PAGE.place) clickTarget(point, event);
  else if (kind === "box") clickBox(point, event);
  else if (kind === "spot") clickSpot(point, event);
  else chatAdd("robot", "第 2 步先选：放进盒子，还是放到桌面点？", { options: PLACE_CHOICES });
}

async function clickTarget(point, event) {
  marker("pending", event);
  showCandidates([]);
  chatAdd("user", `点选：画面 (${point[0]}, ${point[1]})`);
  const reply = chatAdd("robot", "我看看你点的是什么…", { pending: true });
  const started = Date.now();
  try {
    const result = await request(`${GRASP_API}/api/real/target`, { point });
    marker("ok");
    await chatNext(reply, { note: `识别 ${((Date.now() - started) / 1000).toFixed(1)} s · ${result.target.by || "SAM"}` });
  } catch (error) {
    marker("fail");
    chat.awaiting = "target";
    chatResolve(reply, refusal(error, (why) => `没认出来：${why}。再点一下物体中间，或者告诉我它的名字。`));
  }
}

// The desk point 抓取摆放 puts it on (Penpot 「抓取摆放 / 电脑 · 放到桌面点（对话）」).
async function clickSpot(point, event) {
  marker("pending", event);
  showCandidates([]);
  chatAdd("user", "📍 点了画面（放置位置）");
  const reply = chatAdd("robot", "我看看这里能不能放…", { pending: true });
  try {
    const result = await request(`${GRASP_API}/api/real/place`, { kind: "spot", point });
    marker("ok");
    const [x, y] = result.place_spot.xy;
    await chatNext(reply, { prefix: `好的，放在这里（橙色叉，桌面 x ${x.toFixed(2)} m，y ${y.toFixed(2)} m）。` });
  } catch (error) {
    marker("fail");
    chatResolve(reply, refusal(error, (why) => `这里放不了：${why}。再点一下桌面上空着的地方。`));
  }
}

// Where in a box: the + where it was clicked, placed there.
async function clickBox(point, event) {
  marker("pending", event);
  const placed = await chooseBox(point, "📍 点了盒子里的位置", "click");
  marker(placed ? "ok" : "fail");
}

// A box by its point: by "click" (placed where it was clicked) or "words" (a tapped answer: placed in the middle of the
// part of the box the right arm reaches). True when it was chosen.
async function chooseBox(point, label, by) {
  showCandidates([]);
  chatAdd("user", label);
  const reply = chatAdd("robot", "好的…", { pending: true });
  try {
    await request(`${GRASP_API}/api/real/place`, { kind: "box", point, by });
    await chatNext(reply);
    return true;
  } catch (error) {
    chatResolve(reply, refusal(error, (why) => `没选上：${why}`));
    return false;
  }
}

// --- the answers to tap ---

// An answer tapped under the robot's question: said to the chat, done here (go, redo), a box, or the candidate it names.
async function answer(option) {
  if (!option) return;
  if (option.say) { sendChat(option.say); return; }
  if (option.place_box) { chooseBox(option.place_box, option.label, "words"); return; }
  chatAdd("user", option.label);
  if (option.action === "redo") { await redoStep(1); return; }
  if (option.action === "go") {
    // Started with everything chosen only: else the robot asks for what is missing.
    if (activeStep(task.status) !== null) {
      await chatNext(chatAdd("robot", "好的…", { pending: true }));
      return;
    }
    chat.awaiting = "confirm";
    chatAdd("robot", PAGE.place ? "好的，开始抓取摆放，用右手。要停就说「停」。" : `好的，开始抓${sayName(task.status.target.label)}。要停就说「停」。`);
    runTask((text) => chatAdd("robot", text));
    return;
  }
  showCandidates([]);
  const reply = chatAdd("robot", "好的…", { pending: true });
  try {
    await request(`${GRASP_API}/api/real/target`, { prompt: option.prompt, point: option.point });
    await chatNext(reply);
  } catch (error) {
    chatResolve(reply, refusal(error, (why) => `没选上：${why}`));
  }
}

// 重新选 (任务步骤): that step is chosen again; the other stays.
async function redoStep(n) {
  marker(null);
  showCandidates([]);
  try {
    if (n === 1) {
      await request(`${GRASP_API}/api/real/target`, { clear: true });
      chat.awaiting = "target";
      chatAdd("robot", "好的，重新选要抓的物体：在左边画面里点它，或者告诉我它的名字。");
    } else {
      await request(`${GRASP_API}/api/real/place`, { kind: null });
      chat.awaiting = "place";
      chatAdd("robot", "好的，重新选放的地方：放进盒子，还是放到桌面点？", { options: PLACE_CHOICES });
    }
  } catch (error) {
    chatAdd("robot", `没能重新选：${error.message}`);
  }
}

// 清除: the object and where it goes, and the conversation.
async function clearAll() {
  marker(null);
  showCandidates([]);
  byId("target-text").value = "";
  try {
    await request(`${GRASP_API}/api/real/target`, { clear: true });
    if (PAGE.place) await request(`${GRASP_API}/api/real/place`, { kind: null });
  } catch (error) {
    notify(`没清除：${error.message}`);
    return;
  }
  chat.messages = [];
  chat.awaiting = "target";
  chatAdd("robot", PAGE.place ? "清除了。第 1 步：要抓什么？在左边画面里点它，或者告诉我它的名字。"
    : "目标已清除。要抓哪一个？在左边画面里点它，或者告诉我它的名字。");
}

function initChat() {
  byId("head-image").addEventListener("click", (event) => {
    const point = imagePoint(byId("head-image"), event);
    if (!point || task.requesting || (task.status && busy(task.status))) return;
    onHeadClick(point, event);
  });
  if (PAGE.place) {
    // 放置 tapped: said to the chat, which then asks on (where in the box, or to click the desk point).
    byId("place-spot").addEventListener("click", () => sendChat("放到桌面点"));
    byId("place-box").addEventListener("click", () => sendChat("放进盒子"));
    byId("step-2-redo").addEventListener("click", () => redoStep(2));
  }
  byId("step-1-redo").addEventListener("click", () => redoStep(1));
  byId("target-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = byId("target-text").value.trim();
    if (!text) return;
    byId("target-text").value = "";
    sendChat(text);
  });
  byId("btn-target-clear").addEventListener("click", clearAll);
  chatAdd("robot", GREETING, { silent: true });
}
