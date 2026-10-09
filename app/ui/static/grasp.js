// Grasp, pick-place and interactive grasp pages: the real robot's steps (one at a time, in order, or all of them) next to five views
// (head RGB, the head depth live raw and refined by LingBot-Depth, the left and right wrist cameras) and the progress.
// The grasp backend runs one task at a time; each page has its own (window.GRASP_PAGE_ACTIONS, the first is what it
// starts) and offers to switch when the backend holds the other page's. Polled through /grasp-api.
"use strict";

const PAGE_ACTIONS = (window.GRASP_PAGE_ACTIONS || "grasp,move_right").split(",");
const PAGE_ACTION = PAGE_ACTIONS[0];
const ACTION_NAMES = { grasp: "抓取", move_right: "把瓶子往右移", place_in_box: "放进盒子", pick_target: "交互抓取" };
const grasp = { status: null, planShownFor: -1, alignShownFor: -1, requesting: false };
// An element this page may not have (the move-right button and the too-left notice are the grasp page's).
const optional = (id) => document.getElementById(id) || { classList: { toggle() {}, add() {}, remove() {} }, addEventListener() {}, set disabled(v) {}, set textContent(v) {} };
// The backend holds another page's task.
const foreign = (status) => !PAGE_ACTIONS.includes(status.action);

const STEP = "flex items-center gap-x-2 rounded-lg border px-3 py-1.5 text-xs";

function renderSteps(status) {
  const busy = status.running || status.auto;
  document.getElementById("steps").innerHTML = status.stages.map((stage, index) => {
    const done = index < status.next;
    const current = index === status.next;
    const look = done ? "border-teal-200 bg-teal-50 text-teal-800"
      : current && status.failed ? "border-red-200 bg-red-50 text-red-800"
      : current && busy ? "border-blue-200 bg-blue-50 text-blue-800"
      : current ? "border-blue-600 text-gray-800" : "border-gray-200 text-gray-400";
    const mark = done ? "✓" : current && status.failed ? "✕" : current && busy ? "…" : String(index + 1);
    return `<li class="${STEP} ${look}"><span class="w-4 text-center font-mono">${mark}</span>${stage.label}${current && busy ? "（执行中）" : ""}</li>`;
  }).join("");
  const next = status.stages[status.next];
  const finished = status.next >= status.stages.length;
  const button = document.getElementById("btn-next");
  if (foreign(status)) {
    button.textContent = `后台正在「${ACTION_NAMES[status.action] || status.action}」：切换到本页任务`;
    button.disabled = busy || grasp.requesting;
    document.getElementById("btn-run-all").disabled = busy || grasp.requesting;
  } else {
    button.textContent = finished ? "已全部完成" : `执行下一步：${next.label}`;
    button.disabled = busy || status.failed || finished || grasp.requesting;
    document.getElementById("btn-run-all").disabled = busy || status.failed || finished || grasp.requesting;
  }
  document.getElementById("btn-reset").disabled = busy;
  optional("btn-move-right").disabled = busy || grasp.requesting;
  ["target-text", "btn-target-text", "btn-target-clear"].forEach((id) => { optional(id).disabled = busy || grasp.requesting; });
  const [text, classes] = status.auto ? ["完整运行中", "bg-blue-100 text-blue-800"] : status.running ? ["执行中", "bg-blue-100 text-blue-800"]
    : status.failed ? ["失败，需重置", "bg-red-100 text-red-700"] : finished ? ["已完成", "bg-teal-100 text-teal-800"]
    : status.next === 0 ? ["待开始", "bg-gray-100 text-gray-600"] : [`第 ${status.next + 1} 步待执行`, "bg-yellow-100 text-yellow-800"];
  setBadge("grasp-state", text, classes);
}

