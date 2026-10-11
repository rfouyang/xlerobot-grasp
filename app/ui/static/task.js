// The two task pages (物体抓取: pick_target; 抓取摆放: place_in_box / place_at_spot): the task's state, polled through
// /grasp-api, its 任务步骤 (Penpot 「物体抓取 / 抓取摆放 · 电脑（修订）」), the start with its 执行前确认, and the debug
// details. The chat and the clicks on the head image are task_chat.js; the voice is task_voice.js. The backend runs
// one task at a time: the page shows whatever it holds and takes it over (resets it to its own task) to start.
"use strict";

const PAGE = window.TASK_PAGE;
const START_LABEL = PAGE.place ? "开始抓取摆放" : "开始抓取";
const ACTION_NAMES = { grasp: "抓取（旧）", move_right: "瓶子右移", place_in_box: "抓取摆放", place_at_spot: "抓取摆放", pick_target: "物体抓取" };
const ARM_NAMES = { auto: "自动", left: "左手", right: "右手" };
// The page's chat (component/task_chat TaskChat.tasks).
const CHAT_TASK = PAGE.place ? "place" : "pick";
const task = { status: null, requesting: false, planShownFor: -1, alignShownFor: -1, scrolledTo: null };

// The segmented tabs (the keyboard page's): the chosen option a raised white segment on the grey track; the place
// ones in orange, the colour of the place on the head image.
const SEG_ON = "border-gray-200 bg-white font-semibold text-blue-600 shadow-sm";
const SEG_OFF = "border-transparent text-gray-600 hover:bg-gray-200";
const PLACE_ON = "border-gray-200 bg-white font-semibold text-orange-600 shadow-sm";
const STEP_LOOK = { done: "border-emerald-200 bg-emerald-50", active: "border-blue-600 bg-blue-50 ring-1 ring-blue-600", todo: "border-gray-200 bg-gray-50" };
const STEP_MARK = { done: "bg-emerald-600", active: "bg-blue-600", todo: "bg-gray-300" };
const STEP_TEXT = { hint: "font-semibold text-blue-700", todo: "text-gray-400", done: "text-gray-700" };

const byId = (id) => document.getElementById(id);
const escapeText = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// One look of a set: the element's classes of the others taken off, this one's put on.
function setLook(element, looks, look) {
  element.className = element.className.replace(new RegExp(Object.values(looks).join("|"), "g"), "").trim() + " " + looks[look];
}

// --- the task's state ---

// The pick-place page runs one of two tasks: into the box, or at a clicked desk point (status.place_kind).
const PAGE_ACTIONS = PAGE.place ? ["place_in_box", "place_at_spot"] : [PAGE.action];
const ours = (status) => PAGE_ACTIONS.includes(status.action);
const placeKind = () => (task.status && task.status.place_kind) || null;
const currentAction = () => (PAGE.place ? (placeKind() === "spot" ? "place_at_spot" : "place_in_box") : PAGE.action);
const placeChosen = (status) => (status.place_kind === "box" && Boolean(status.place_box)) || (status.place_kind === "spot" && Boolean(status.place_spot));
const targetChosen = (status) => Boolean(status.target && status.target.label && !status.target.default);
const busy = (status) => status.running || status.auto;
// Nothing of this page's task done yet (or it is done, or failed): a start begins from step 0.
const fresh = (status) => !ours(status) || status.next === 0 || status.next >= status.stages.length || status.failed;
// 任务步骤 (10-10 / 10-11): 1 the object, 2 where it goes (抓取摆放), in that order; null once all are chosen. A click
// on the head image sets the step it returns.
const activeStep = (status) => (!status || !targetChosen(status) ? 1 : PAGE.place && !placeChosen(status) ? 2 : null);
const stepMode = () => byId("step-mode").checked;
const spotText = (spot) => `桌面点 (${spot.xy[0].toFixed(2)}, ${spot.xy[1].toFixed(2)}) m`;

// --- rendering ---

