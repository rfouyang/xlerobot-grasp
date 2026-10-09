// The two task pages (物体抓取: pick_target, 抓取摆放: place_in_box). The backend runs one task at a time: this page
// shows whatever it holds and takes it over (reset to its own task) when asked to start. Polled through /grasp-api.
"use strict";

const PAGE = window.TASK_PAGE;
const ACTION_NAMES = { grasp: "抓取（旧）", move_right: "瓶子右移", place_in_box: "抓取摆放", pick_target: "物体抓取" };
const ARM_NAMES = { auto: "自动", left: "左手", right: "右手" };
const task = { status: null, requesting: false, planShownFor: -1, alignShownFor: -1, placeMode: "box", clickFor: "object", placeModeSet: false };
const PLACE_ON = "border-orange-500 bg-orange-50 font-semibold text-orange-600";
const PLACE_OFF = "border-gray-200 bg-white text-gray-800";
const SEG_ON = "border-blue-600 bg-blue-50 font-semibold text-blue-600";
const SEG_OFF = "border-gray-200 bg-white text-gray-800";

const byId = (id) => document.getElementById(id);
// The pick-place page runs one of two tasks: into the box, or at a clicked desk point.
const PAGE_ACTIONS = PAGE.place ? ["place_in_box", "place_at_spot"] : [PAGE.action];
const ours = (status) => PAGE_ACTIONS.includes(status.action);
const currentAction = () => (PAGE.place ? (task.placeMode === "spot" ? "place_at_spot" : "place_in_box") : PAGE.action);
const busy = (status) => status.running || status.auto;
const stepMode = () => byId("step-mode").checked;

// --- pictures ---

// The next picture is asked for once the last one has arrived, so a slow backend only slows the view down; asking is
// also what keeps the backend's preview of that kind running.
function live(imageId, url, emptyId) {
  const image = byId(imageId);
  const next = (delay) => setTimeout(() => { image.src = `${url}?t=${Date.now()}`; }, delay);
  image.addEventListener("load", () => {
    image.classList.remove("hidden");
    if (emptyId) byId(emptyId).classList.add("hidden");
    next(80);
  });
  image.addEventListener("error", () => next(1000));
  next(0);
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

// --- rendering ---

function renderSteps(status) {
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
    return `<li class="flex items-center gap-x-2.5 rounded-lg px-2 py-1.5 ${row}">
      <span class="size-3 shrink-0 rounded-full border-2 ${dot}"></span>
      <span class="text-[13px] ${text} ${current ? "font-semibold" : ""}">${stage.label}</span>${time}</li>`;
  }).join("");
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
  const fresh = !mine || status.next === 0 || finished || status.failed;
  if (stepMode()) start.textContent = fresh ? `单步：从头开始${PAGE.title}` : `执行下一步：${next.label}`;
  else start.textContent = fresh ? `确认并开始${PAGE.title}` : `继续：剩下 ${status.stages.length - status.next} 步`;
  start.disabled = blocked;
  const nextButton = byId("btn-next");
  // style, not the hidden class: the button's inline-flex would win over it.
  nextButton.style.display = !stepMode() || fresh ? "none" : "";
  if (!fresh) nextButton.textContent = `下一步：${next.label}`;
  nextButton.disabled = blocked;
  byId("btn-reset").disabled = busy(status);
  ["target-text", "btn-target-send", "btn-target-clear"].forEach((id) => { byId(id).disabled = blocked; });
  document.querySelectorAll("#arm-choice button[data-arm]").forEach((button) => {
    const on = button.dataset.arm === (PAGE.place ? "right" : status.arm_override);
    button.className = button.className.replace(new RegExp(`${SEG_ON}|${SEG_OFF}`, "g"), "").trim() + " " + (on ? SEG_ON : SEG_OFF);
    if (!(PAGE.place && button.dataset.arm === "left")) button.disabled = blocked || (PAGE.place && button.dataset.arm === "auto");
  });
}

function swap(element, on, onClasses, offClasses) {
  element.className = element.className.replace(new RegExp(`${onClasses}|${offClasses}`, "g"), "").trim() + " " + (on ? onClasses : offClasses);
}