// The grasp stopped because the bottle stands too far left: say so and offer the two choices.
function renderTooLeft(status) {
  const left = status.too_left;
  optional("too-left").classList.toggle("hidden", !left || status.running || status.auto);
  if (left) optional("too-left-text").textContent =
    `瓶子太靠左（y ${left.axis_y >= 0 ? "+" : ""}${left.axis_y.toFixed(3)} m），机器人没动。先把瓶子往右移 ${(left.shift_m * 100).toFixed(1)} cm，还是照常抓取？`;
}

// The interactive page's chosen target: its name, and where it was clicked (with the prompt-free model's score).
function renderTarget(status) {
  const label = document.getElementById("target-label");
  if (!label || !status.target) return;
  const target = status.target;
  const where = target.point ? ` @ (${target.point[0]}, ${target.point[1]})` : "（最近的）";
  label.textContent = `${target.label}${target.score != null ? ` ${target.score.toFixed(2)}` : ""}${where}`;
  // Whether the right arm reaches it where it stands, and if not which way to move it (from the click).
  const reach = optional("target-reach");
  reach.classList.toggle("hidden", !target.reach);
  if (target.reach) {
    reach.textContent = target.reach.text;
    reach.classList.toggle("text-red-700", !target.reach.ok);
    reach.classList.toggle("text-teal-700", target.reach.ok);
  }
}

function renderImages(status) {
  // Refreshed when the progress moves (each look during the alignment adds a line).
  const shown = [["plan", status.plan_image, "planShownFor"], ["align", status.align_image, "alignShownFor"]];
  for (const [name, available, key] of shown) {
    const image = document.getElementById(`${name}-image`);
    // The page shows only the views the user wants: a picture it has no panel for is skipped.
    if (!image) continue;
    image.classList.toggle("hidden", !available);
    document.getElementById(`${name}-empty`).classList.toggle("hidden", available);
    if (available && grasp[key] !== status.progress.length) {
      grasp[key] = status.progress.length;
      image.src = `${GRASP_API}/api/real/${name}.jpg?t=${Date.now()}`;
    }
  }
}

function renderProgress(status) {
  const list = document.getElementById("progress");
  const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 4;
  list.innerHTML = status.progress.map((line) => `<li${/失败|出错|没跟上|停止/.test(line) ? ' class="text-red-700"' : ""}>${line}</li>`).join("");
  if (atBottom) list.scrollTop = list.scrollHeight;
}

async function poll() {
  const response = await fetch(`${GRASP_API}/api/real/status`).catch(() => null);
  if (!response || !response.ok) {
    setBadge("grasp-state", "抓取后台未启动", "bg-red-100 text-red-700");
    ["btn-next", "btn-run-all", "btn-reset", "btn-move-right"].forEach((id) => { optional(id).disabled = true; });
    return;
  }
  const status = await response.json();
  grasp.status = status;
  renderSteps(status);
  renderTooLeft(status);
  renderTarget(status);
  renderImages(status);
  renderProgress(status);
}

async function runNext() {
  if (foreign(grasp.status)) {
    await reset();
    return;
  }
  const stage = grasp.status.stages[grasp.status.next];
  // Planning only looks; every other step moves the robot.
  if (stage.name !== "plan" && !confirm(`真机执行：${stage.label}。确认周围安全？（停止 / Esc / 空格 随时可停）`)) return;
  grasp.requesting = true;
  poll();
  const result = await post(`${GRASP_API}/api/real/stage`, { name: stage.name });
  grasp.requesting = false;
  notify(result.ok ? "" : `「${stage.label}」没有完成：${result.error || "看进度里的原因"}`);
  poll();
}

async function runAll() {
  const status = grasp.status;
  // Not this page's task, or not started yet: this page's task from the start.
  if (foreign(status) || status.next === 0 && status.action !== PAGE_ACTION) {
    await startAction(PAGE_ACTION, false);
    return;
  }
  const left = status.stages.length - status.next;
  const from = status.stages[status.next].label;
  if (!confirm(`从「${from}」开始自动执行剩下的 ${left} 步，最后关力矩（停在抓取位）。任何一步失败就停在原地；停止 / Esc / 空格 随时可停。开始？`)) return;
  const result = await post(`${GRASP_API}/api/real/run_all`);
  notify(result.ok ? "" : `没有开始：${result.error}`);
  poll();
}