function renderProgress(status) {
  const times = status.stage_seconds || {};
  byId("steps").innerHTML = status.stages.map((stage, index) => {
    const done = index < status.next;
    const current = index === status.next;
    const failed = current && status.failed;
    const running = current && busy(status);
    const dot = done ? "bg-emerald-600 border-emerald-600" : failed ? "bg-red-600 border-red-600"
      : running ? "bg-blue-600 border-blue-600 animate-pulse" : current ? "bg-white border-blue-600" : "bg-white border-gray-300";
    const row = current ? (failed ? "bg-red-50" : "bg-blue-50") : "";
    const text = done || current ? "text-gray-800" : "text-gray-400";
    const time = times[stage.name] != null ? `<span class="ms-auto font-mono text-[11px] text-gray-400">${times[stage.name]} s</span>` : "";
    return `<li class="flex items-center gap-x-2.5 rounded-lg px-2 py-1.5 ${row}"${current ? " data-current" : ""}>
      <span class="size-3 shrink-0 rounded-full border-2 ${dot}"></span>
      <span class="text-[13px] ${text} ${current ? "font-semibold" : ""}">${stage.label}</span>${time}</li>`;
  }).join("");
  // The list scrolls inside its card when the window is short: keep the current step in view as it moves on.
  if (task.scrolledTo !== status.next) {
    task.scrolledTo = status.next;
    byId("steps").querySelector("[data-current]")?.scrollIntoView({ block: "nearest" });
  }
}

function renderControls(status) {
  const mine = ours(status);
  const finished = status.next >= status.stages.length;
  const next = status.stages[status.next];
  const blocked = busy(status) || task.requesting;
  const [text, classes] = !mine ? [`后台在「${ACTION_NAMES[status.action] || status.action}」`, "bg-yellow-100 text-yellow-800"]
    : status.auto ? ["完整运行中", "bg-blue-100 text-blue-800"] : status.running ? ["执行中", "bg-blue-100 text-blue-800"]
    : status.failed ? ["失败，需重置", "bg-red-100 text-red-700"] : finished ? ["已完成", "bg-emerald-100 text-emerald-800"]
    : status.next === 0 ? ["待开始", "bg-gray-100 text-gray-600"] : [`第 ${status.next + 1} 步待执行`, "bg-yellow-100 text-yellow-800"];
  setBadge("task-state", text, classes);
  const start = byId("btn-start");
  const anew = fresh(status);
  if (stepMode()) start.textContent = anew ? `单步：${START_LABEL}` : `执行下一步：${next.label}`;
  else start.textContent = anew ? START_LABEL : `继续：剩下 ${status.stages.length - status.next} 步`;
  // Starts once the steps are chosen.
  start.disabled = blocked || confirmOpen() || (anew && activeStep(status) !== null);
  const nextButton = byId("btn-next");
  // style, not the hidden class: the button's inline-flex would win over it.
  nextButton.style.display = !stepMode() || anew ? "none" : "";
  if (!anew) nextButton.textContent = `下一步：${next.label}`;
  nextButton.disabled = blocked;
  byId("btn-reset").disabled = busy(status);
  byId("btn-target-clear").disabled = blocked;
  document.querySelectorAll("#arm-choice button[data-arm]").forEach((button) => {
    // 抓取摆放 places with the right arm only.
    setLook(button, { on: SEG_ON, off: SEG_OFF }, button.dataset.arm === (PAGE.place ? "right" : status.arm_override) ? "on" : "off");
    if (!(PAGE.place && button.dataset.arm === "left")) button.disabled = blocked || (PAGE.place && button.dataset.arm === "auto");
  });
}

// One 任务步骤 row: framed by its state, its mark (✓ or the number), what is chosen or what to do, 重新选 once chosen.
function renderStep(n, state, html, textLook) {
  setLook(byId(`step-${n}`), STEP_LOOK, state);
  const mark = byId(`step-${n}-mark`);
  setLook(mark, STEP_MARK, state);
  mark.textContent = state === "done" ? "✓" : String(n);
  const text = byId(`step-${n}-text`);
  text.className = `min-w-0 flex-1 truncate text-xs ${STEP_TEXT[textLook]}`;
  text.innerHTML = html;
  // style, not the hidden class: the button's inline-flex would win over it.
  byId(`step-${n}-redo`).style.display = state === "done" ? "" : "none";
}

// ① the object: the arm that takes it (抓取摆放 the right one; 物体抓取 the chosen one, else the nearer).
function targetText(status) {
  const target = status.target;
  const arms = (target.reach && target.reach.arms) || [];
  const arm = PAGE.place ? "right" : status.arm_override !== "auto" ? status.arm_override : arms[0];
  const reach = !target.reach ? "" : !arm ? " · 两只手都够不着" : ` · ${ARM_SAY[arm]}${arms.includes(arm) ? "够得到" : "够不着"}`;
  const html = `<span class="text-fuchsia-600">■</span> ${escapeText(sayName(target.label))}（紫框）${reach}`;
  return html;
}

