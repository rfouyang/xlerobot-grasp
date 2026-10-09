// Shared by both pages: the robot state over the WebSocket, the header's safety controls (one torque switch, rest
// pose, grasp start pose, stop) and the stop keys. Every request goes to this server, which forwards it:
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

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  // A server error must not leave a switch disabled: it comes back as { error }.
  const result = response.ok ? await response.json() : { error: `${response.status} ${await response.text()}` };
  return result;
}

function notify(text) {
  const alert = document.getElementById("action-alert");
  alert.textContent = text;
  alert.classList.toggle("hidden", !text);
}

function setBadge(id, text, classes) {
  const element = document.getElementById(id);
  element.textContent = text;
  element.className = `${BADGE} ${classes}`;
}

const awayText = (away) => Object.entries(away).map(([part, joints]) =>
  `${App.config.parts[part]}：` + Object.entries(joints).map(([joint, delta]) => `${joint} ${delta > 0 ? "+" : ""}${delta}°`).join("，")).join("；");

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
  const result = await post(`${ROBOT_API}/api/torque`, { part: "all", on });
  App.torqueBusy = false;
  input.disabled = false;
  if (result.error) notify(`力矩${on ? "打开" : "关闭"}失败：${result.error}`);
  else if (on && result.away_from_rest) notify(`没有打开力矩：要从休息位或抓取位开始，先用手把这些关节摆回去——${awayText(result.away_from_rest)}`);
  else if (!on && result.cancelled && result.cancelled.length) notify(`回停靠位被停止了：${result.cancelled.map((part) => App.config.parts[part]).join("、")}的力矩保持开着。`);
  else notify("");
}

async function onPose(name, label) {
  if (!App.latest || !Object.entries(App.latest.torque).some(([part, on]) => on && part !== "base")) {
    notify("先打开力矩（机器人在休息位或抓取位上），再去" + label + "。");
    return;
  }
  if (!confirm(`手臂和头会以 20°/s 走到${label}。开始？`)) return;
  const result = await post(`${ROBOT_API}/api/pose`, { name });
  notify(result.error ? `去${label}失败：${result.error}` : "");
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
    setBadge("link-badge", "已连接", "bg-teal-100 text-teal-800");
    sendKeys();
  };
  App.socket.onmessage = (event) => {
    const state = JSON.parse(event.data);
    renderHeader(state);
    App.stateListeners.forEach((listener) => listener(state));
  };
  App.socket.onclose = () => {
    setBadge("link-badge", "重连中", "bg-red-100 text-red-700");
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
  document.getElementById("focus-alert").classList.toggle("hidden", !show);
}

document.addEventListener("keydown", (event) => {
  if (!App.mappedCodes.has(event.code)) return;
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
    setBadge("link-badge", "未启动", "bg-red-100 text-red-700");
    await new Promise((resolve) => setTimeout(resolve, 2000));
    response = await fetch(`${ROBOT_API}/api/config`);
  }
  App.config = await response.json();
  App.uiStartedAt = (await (await fetch("/ui-api/info")).json()).started_at;
  App.mappedCodes = new Set(App.config.keymap.stop.map((key) => key.code));
  document.getElementById("robot-name").textContent = App.config.robot;
  document.getElementById("torque-all").addEventListener("change", onTorque);
  document.getElementById("btn-stop").addEventListener("click", () => post(`${ROBOT_API}/api/stop`));
  document.getElementById("btn-rest").addEventListener("click", () => onPose("rest", "休息位"));
  document.getElementById("btn-grasp-start").addEventListener("click", () => onPose("grasp_start", "抓取位"));
  connect();
  // Heartbeat: the backend's watchdog releases the keys if these stop arriving.
  setInterval(sendKeys, App.config.heartbeat_ms);
  setFocusAlert(!document.hasFocus());
})();