// The live depth (raw / refined): the next picture is asked for once the last one has arrived, so a slow backend
// only slows the panel down; fetching is also what keeps the backend's preview running.
function liveDepth(kind) {
  const image = document.getElementById(`depth_${kind}-image`);
  const next = (delay) => setTimeout(() => { image.src = `${GRASP_API}/api/depth/${kind}.jpg?t=${Date.now()}`; }, delay);
  image.addEventListener("load", () => {
    image.classList.remove("hidden");
    document.getElementById(`depth_${kind}-empty`).classList.add("hidden");
    next(100);
  });
  image.addEventListener("error", () => next(1000));
  next(0);
}

// From the start: "move_right" grasps the bottle and puts it down further right; "grasp" with ignoreLeft grasps
// even though it stands too far left.
async function startAction(action, ignoreLeft) {
  const target = grasp.status && grasp.status.target ? grasp.status.target.label : "目标";
  const what = { move_right: "把瓶子往右移（抓起、在空中往右移、放下）", place_in_box: "把瓶子夹起来放进盒子",
    pick_target: `抓取选中的「${target}」（抓起、放回原处）`,
    grasp: ignoreLeft ? "照常抓取（不管瓶子是否太靠左）" : "抓取（抓起、放回原处）" }[action];
  if (!confirm(`从头开始：${what}，最后关力矩（停在抓取位）。任何一步失败就停在原地；停止 / Esc / 空格 随时可停。开始？`)) return;
  const result = await post(`${GRASP_API}/api/real/action`, { action, ignore_left: ignoreLeft });
  notify(result.ok ? "" : `没有开始：${result.error}`);
  poll();
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

// The interactive page's target: { point } (the backend names what is there), { prompt } or { clear: true }.
async function chooseTarget(body) {
  grasp.requesting = true;
  poll();
  const result = await post(`${GRASP_API}/api/real/target`, body);
  grasp.requesting = false;
  notify(result.ok ? "" : `没有选上目标：${result.error || "看进度"}`);
  poll();
}

function setupTarget() {
  const image = document.getElementById("depth_target-image");
  if (!image) return;
  image.addEventListener("click", (event) => {
    const point = imagePoint(image, event);
    if (point && !grasp.requesting && !(grasp.status && (grasp.status.running || grasp.status.auto))) chooseTarget({ point });
  });
  document.getElementById("target-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const prompt = document.getElementById("target-text").value.trim();
    if (prompt) chooseTarget({ prompt });
  });
  document.getElementById("btn-target-clear").addEventListener("click", () => chooseTarget({ clear: true }));
  liveDepth("target");
}

async function reset() {
  await post(`${GRASP_API}/api/real/reset`, { action: PAGE_ACTION });
  notify("");
  poll();
}

App.ready.then(() => {
  // Each camera into its slot of the picture grid (filling the cell); one the page has no slot for is left out.
  for (const [name, label] of Object.entries(App.config.cameras)) {
    const slot = document.getElementById(`cam-slot-${name}`);
    if (slot) slot.outerHTML = cameraFigure(name, label, true);
  }
  document.getElementById("btn-next").addEventListener("click", runNext);
  document.getElementById("btn-run-all").addEventListener("click", runAll);
  document.getElementById("btn-reset").addEventListener("click", reset);
  optional("btn-move-right").addEventListener("click", () => startAction("move_right", false));
  optional("btn-too-left-move").addEventListener("click", () => startAction("move_right", false));
  optional("btn-too-left-grasp").addEventListener("click", () => startAction("grasp", true));
  poll();
  setInterval(poll, 500);
  liveDepth("raw");
  liveDepth("refined");
  // The pick-place page's head view: the bottle and the box framed by the backend.
  if (document.getElementById("depth_detect-image")) liveDepth("detect");
  // The interactive page's head view: the target framed, and clicks pick it.
  setupTarget();
});
