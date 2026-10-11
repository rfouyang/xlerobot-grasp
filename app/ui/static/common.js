// Shared by every page: the robot state over the WebSocket, the top bar's safety controls (one torque switch, stop),
// the stop keys, and the keyboard page's rest / grasp start pose buttons. Every request goes to this server, which forwards it:
// /robot-api/* and /robot-ws/* to the motor backend, /grasp-api/* to the grasp backend.
"use strict";

const ROBOT_API = "/robot-api";
const GRASP_API = "/grasp-api";
const BADGE = "inline-flex items-center gap-x-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium";
const KEY_NAMES = {
  ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→", BracketLeft: "[", BracketRight: "]",
  Semicolon: ";", Minus: "-", Equal: "=", Escape: "Esc", Space: "空格",
};
// A detection stays drawn this long after the page first sees it (a grasp run goes on ~40 s past its detection).
const OVERLAY_KEEP_MS = 60000;
const keyName = (code) => KEY_NAMES[code] || code.replace(/^Key/, "").replace(/^Digit/, "");

const App = {
  config: null,
  uiStartedAt: null,
  latest: null,
  socket: null,
  keyboardKeys: new Set(),
  pointerKeys: new Set(),
  // The keys this page sends; the stop keys on every page, the keyboard page adds the rest.
  mappedCodes: new Set(),
  stateListeners: [],
  // A torque request is in flight (switching off walks to the rest pose first and takes a while).
  torqueBusy: false,
  overlayDrawnAt: -1,
  // When this page first saw the drawn detection (its own clock, not the backend's); null once cleared.
  overlaySeenAt: null,
};

const heldKeys = () => [...new Set([...App.keyboardKeys, ...App.pointerKeys])];

function sendKeys() {
  if (App.socket && App.socket.readyState === WebSocket.OPEN) App.socket.send(JSON.stringify({ keys: heldKeys() }));
}

function releaseAll() {
  App.keyboardKeys.clear();
  App.pointerKeys.clear();
  sendKeys();
}

// Every request of the pages (app/api/errors.py): the answer, or an Error with the server's own words in its message
// and the HTTP status (409 busy, 422 cannot be chosen or done, 404 not there, 503 no head camera picture). A body
// that is a Blob goes as it is.
function detailText(detail) {
  // FastAPI's validation errors come as a list; our own as one string.
  if (Array.isArray(detail)) return detail.map((item) => `${item.loc[item.loc.length - 1]}: ${item.msg}`).join("; ");
  return detail || "";
}

async function api(method, url, body) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body instanceof Blob) {
    options.body = body;
    options.headers["Content-Type"] = "application/octet-stream";
  } else if (body !== undefined) {
    options.body = JSON.stringify(body);
    options.headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, options).catch(() => null);
  if (!response) throw Object.assign(new Error("网页服务没响应"), { status: 0 });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw Object.assign(new Error(detailText(data.detail) || `请求失败（${response.status}）`), { status: response.status });
  }
  return data;
}

function notify(text) {
  const alert = document.getElementById("action-alert");
  alert.textContent = text;
  alert.classList.toggle("hidden", !text);
}

function setBadge(id, text, classes) {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = text;
  element.className = `${BADGE} ${classes}`;
}

// The robot process's link: the top bar's pill and the sidebar's row.
function setLink(text, classes) {
  setBadge("link-badge", text, classes);
  setBadge("robot-link-badge", text, classes);
}

const awayText = (away) => Object.entries(away).map(([part, joints]) =>
  `${App.config.parts[part]}：` + Object.entries(joints).map(([joint, delta]) => `${joint} ${delta > 0 ? "+" : ""}${delta}°`).join("，")).join("；");

// --- pictures ---