function renderPlace(status) {
  if (!PAGE.place) return;
  if (!task.placeModeSet && ours(status)) task.placeMode = status.action === "place_at_spot" ? "spot" : "box";
  swap(byId("place-spot"), task.placeMode === "spot", PLACE_ON, PLACE_OFF);
  swap(byId("place-box"), task.placeMode === "box", PLACE_ON, PLACE_OFF);
  byId("click-choice").classList.toggle("hidden", task.placeMode !== "spot");
  byId("click-choice").classList.toggle("flex", task.placeMode === "spot");
  swap(byId("click-object"), task.clickFor === "object", SEG_ON, SEG_OFF);
  swap(byId("click-place"), task.clickFor === "place", PLACE_ON, PLACE_OFF);
  ["place-spot", "place-box", "click-object", "click-place"].forEach((id) => { byId(id).disabled = busy(status) || task.requesting; });
}

function setPlaceMode(mode) {
  task.placeMode = mode;
  task.placeModeSet = true;
  task.clickFor = mode === "spot" && !(task.status && task.status.place_spot) ? "place" : "object";
  if (task.status) { renderPlace(task.status); renderControls(task.status); renderTarget(task.status); }
}

function renderTarget(status) {
  const target = status.target || {};
  const reach = target.reach ? ` · ${target.reach.text}` : "";
  const where = target.point ? "" : "（最近的）";
  const chosen = byId("target-chosen");
  const spot = status.place_spot;
  const place = !PAGE.place ? "" : task.placeMode === "box" ? " → 盒子" : spot ? ` → 桌面点 (${spot.xy[0].toFixed(2)}, ${spot.xy[1].toFixed(2)}) m` : " → 桌面点：请在画面里点放置位置";
  chosen.textContent = `已选择：${target.label || "—"}${where}${reach}${place}`;
  const bad = target.reach && !target.reach.ok;
  chosen.className = `rounded-lg px-3 py-1.5 text-sm font-semibold ${bad ? "bg-orange-50 text-orange-700" : "bg-emerald-50 text-emerald-700"}`;
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

// The last result and what needs attention, from the progress lines.
function renderCards(status) {
  const lines = status.progress.map((line) => line.replace(/^\d\d:\d\d:\d\d /, ""));
  const result = [...lines].reverse().find((line) => /瓶子立着|完整运行结束|已放进/.test(line));
  byId("result-card").classList.toggle("hidden", !result || status.failed);
  if (result) byId("result-text").textContent = result;
  let problem = null;
  if (status.failed) {
    const at = lines.map((line) => line.startsWith("失败：")).lastIndexOf(true);
    const reason = lines.slice(0, at).reverse().find((line) => !/^开始：|^完成：/.test(line));
    problem = `${reason || ""}${reason ? "；" : ""}${(lines[at] || "").replace(/（.*$/, "")}。机器人停在原地，重置后从头开始。`;
  } else if (status.too_left && ours(status)) {
    problem = "瓶子太靠左，机器人没动。";
  }
  byId("problem-card").classList.toggle("hidden", !problem);
  if (problem) byId("problem-text").textContent = problem;
}

function renderDebug(status) {
  const list = byId("progress");
  const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 4;
  list.innerHTML = status.progress.map((line) => `<li${/失败|出错|没跟上|停止|桌面守卫/.test(line) ? ' class="text-red-700"' : ""}>${line}</li>`).join("");
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
  renderSteps(status);
  renderPlace(status);
  renderControls(status);
  renderTarget(status);
  renderArm(status);
  renderCards(status);
  renderDebug(status);
}

// --- actions ---

async function request(url, body) {
  task.requesting = true;
  poll();
  const result = await post(url, body);
  task.requesting = false;
  poll();
  return result;
}

async function chooseTarget(body) {
  const result = await request(`${GRASP_API}/api/real/target`, body);
  notify(result.ok ? "" : `没有选上目标：${result.error || "看调试详情里的日志"}`);
}

// --- voice: record (16 kHz mono PCM) -> BytePlus ASR -> the text box; a Chinese instruction -> GPT -> target ---

const voice = { stream: null, context: null, processor: null, chunks: [], started: 0, timer: null, busy: false };
const VOICE_IDLE = "● 语音";

function voiceState(text, tone = "text-gray-500") {
  const line = byId("voice-state");
  line.textContent = text;
  line.className = `text-xs ${tone}${text ? "" : " hidden"}`;
}

async function startRecording() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    voiceState("无法使用麦克风：浏览器只在 https 或 localhost 下给麦克风（iPad 需要 HTTPS）。可以改用文字。", "text-orange-700");
    return;
  }
  try {
    voice.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch (error) {
    voiceState(`无法使用麦克风：${error.message}。请检查浏览器的麦克风权限，或改用文字。`, "text-orange-700");
    return;
  }
  voice.context = new AudioContext({ sampleRate: 16000 });
  const source = voice.context.createMediaStreamSource(voice.stream);
  voice.processor = voice.context.createScriptProcessor(4096, 1, 1);
  voice.chunks = [];
  voice.processor.onaudioprocess = (event) => voice.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  source.connect(voice.processor);
  voice.processor.connect(voice.context.destination);
  voice.started = Date.now();
  const button = byId("mode-voice");
  button.classList.add("border-red-600", "bg-red-600", "text-white");
  voice.timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - voice.started) / 1000);
    button.textContent = `■ 停止 ${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    if (seconds >= 30) stopRecording();
  }, 250);
  voiceState("正在录音：说完再点一下「停止」，录音期间不会执行任何动作。", "text-red-700");
}

// Float32 at the context's rate -> int16 at 16 kHz (most browsers give 16 kHz as asked; others are resampled here).
function pcm16(chunks, rate) {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const all = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) { all.set(chunk, offset); offset += chunk.length; }
  const ratio = rate / 16000;
  const out = new Int16Array(Math.floor(length / ratio));
  for (let i = 0; i < out.length; i++) {
    const sample = Math.max(-1, Math.min(1, all[Math.floor(i * ratio)]));
    out[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return out;
}

async function stopRecording() {
  clearInterval(voice.timer);
  const button = byId("mode-voice");
  button.classList.remove("border-red-600", "bg-red-600", "text-white");
  button.textContent = VOICE_IDLE;
  if (!voice.context) return;
  voice.processor.disconnect();
  voice.stream.getTracks().forEach((track) => track.stop());
  const rate = voice.context.sampleRate;
  await voice.context.close();
  voice.context = null;
  const pcm = pcm16(voice.chunks, rate);
  voice.busy = true;
  button.disabled = true;
  voiceState("正在将语音转成文字……", "text-blue-700");
  const response = await fetch("/api/voice/transcribe", { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: pcm.buffer }).catch(() => null);
  const result = response && response.ok ? await response.json() : { ok: false, error: "网页服务没响应" };
  voice.busy = false;
  button.disabled = false;
  if (result.ok) {
    byId("target-text").value = result.text;
    setMode("text");
    voiceState(`转写完成（${result.seconds} s）：可以先改一改，再点「发送」。`, "text-emerald-700");
  } else {
    voiceState(`${result.error || "没听清"}。可以再录一次，或改用文字。`, "text-orange-700");
  }
}

function toggleRecording() {
  if (voice.busy) return;
  if (voice.context) stopRecording();
  else startRecording();
}

function speak(text) {
  if (!text) return;
  fetch("/api/voice/say", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) })
    .then((response) => (response.ok ? response.blob() : null))
    .then((blob) => { if (blob) new Audio(URL.createObjectURL(blob)).play().catch(() => {}); })
    .catch(() => {});
}

// A typed or spoken instruction: Chinese (or any non-ASCII) goes through the dialog; a plain English name to YOLOE.
async function sendInstruction(text) {
  if (!/[^\x00-\x7f]/.test(text)) {
    await chooseTarget({ prompt: text });
    return;
  }
  voiceState("正在理解指令……", "text-blue-700");
  const result = await request("/api/voice/command", { text });
  const understood = result.understood || {};
  const where = { left: "左边", right: "右边" }[understood.side] || "最近的";
  const placeNote = understood.place === "box" && !PAGE.place ? "（要放进盒子请用「抓取摆放」页）" : "";
  voiceState(result.ok ? `理解为：${where}的「${understood.object}」${understood.by === "words" ? "（词表）" : ""}${placeNote}` : `${result.error || "没理解"}`,
    result.ok ? "text-emerald-700" : "text-orange-700");
  speak(result.reply);
}

async function chooseArm(arm) {
  const result = await request(`${GRASP_API}/api/real/arm`, { arm });
  notify(result.ok ? "" : `没有改手臂：${result.error}`);
}

async function runStep() {
  const status = task.status;
  const stage = status.stages[status.next];
  // Planning only looks; every other step moves the robot.
  if (stage.name !== "plan" && !confirm(`真机执行：${stage.label}。确认周围安全？（停止 / Esc / 空格 随时可停）`)) return;
  const result = await request(`${GRASP_API}/api/real/stage`, { name: stage.name });
  notify(result.ok ? "" : `「${stage.label}」没有完成：${result.error || "看任务进度和调试详情"}`);
}

async function start() {
  const status = task.status;
  const fresh = !ours(status) || status.next === 0 || status.next >= status.stages.length || status.failed;
  const target = status.target && status.target.label ? `「${status.target.label}」` : "最近的瓶子";
  const what = !PAGE.place ? `抓起${target}，再放回原处`
    : task.placeMode === "spot" ? `把${target}夹起来放到点选的桌面位置` : `把${target}夹起来放进盒子`;
  if (PAGE.place && task.placeMode === "spot" && !status.place_spot) {
    task.clickFor = "place";
    renderPlace(status);
    notify("先在头部画面里点一下桌面上要放的位置（橙色十字），再开始。");
    return;
  }
  if (stepMode()) {
    if (fresh) {
      await request(`${GRASP_API}/api/real/reset`, { action: currentAction() });
      notify(`已重置为「${PAGE.title}」：点「下一步」一步一步执行。`);
      return;
    }
    await runStep();
    return;
  }
  if (fresh) {
    if (!confirm(`从头开始：${what}，最后关力矩（停在抓取位）。任何一步失败就停在原地；停止 / Esc / 空格 随时可停。开始？`)) return;
    const result = await request(`${GRASP_API}/api/real/action`, { action: currentAction() });
    notify(result.ok ? "" : `没有开始：${result.error}`);
    return;
  }
  if (!confirm(`从「${status.stages[status.next].label}」开始自动执行剩下的 ${status.stages.length - status.next} 步，最后关力矩。开始？`)) return;
  const result = await request(`${GRASP_API}/api/real/run_all`);
  notify(result.ok ? "" : `没有开始：${result.error}`);
}

function setMode(mode) {
  byId("mode-click").className = byId("mode-click").className.replace(new RegExp(`${SEG_ON}|${SEG_OFF}`, "g"), "").trim() + " " + (mode === "click" ? SEG_ON : SEG_OFF);
  byId("mode-text").className = byId("mode-text").className.replace(new RegExp(`${SEG_ON}|${SEG_OFF}`, "g"), "").trim() + " " + (mode === "text" ? SEG_ON : SEG_OFF);
  if (mode === "text") byId("target-text").focus();
}

App.ready.then(() => {
  live("head-image", `${GRASP_API}/api/depth/${PAGE.place ? "place" : "target"}.jpg`, "head-empty");
  live("depth-image", `${GRASP_API}/api/depth/refined.jpg`);
  for (const side of ["left", "right"]) byId(`${side}_wrist-image`).src = `${ROBOT_API}/stream/${side}_wrist`;
  byId("head-image").addEventListener("click", async (event) => {
    const point = imagePoint(byId("head-image"), event);
    if (!point || task.requesting || (task.status && busy(task.status))) return;
    if (PAGE.place && task.placeMode === "spot" && task.clickFor === "place") {
      const result = await request(`${GRASP_API}/api/real/place_spot`, { point });
      notify(result.ok ? "" : `放置位置没选上：${result.error}`);
      if (result.ok) task.clickFor = "object";
      return;
    }
    chooseTarget({ point });
  });
  if (PAGE.place) {
    byId("place-spot").addEventListener("click", () => setPlaceMode("spot"));
    byId("place-box").addEventListener("click", () => setPlaceMode("box"));
    byId("click-object").addEventListener("click", () => { task.clickFor = "object"; renderPlace(task.status); });
    byId("click-place").addEventListener("click", () => { task.clickFor = "place"; renderPlace(task.status); });
  }
  byId("target-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = byId("target-text").value.trim();
    if (text) sendInstruction(text);
  });
  byId("mode-voice").addEventListener("click", toggleRecording);
  byId("btn-target-clear").addEventListener("click", () => chooseTarget({ clear: true }));
  byId("mode-click").addEventListener("click", () => setMode("click"));
  byId("mode-text").addEventListener("click", () => setMode("text"));
  document.querySelectorAll("#arm-choice button[data-arm]").forEach((button) => button.addEventListener("click", () => chooseArm(button.dataset.arm)));
  const remembered = (() => { try { return localStorage.getItem("xlerobot.step_mode") === "1"; } catch { return false; } })();
  byId("step-mode").checked = remembered;
  byId("step-mode").addEventListener("change", () => {
    try { localStorage.setItem("xlerobot.step_mode", stepMode() ? "1" : "0"); } catch { /* private window */ }
    if (task.status) renderControls(task.status);
  });
  byId("btn-start").addEventListener("click", start);
  byId("btn-next").addEventListener("click", runStep);
  byId("btn-reset").addEventListener("click", async () => { await request(`${GRASP_API}/api/real/reset`, { action: currentAction() }); notify(""); });
  App.stateListeners.push(renderJoints);
  poll();
  setInterval(poll, 500);
});