// 任务步骤 and the head image's caption, from what the backend holds.
function renderTaskSteps(status) {
  const step = activeStep(status);
  const blocked = busy(status) || task.requesting;
  if (targetChosen(status)) renderStep(1, "done", targetText(status), "done");
  else renderStep(1, "active", "在左边画面里点要抓的物体，或者在对话里说", "hint");
  byId("step-1-redo").disabled = blocked;
  if (!PAGE.place) {
    byId("head-caption").textContent = step === 1 ? "头部 RGB · 第 1 步：点要抓的物体" : "头部 RGB · 物体选好了";
    return;
  }
  // ② where it goes: 桌面点 | 盒子, then where exactly (a click), in order once the object is chosen.
  const kind = status.place_kind;
  const ask = { null: "先选：放进盒子，还是放到桌面点", box: "在画面里点盒子里要放的位置", spot: "在画面里点桌面上要放的位置" }[kind];
  if (placeChosen(status)) renderStep(2, "done", `<span class="text-orange-600">■</span> ${kind === "box" ? "盒子" : spotText(status.place_spot)}`, "done");
  else if (step === 2) renderStep(2, "active", ask, "hint");
  else renderStep(2, "todo", "选好物体后再选", "todo");
  setLook(byId("place-spot"), { on: PLACE_ON, off: SEG_OFF }, kind === "spot" ? "on" : "off");
  setLook(byId("place-box"), { on: PLACE_ON, off: SEG_OFF }, kind === "box" ? "on" : "off");
  ["place-spot", "place-box"].forEach((id) => { byId(id).disabled = blocked || !targetChosen(status); });
  byId("step-2-redo").disabled = blocked;
  const caption = { null: "第 2 步：先选盒子还是桌面点", box: "第 2 步：点盒子里要放的位置", spot: "第 2 步：点桌面上要放的位置" }[kind];
  byId("head-caption").textContent = `头部 RGB · ${step === 1 ? "第 1 步：点要抓的物体" : step === 2 ? caption : "两步都选好了"}`;
}

// The arm doing this task (once planning chose it): its wrist camera framed in blue, with the alignment's last offset.
function renderArm(status) {
  const active = ours(status) && status.next >= 2 ? status.arm : null;
  for (const side of ["left", "right"]) {
    const on = active === side;
    byId(`tile-${side}_wrist`).classList.toggle("ring-2", on);
    byId(`tile-${side}_wrist`).classList.toggle("ring-blue-600", on);
    byId(`${side}_wrist-active`).classList.toggle("hidden", !on);
    const align = on && status.align;
    byId(`${side}_wrist-note`).textContent = align ? `离定指 ${align.gap_cm >= 0 ? "+" : ""}${align.gap_cm} cm · 还要左移 ${align.shift_cm} cm` : "";
    byId(`${side}_wrist-note`).classList.toggle("text-orange-600", Boolean(align));
  }
}

// The progress lines without their clock.
const progressLines = (status) => status.progress.map((line) => line.replace(/^\d\d:\d\d:\d\d /, ""));

// Why the last step failed: the line before 「失败：」 (not a 开始 / 完成 one), without its bracketed details.
function failureReason(status) {
  const lines = progressLines(status);
  const at = lines.map((line) => line.startsWith("失败：")).lastIndexOf(true);
  const reason = lines.slice(0, at).reverse().find((line) => !/^开始：|^完成：/.test(line)) || "";
  return reason.replace(/（[^）]*）/g, "");
}

// The last result and what needs attention.
function renderCards(status) {
  const lines = progressLines(status);
  const result = [...lines].reverse().find((line) => /瓶子立着|完整运行结束|已放进/.test(line));
  byId("result-card").classList.toggle("hidden", !result || status.failed);
  if (result) byId("result-text").textContent = result;
  let problem = null;
  if (status.failed) {
    const failed = [...lines].reverse().find((line) => line.startsWith("失败：")) || "";
    const reason = failureReason(status);
    problem = `${reason}${reason ? "；" : ""}${failed.replace(/（.*$/, "")}。机器人停在原地，重置后从头开始。`;
  } else if (status.too_left && ours(status)) {
    problem = "瓶子太靠左，机器人没动。";
  }
  byId("problem-card").classList.toggle("hidden", !problem);
  if (problem) byId("problem-text").textContent = problem;
}