// A picture polled from the grasp side (the head depth, the head overlays): the next one is asked for once the last
// has arrived, so a slow backend only slows the view down; asking is also what keeps the backend's preview of that
// kind running. emptyId: a placeholder hidden once the first picture is in; onHeaders: gets each picture's response
// headers (the depth's X-Valid / X-Range).
function live(imageId, url, emptyId, onHeaders) {
  const image = document.getElementById(imageId);
  const next = (delay) => setTimeout(load, delay);
  async function load() {
    const response = await fetch(`${url}?t=${Date.now()}`).catch(() => null);
    if (!response || !response.ok) {
      next(1000);
      return;
    }
    const old = image.src;
    image.src = URL.createObjectURL(await response.blob());
    if (old.startsWith("blob:")) URL.revokeObjectURL(old);
    image.classList.remove("hidden");
    if (emptyId) document.getElementById(emptyId).classList.add("hidden");
    if (onHeaders) onHeaders(response.headers);
    next(80);
  }
  next(0);
}

// The caption of a depth picture: how much of it has a depth, and its colour scale (blue near, red far).
function depthNote(headers) {
  const note = document.getElementById("depth-note");
  const valid = headers.get("X-Valid");
  const range = headers.get("X-Range");
  if (!note || valid === null || !range) return;
  const [near, far] = range.split("-");
  note.textContent = `有效 ${Math.round(Number(valid) * 100)}% · 蓝 ${near} m → 红 ${far} m`;
}

// --- header actions ---

async function onTorque(event) {
  const input = event.target;
  const on = input.checked;
  if (!on && !confirm("手臂和头会先以 20°/s 走回最近的停靠位（休息位或抓取位），停稳后整机断力矩（底盘也断）。开始？")) {
    input.checked = true;
    return;
  }
  App.torqueBusy = true;
  input.disabled = true;
  notify(on ? "" : "正在回停靠位，到了之后断力矩……");
  // A server error must not leave the switch disabled: it is said like a refusal.
  const result = await api("POST", `${ROBOT_API}/api/torque`, { part: "all", on }).catch((error) => ({ error: error.message }));
  App.torqueBusy = false;
  input.disabled = false;
  if (result.error) notify(`力矩${on ? "打开" : "关闭"}失败：${result.error}`);
  else if (on && result.away_from_rest) notify(`没有打开力矩：要从休息位或抓取位开始，先用手把这些关节摆回去——${awayText(result.away_from_rest)}`);
  else if (!on && result.cancelled && result.cancelled.length) notify(`回停靠位被停止了：${result.cancelled.map((part) => App.config.parts[part]).join("、")}的力矩保持开着。`);
  else notify("");
}

// 回到停靠位并关闭力矩 (the keyboard and task pages' bottom bar): the top bar's torque switch turned off, which walks
// to the nearest dock first, then cuts the torque.
function onDockOff(event) {
  event.preventDefault();
  const input = document.getElementById("torque-all");
  if (!input.checked) {
    notify("力矩已经是关的。");
    return;
  }
  input.checked = false;
  input.dispatchEvent(new Event("change"));
}

async function onPose(name, label) {
  if (!App.latest || !Object.entries(App.latest.torque).some(([part, on]) => on && part !== "base")) {
    notify("先打开力矩（机器人在休息位或抓取位上），再去" + label + "。");
    return;
  }
  if (!confirm(`手臂和头会以 20°/s 走到${label}。开始？`)) return;
  try {
    await api("POST", `${ROBOT_API}/api/pose`, { name });
    notify("");
  } catch (error) {
    notify(`去${label}失败：${error.message}`);
  }
}

// --- the head camera's detection overlay (a canvas with id "overlay" over the head view) ---

