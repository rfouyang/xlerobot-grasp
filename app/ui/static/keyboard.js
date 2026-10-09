// Keyboard control page: every part's state, the four cameras, the keymap (keyboard or held buttons), the joints and
// the rest pose. The header, the WebSocket and the keys themselves are common.js's.
"use strict";

const PART_GROUPS = { left_arm: "左臂", right_arm: "右臂", head: "头部", base: "底盘", stop: "停止" };

function buildOverview() {
  document.getElementById("overview").innerHTML = Object.entries(App.config.parts).map(([part, label]) => `
    <div class="flex flex-col rounded-xl border border-gray-200 bg-white shadow-2xs">
      <div class="flex grow flex-col gap-y-3 p-4 md:p-5">
        <div class="flex items-center justify-between gap-x-2">
          <p class="text-xs font-medium tracking-wide text-gray-500">${label}</p>
          <span id="rest-${part}" class="${BADGE} bg-gray-100 text-gray-600">…</span>
        </div>
        <p id="torque-label-${part}" class="text-xl font-semibold text-gray-800">力矩关</p>
        <p id="detail-${part}" class="min-h-4 font-mono text-xs text-gray-500"></p>
        ${part === "base" ? `<div id="base-levels" class="flex flex-wrap gap-1.5"></div>` : ""}
      </div>
    </div>`).join("");
  document.getElementById("base-levels").innerHTML = App.config.base_levels.map((level, index) => `
    <span data-level="${index}" class="rounded-md border border-gray-200 px-2 py-0.5 font-mono text-xs text-gray-500">${level.xy} m/s</span>`).join("");
}

function buildCameras() {
  document.getElementById("cameras").innerHTML = Object.entries(App.config.cameras).map(([name, label]) => cameraFigure(name, label)).join("");
}

function buildKeymap() {
  const chip = "inline-flex min-w-9 select-none items-center justify-center rounded-md border border-gray-300 bg-gray-50 px-1.5 py-1 font-mono text-xs font-semibold text-gray-700 shadow-[0_2px_0_0_rgba(0,0,0,0.08)] data-[held=true]:border-blue-600 data-[held=true]:bg-blue-600 data-[held=true]:text-white";
  document.getElementById("keymap").innerHTML = Object.entries(App.config.keymap).map(([part, keys]) => `
    <div>
      <h3 class="mb-2 text-xs font-medium tracking-wide text-gray-500">${PART_GROUPS[part]}</h3>
      <div class="grid grid-cols-2 gap-1.5">
        ${keys.map((key) => `
          <button type="button" data-code="${key.code}" class="flex items-center gap-x-2 rounded-lg px-1 py-0.5 text-start hover:bg-gray-50">
            <kbd data-chip="${key.code}" class="${chip}">${keyName(key.code)}</kbd>
            <span class="text-xs text-gray-600">${key.label}</span>
          </button>`).join("")}
      </div>
    </div>`).join("");
  // On this page every mapped key drives the robot.
  App.mappedCodes = new Set(Object.values(App.config.keymap).flat().map((key) => key.code));
  document.querySelectorAll("[data-code]").forEach((element) => {
    const code = element.dataset.code;
    const release = () => { if (App.pointerKeys.delete(code)) sendKeys(); };
    element.addEventListener("pointerdown", (event) => {
      element.setPointerCapture(event.pointerId);
      App.pointerKeys.add(code);
      sendKeys();
    });
    element.addEventListener("pointerup", release);
    element.addEventListener("pointercancel", release);
    element.addEventListener("lostpointercapture", release);
  });
}

function buildRestPose() {
  document.getElementById("rest-pose").innerHTML = Object.entries(App.config.rest_pose).map(([part, joints]) => `
    <div>
      <h3 class="mb-2 text-xs font-medium tracking-wide text-gray-500">${App.config.parts[part]}</h3>
      <dl class="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-xs">
        ${Object.entries(joints).map(([joint, value]) => `<dt class="text-gray-500">${joint}</dt><dd class="text-end text-gray-800">${value.toFixed(1)}</dd>`).join("")}
      </dl>
    </div>`).join("");
}

async function onRecordRest() {
  if (!confirm("把三个部件现在的关节位置记为新的休息位？以后每次上力矩都要从这里开始、断力矩前都回到这里。")) return;
  const result = await post(`${ROBOT_API}/api/rest_pose`);
  App.config.rest_pose = result.rest_pose;
  buildRestPose();
  notify("休息位已更新。");
}

function render(state) {
  const held = new Set(state.pressed);
  document.querySelectorAll("[data-chip]").forEach((chip) => { chip.dataset.held = held.has(chip.dataset.chip); });
  for (const [part, on] of Object.entries(state.torque)) {
    document.getElementById(`torque-label-${part}`).textContent = on ? "力矩开" : "力矩关";
  }
  for (const part of Object.keys(App.config.rest_pose)) {
    const away = state.away_from_rest[part];
    const worst = Math.max(0, ...Object.values(away).map(Math.abs));
    if (state.following.includes(part)) setBadge(`rest-${part}`, "跟随轨迹中", "bg-purple-100 text-purple-800");
    else if (state.parking.includes(part)) setBadge(`rest-${part}`, "回停靠位中", "bg-blue-100 text-blue-800");
    else if (state.moving[part]) setBadge(`rest-${part}`, "移动中", "bg-blue-100 text-blue-800");
    else if (!Object.keys(away).length) setBadge(`rest-${part}`, "在停靠位", "bg-teal-100 text-teal-800");
    else setBadge(`rest-${part}`, `离停靠位 ${worst.toFixed(0)}°`, "bg-yellow-100 text-yellow-800");
    document.getElementById(`detail-${part}`).textContent = Object.values(state.joints[part]).map(([present]) => present.toFixed(0)).join(" · ");
  }
  setBadge("rest-base", state.torque.base ? `档 ${state.base_level + 1}` : "—", "bg-gray-100 text-gray-600");
  document.querySelectorAll("[data-level]").forEach((element) => {
    const active = Number(element.dataset.level) === state.base_level;
    element.className = `rounded-md border px-2 py-0.5 font-mono text-xs ${active ? "border-blue-600 bg-blue-50 text-blue-700" : "border-gray-200 text-gray-500"}`;
  });
  const command = state.base_command;
  document.getElementById("detail-base").textContent =
    `x ${command["x.vel"].toFixed(2)} · y ${command["y.vel"].toFixed(2)} · θ ${command["theta.vel"].toFixed(0)}`;
  const rows = [];
  for (const [part, joints] of Object.entries(state.joints)) {
    rows.push(`<tr class="bg-gray-50/60"><td colspan="3" class="px-4 py-1.5 font-sans text-xs font-medium text-gray-600">${App.config.parts[part]}${state.torque[part] ? "" : "（无力矩，跟随）"}</td></tr>`);
    for (const [joint, [present, target]] of Object.entries(joints)) {
      const moving = state.torque[part] && Math.abs(present - target) > 1;
      rows.push(`<tr><td class="px-4 py-1 text-gray-600">${joint}</td><td class="px-4 py-1 text-end text-gray-800">${present.toFixed(1)}</td>
        <td class="px-4 py-1 text-end ${moving ? "text-blue-600" : "text-gray-400"}">${target.toFixed(1)}</td></tr>`);
    }
  }
  document.getElementById("joints").innerHTML = rows.join("");
}

App.ready.then(() => {
  buildOverview();
  buildCameras();
  buildKeymap();
  buildRestPose();
  document.getElementById("btn-record-rest").addEventListener("click", onRecordRest);
  App.stateListeners.push(render);
});