function renderDebug(status) {
  const list = byId("progress");
  const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 4;
  list.innerHTML = status.progress.map((line) => `<li${/失败|出错|没跟上|停止|桌面守卫/.test(line) ? ' class="text-red-700"' : ""}>${escapeText(line)}</li>`).join("");
  if (atBottom) list.scrollTop = list.scrollHeight;
  for (const [name, available, key] of [["plan", status.plan_image, "planShownFor"], ["align", status.align_image, "alignShownFor"]]) {
    byId(`${name}-image`).classList.toggle("hidden", !available);
    byId(`${name}-empty`).classList.toggle("hidden", available);
    if (available && task[key] !== status.progress.length) {
      task[key] = status.progress.length;
      byId(`${name}-image`).src = `${GRASP_API}/api/real/${name}.jpg?t=${Date.now()}`;
    }
  }
}

function renderJoints(state) {
  const rows = [];
  for (const [part, joints] of Object.entries(state.joints)) {
    for (const [joint, [present, target]] of Object.entries(joints)) {
      rows.push(`<tr><td class="py-1 pe-3 text-gray-500">${App.config.parts[part] || part}</td><td class="pe-3">${joint}</td>
        <td class="pe-3 text-end">${present.toFixed(1)}</td><td class="text-end text-gray-400">${target == null ? "—" : target.toFixed(1)}</td></tr>`);
    }
  }
  byId("joints").innerHTML = rows.join("");
}

async function poll() {
  const response = await fetch(`${GRASP_API}/api/real/status`).catch(() => null);
  if (!response || !response.ok) {
    setBadge("task-state", "抓取服务未启动", "bg-red-100 text-red-700");
    ["btn-start", "btn-next", "btn-reset"].forEach((id) => { byId(id).disabled = true; });
    return;
  }
  const status = await response.json();
  task.status = status;
  renderProgress(status);
  renderTaskSteps(status);
  renderControls(status);
  renderArm(status);
  renderCards(status);
  renderDebug(status);
  narrate(status);
}

// A request that changes the task: the controls are off while it goes, and the page shows its outcome at once.
async function request(url, body) {
  task.requesting = true;
  poll();
  try {
    const result = await api("POST", url, body);
    return result;
  } finally {
    task.requesting = false;
    poll();
  }
}

// --- starting ---

async function chooseArm(arm) {
  try {
    await request(`${GRASP_API}/api/real/arm`, { arm });
    notify("");
  } catch (error) {
    notify(`没有改手臂：${error.message}`);
  }
}

async function resetTask(message = "") {
  try {
    await request(`${GRASP_API}/api/real/reset`, { action: currentAction() });
    notify(message);
  } catch (error) {
    notify(`没有重置：${error.message}`);
  }
}

async function runStep() {
  const status = task.status;
  const stage = status.stages[status.next];
  // Planning only looks; every other step moves the robot.
  if (stage.name !== "plan" && !confirm(`真机执行：${stage.label}。确认周围安全？（停止 / Esc / 空格 随时可停）`)) return;
  try {
    const result = await request(`${GRASP_API}/api/real/stage`, { name: stage.name });
    notify(result.done ? "" : `「${stage.label}」没有完成：看任务进度和调试详情`);
  } catch (error) {
    notify(`「${stage.label}」没有执行：${error.message}`);
  }
}

// The whole task from step 0, or the rest of it; said in the chat when asked there, else on the page.
async function runTask(say = notify) {
  try {
    if (fresh(task.status)) await request(`${GRASP_API}/api/real/action`, { action: currentAction() });
    else await request(`${GRASP_API}/api/real/run_all`);
    notify("");
  } catch (error) {
    say(`没有开始：${error.message}`);
  }
}