function drawOverlay(overlay) {
  const canvas = document.getElementById("overlay");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  const info = document.getElementById("overlay-info");
  if (overlay.time === App.overlayDrawnAt) {
    if (App.overlaySeenAt !== null && Date.now() - App.overlaySeenAt > OVERLAY_KEEP_MS) {
      context.clearRect(0, 0, canvas.width, canvas.height);
      App.overlaySeenAt = null;
      if (info && overlay.items.length) info.textContent = `检测（${overlay.source}）${new Date(overlay.time * 1000).toLocaleTimeString()}：已过 ${OVERLAY_KEEP_MS / 1000} 秒，框已清掉`;
    }
    return;
  }
  App.overlayDrawnAt = overlay.time;
  App.overlaySeenAt = Date.now();
  context.clearRect(0, 0, canvas.width, canvas.height);
  for (const item of overlay.items) {
    const color = item.target ? "#22c55e" : "#94a3b8";
    if (item.polygon && item.polygon.length > 2) {
      context.beginPath();
      item.polygon.forEach(([x, y], index) => (index ? context.lineTo(x, y) : context.moveTo(x, y)));
      context.closePath();
      context.fillStyle = item.target ? "rgba(34,197,94,0.25)" : "rgba(148,163,184,0.2)";
      context.fill();
    }
    const [x1, y1, x2, y2] = item.box;
    context.lineWidth = item.target ? 3 : 2;
    context.strokeStyle = color;
    context.strokeRect(x1, y1, x2 - x1, y2 - y1);
    const text = `${item.target ? "目标 " : ""}${item.label} ${item.score.toFixed(2)}${item.distance_m ? ` ${item.distance_m.toFixed(2)} m` : ""}`;
    context.font = "16px sans-serif";
    const width = context.measureText(text).width + 8;
    context.fillStyle = color;
    context.fillRect(x1, Math.max(0, y1 - 22), width, 22);
    context.fillStyle = "#ffffff";
    context.fillText(text, x1 + 4, Math.max(16, y1 - 6));
  }
  if (info && overlay.items.length) info.textContent = `检测（${overlay.source}）${new Date(overlay.time * 1000).toLocaleTimeString()}：${overlay.items.length} 个`;
}

// A camera view; the head one carries the overlay canvas.
// fill: the figure fills its grid cell on large screens (the grasp page fits one screen); otherwise a 4:3 box. The
// overlay canvas is object-contain like the image, so the detections stay on it whatever the cell's shape.
function cameraFigure(name, label, fill = false) {
  return `
    <figure class="${fill ? "flex min-h-0 flex-col " : ""}overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xs">
      <div class="relative aspect-[4/3] bg-gray-900${fill ? " lg:aspect-auto lg:min-h-0 lg:flex-1" : ""}">
        <img src="${ROBOT_API}/stream/${name}" alt="${label}" class="absolute inset-0 h-full w-full object-contain" />
        ${name === "head" ? `<canvas id="overlay" width="640" height="480" class="pointer-events-none absolute inset-0 h-full w-full object-contain"></canvas>` : ""}
      </div>
      <figcaption class="flex items-center justify-between px-3 ${fill ? "py-1.5" : "py-2"} text-xs">
        <span class="font-medium text-gray-700">${label}</span><span id="cam-fps-${name}" class="font-mono text-gray-400"></span>
      </figcaption>
    </figure>`;
}

// --- live state ---

function renderHeader(state) {
  App.latest = state;
  setBadge("loop-badge", state.loop_alive ? `${(1 / state.dt).toFixed(0)} Hz` : "已停止",
    state.loop_alive ? "bg-gray-100 text-gray-600 font-mono" : "bg-red-100 text-red-700");
  document.getElementById("loop-alert").classList.toggle("hidden", state.loop_alive);
  const keys = document.getElementById("keys-badge");
  keys.textContent = `按键：${state.pressed.length ? state.pressed.map(keyName).join(" ") : "无"}`;
  keys.classList.toggle("bg-red-100", state.stopping);
  const powered = Object.values(state.torque);
  const allOn = powered.every(Boolean);
  const someOn = powered.some(Boolean);
  if (!App.torqueBusy) document.getElementById("torque-all").checked = someOn;
  document.getElementById("torque-all-label").textContent = allOn ? "力矩开" : someOn ? "部分有力矩" : "力矩关";
  for (const [name, frames] of Object.entries(state.camera_frames)) {
    const element = document.getElementById(`cam-fps-${name}`);
    if (element) element.textContent = `${frames} 帧`;
  }
  drawOverlay(state.overlay);
}

async function startedAt() {
  // Either service restarted (new code, fresh state): the page reloads rather than keep its own.
  const [robot, ui] = await Promise.all([fetch(`${ROBOT_API}/api/config`).then((r) => r.json()), fetch("/ui-api/info").then((r) => r.json())]);
  return [robot.started_at, ui.started_at];
}

function connect() {
  App.socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/robot-ws/teleop`);
  App.socket.onopen = async () => {
    const [robot, ui] = await startedAt();
    if (robot !== App.config.started_at || ui !== App.uiStartedAt) {
      location.reload();
      return;
    }
    setLink("● 已连接", "bg-teal-100 text-teal-800");
    sendKeys();
  };
  App.socket.onmessage = (event) => {
    const state = JSON.parse(event.data);
    renderHeader(state);
    App.stateListeners.forEach((listener) => listener(state));
  };
  App.socket.onclose = () => {
    setLink("重连中", "bg-red-100 text-red-700");
    setTimeout(connect, 1000);
  };
}

// The sidebar's grasp backend badge, on both pages (the grasp page also shows the steps from the same status).
async function pollGraspLink() {
  const response = await fetch(`${GRASP_API}/api/real/status`).catch(() => null);
  if (response && response.ok) setBadge("grasp-link-badge", "已连接", "bg-teal-100 text-teal-800");
  else setBadge("grasp-link-badge", "未启动", "bg-red-100 text-red-700");
}

// --- keyboard, focus ---

function setFocusAlert(show) {
  // An inline style: the badge's own display classes would otherwise beat "hidden".
  document.getElementById("focus-alert").style.display = show ? "inline-block" : "none";
}

// Typing in a box (a joint target, a task description) must not drive the robot: digits are the head's keys. Esc
// still stops.
const typing = (event) => event.target.closest?.("input, textarea, select, [contenteditable]") && event.code !== "Escape";

document.addEventListener("keydown", (event) => {
  if (!App.mappedCodes.has(event.code) || typing(event)) return;
  event.preventDefault();
  if (!App.keyboardKeys.has(event.code)) {
    App.keyboardKeys.add(event.code);
    sendKeys();
  }
});
document.addEventListener("keyup", (event) => {
  if (App.keyboardKeys.delete(event.code)) sendKeys();
});
window.addEventListener("blur", () => { releaseAll(); setFocusAlert(true); });
window.addEventListener("focus", () => setFocusAlert(false));
document.addEventListener("visibilitychange", () => { if (document.hidden) releaseAll(); });

// The page's own script waits for this: the robot's config is loaded and the header is wired.
App.ready = (async () => {
  pollGraspLink();
  setInterval(pollGraspLink, 3000);
  let response = await fetch(`${ROBOT_API}/api/config`);
  while (!response.ok) {
    // The motor backend is not up yet: keep trying, the page fills in when it is.
    setLink("未启动", "bg-red-100 text-red-700");
    await new Promise((resolve) => setTimeout(resolve, 2000));
    response = await fetch(`${ROBOT_API}/api/config`);
  }
  App.config = await response.json();
  App.uiStartedAt = (await (await fetch("/ui-api/info")).json()).started_at;
  App.mappedCodes = new Set(App.config.keymap.stop.map((key) => key.code));
  document.getElementById("robot-name").textContent = App.config.robot;
  document.getElementById("torque-all").addEventListener("change", onTorque);
  document.getElementById("btn-stop").addEventListener("click", () => api("POST", `${ROBOT_API}/api/stop`).catch((error) => notify(`没能停止：${error.message}`)));
  // The pose buttons are on the keyboard page only.
  document.getElementById("btn-rest")?.addEventListener("click", () => onPose("rest", "休息位"));
  document.getElementById("btn-grasp-start")?.addEventListener("click", () => onPose("grasp_start", "抓取位"));
  document.getElementById("btn-dock-off")?.addEventListener("click", onDockOff);
  connect();
  // Heartbeat: the backend's watchdog releases the keys if these stop arriving.
  setInterval(sendKeys, App.config.heartbeat_ms);
  setFocusAlert(!document.hasFocus());
})();