// The start button: the steps chosen first (else the chat asks for what is missing), then 执行前确认; one step at a time
// in 单步; the rest of a task left half way after a confirm.
async function start() {
  const status = task.status;
  if (fresh(status) && activeStep(status) !== null) {
    notify(PAGE.place ? "先按顺序选好两步（抓取物体、放置位置），再开始。" : "先选好要抓的物体，再开始。");
    await chatNext(chatAdd("robot", "…", { pending: true }));
    return;
  }
  if (stepMode()) {
    if (fresh(status)) await resetTask(`已重置为「${PAGE.title}」：点「下一步」一步一步执行。`);
    else await runStep();
    return;
  }
  if (fresh(status)) {
    showConfirm();
    return;
  }
  if (!confirm(`从「${status.stages[status.next].label}」开始自动执行剩下的 ${status.stages.length - status.next} 步，最后关力矩。开始？`)) return;
  await runTask();
}

// 任务 · 执行前确认 (Penpot 「抓取摆放 / 电脑 · 执行前确认（弹窗）」): what, where, which arm, then the run; a dialog over the
// page, so its buttons are always there to press.
function showConfirm() {
  const status = task.status;
  const target = status.target;
  const name = `「${sayName(target.label)}」`;
  const what = !PAGE.place ? `抓起${name}，再放回原处` : status.place_kind === "spot" ? `把${name}夹起来放到点选的桌面位置` : `把${name}夹起来放进盒子`;
  const place = !PAGE.place ? "放回原处" : status.place_kind === "spot" ? spotText(status.place_spot) : "盒子";
  const nearer = target.reach && target.reach.arms && target.reach.arms[0];
  const arm = PAGE.place ? "右手（摆放只用右手）" : status.arm_override === "auto" ? `自动（离哪边肩膀近）${nearer ? ` → ${ARM_NAMES[nearer]}` : ""}` : ARM_NAMES[status.arm_override];
  const rows = [["任务", what], ["抓取物体", `${sayName(target.label)}（紫框）`], ["放置", place], ["手臂", arm],
    ["方式", "一次跑完全部步骤，最后关力矩（停在抓取位）；任何一步失败就停在原地"]];
  byId("confirm-list").innerHTML = rows.map(([key, value]) => `<div class="flex gap-2"><dt class="w-16 shrink-0 text-gray-500">${key}</dt><dd class="font-medium text-gray-800">${escapeText(value)}</dd></div>`).join("");
  byId("confirm-card").style.display = "flex";
  byId("btn-start").disabled = true;
}

// style, not the hidden class: the dialog's flex would win over it.
const confirmOpen = () => byId("confirm-card").style.display !== "none";

function hideConfirm() {
  byId("confirm-card").style.display = "none";
  if (task.status) renderControls(task.status);
}

async function confirmGo() {
  hideConfirm();
  await runTask();
}

// A switch remembered on this browser (a private window has no storage: off).
function remembered(key) {
  try { return localStorage.getItem(key) === "1"; } catch { return false; }
}

function remember(key, on) {
  try { localStorage.setItem(key, on ? "1" : "0"); } catch { /* private window */ }
}

// After every deferred script (task_chat.js and task_voice.js come after this one).
document.addEventListener("DOMContentLoaded", () => App.ready.then(() => {
  live("head-image", `${GRASP_API}/api/depth/${PAGE.place ? "place" : "target"}.jpg`, "head-empty");
  live("depth-image", `${GRASP_API}/api/depth/refined.jpg`, null, depthNote);
  for (const side of ["left", "right"]) byId(`${side}_wrist-image`).src = `${ROBOT_API}/stream/${side}_wrist`;
  initChat();
  initVoice();
  document.querySelectorAll("#arm-choice button[data-arm]").forEach((button) => button.addEventListener("click", () => chooseArm(button.dataset.arm)));
  byId("step-mode").checked = remembered("xlerobot.step_mode");
  byId("step-mode").addEventListener("change", () => {
    remember("xlerobot.step_mode", stepMode());
    if (task.status) renderControls(task.status);
  });
  byId("btn-start").addEventListener("click", start);
  byId("btn-confirm-back").addEventListener("click", hideConfirm);
  byId("btn-confirm-go").addEventListener("click", confirmGo);
  byId("btn-new-task").addEventListener("click", () => resetTask(`新任务：选好物体后点「${START_LABEL}」。`));
  byId("btn-next").addEventListener("click", runStep);
  byId("btn-reset").addEventListener("click", () => resetTask());
  App.stateListeners.push(renderJoints);
  poll();
  setInterval(poll, 500);
}));
