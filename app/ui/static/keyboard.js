// Keyboard control page (Penpot page 08, 「键盘控制 / 电脑（修订）」): the cameras (in the template), 控制面板 (one part's
// keys at a time, held by touch or mouse; the keyboard drives every part whichever tab is shown), 运行状态, and the
// joints and rest pose folded under 调试详情. The top bar, the WebSocket and the keys themselves are common.js's.
"use strict";

const PART_TABS = { left_arm: "左臂", right_arm: "右臂", head: "头部", base: "底盘" };
const LEVEL_NAMES = ["低速", "中速", "高速"];
// The tabs' live state from the last robot state: torque per part, and the parts a held key is driving.
const keyboard = { part: "right_arm", torque: {}, driven: new Set() };

// The on-screen keys laid out the way each part moves (Penpot: 「键盘控制 / 电脑（修订）」). A pad is groups of
// columns, three rows each, "|" a divider between groups; a cell is [column, row, action] or [column, row, "@legend"],
// the legend naming the control in the empty middle cell. "@level" is the base's live speed level.
const ARM_PAD = [
  { cols: 3, cells: [[1, 0, "forward"], [0, 1, "pan_left"], [1, 1, "@手臂"], [2, 1, "pan_right"], [1, 2, "back"]] },
  { cols: 1, air: true, cells: [[0, 0, "up"], [0, 1, "@高度"], [0, 2, "down"]] },
  "|",
  { cols: 2, cells: [[0, 0, "pitch_up"], [0, 1, "@俯仰"], [0, 2, "pitch_down"], [1, 0, "roll_ccw"], [1, 1, "@旋转"], [1, 2, "roll_cw"]] },
  "|",
  { cols: 1, cells: [[0, 0, "open"], [0, 1, "@夹爪"], [0, 2, "close"]] },
];
const PADS = {
  left_arm: ARM_PAD,
  right_arm: ARM_PAD,
  head: [{ cols: 3, cells: [[1, 0, "look_up"], [0, 1, "look_left"], [1, 1, "@头部"], [2, 1, "look_right"], [1, 2, "look_down"]] }],
  base: [
    { cols: 3, cells: [[1, 0, "forward"], [0, 1, "left"], [1, 1, "@平移"], [2, 1, "right"], [1, 2, "backward"]] },
    { cols: 1, air: true, cells: [[0, 0, "turn_left"], [0, 1, "@转向"], [0, 2, "turn_right"]] },
    "|",
    { cols: 1, cells: [[0, 0, "faster"], [0, 1, "@level"], [0, 2, "slower"]] },
  ],
};
// The direction each action moves.
const GLYPHS = {
  forward: "↑", back: "↓", backward: "↓", pan_left: "←", pan_right: "→", up: "↑", down: "↓", pitch_up: "↑", pitch_down: "↓",
  roll_ccw: "↺", roll_cw: "↻", open: "↔", close: "→←", look_up: "↑", look_down: "↓", look_left: "←", look_right: "→",
  left: "←", right: "→", turn_left: "↺", turn_right: "↻", faster: "+", slower: "−",
};
// Per part: the base drives forward (前进), it does not reach (the arms' 前伸).
const ARM_LABELS = { roll_ccw: "逆时针", roll_cw: "顺时针" };
const PAD_LABELS = { left_arm: ARM_LABELS, right_arm: ARM_LABELS, head: {}, base: { forward: "前进" } };
// Seven columns at most (the arm), so a cell is the same size on every tab.
const PAD_CELL = "((100% - 80px) / 7)";

function padHtml(part) {
  const keys = Object.fromEntries(App.config.keymap[part].map((key) => [key.action, key]));
  return PADS[part].map((group) => {
    if (group === "|") return `<div class="mx-1.5 w-px shrink-0 self-stretch bg-gray-200"></div>`;
    const cells = group.cells.map(([column, row, what]) => {
      const place = `grid-column: ${column + 1}; grid-row: ${row + 1}`;
      if (what === "@level") return `<span id="base-level" style="${place}" class="flex items-center justify-center text-xs font-semibold text-blue-600"></span>`;
      if (what.startsWith("@")) return `<span style="${place}" class="flex items-center justify-center text-xs text-gray-400">${what.slice(1)}</span>`;
      const key = keys[what];
      // An arrow key already shows its direction: "↑", not "↑ ↑".
      const name = keyName(key.code);
      const hint = name === GLYPHS[what] ? name : `${GLYPHS[what]} ${name}`;
      return `<button type="button" data-code="${key.code}" style="${place}" class="flex min-w-0 select-none flex-col items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-800 hover:bg-gray-50 focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:outline-none data-[held=true]:border-blue-50 data-[held=true]:bg-blue-50 data-[held=true]:text-blue-600">
        <span class="truncate text-xs font-semibold">${PAD_LABELS[part][what] ?? key.label}</span><span class="text-[11px] opacity-60">${hint}</span></button>`;
    }).join("");
    const width = `calc(${PAD_CELL} * ${group.cols} + ${(group.cols - 1) * 6}px)`;
    return `<div class="grid shrink-0 grid-rows-[3rem_3rem_3rem] gap-1.5 ${group.air ? "ms-1.5" : ""}" style="width: ${width}; grid-template-columns: repeat(${group.cols}, minmax(0, 1fr))">${cells}</div>`;
  }).join("");
}

function buildTabs() {
  document.getElementById("part-tabs").innerHTML = Object.entries(PART_TABS).map(([part, label]) =>
    `<button type="button" role="tab" data-tab="${part}"><span data-dot="${part}" class="size-2 shrink-0 rounded-full"></span>${label}</button>`).join("");
  document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => { keyboard.part = button.dataset.tab; buildKeys(); }));
  try { keyboard.part = localStorage.getItem("xlerobot.keyboard_part") || keyboard.part; } catch { /* private window */ }
  // ?part=head opens on that tab (a link to it, or a screenshot of it).
  const asked = new URLSearchParams(location.search).get("part");
  if (asked in PART_TABS) keyboard.part = asked;
}

// Each tab is a button and a status: the dot is the part's torque (filled teal on, hollow grey off); the chosen tab is
// a raised white segment; a part a held key is driving turns light blue, the same blue as a held key, even when its
// tab is not the one shown.
function paintTabs() {
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const part = button.dataset.tab;
    const chosen = part === keyboard.part;
    const look = chosen ? "border-gray-200 bg-white font-semibold text-blue-600 shadow-sm"
      : keyboard.driven.has(part) ? "border-transparent bg-blue-50 font-medium text-blue-600"
        : "border-transparent font-medium text-gray-600 hover:bg-gray-200 hover:text-gray-800";
    button.setAttribute("aria-selected", chosen);
    button.className = `flex h-9 items-center justify-center gap-2 rounded-md border text-sm focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:outline-none ${look}`;
  });
  document.querySelectorAll("[data-dot]").forEach((dot) => {
    const on = keyboard.torque[dot.dataset.dot];
    dot.className = `size-2 shrink-0 rounded-full ${on ? "bg-teal-600" : "border-[1.5px] border-gray-400"}`;
    dot.title = on ? "力矩开" : "力矩关";
  });
}

function buildKeys() {
  try { localStorage.setItem("xlerobot.keyboard_part", keyboard.part); } catch { /* private window */ }
  paintTabs();
  document.getElementById("part-keys").innerHTML = padHtml(keyboard.part);
  document.querySelectorAll("#part-keys [data-code]").forEach((element) => {
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

// The parts whose target boxes the user has changed: those stop following the live joints until 执行 or 重置.
const edited = new Set();
const BASE_ROWS = [["x", "x"], ["y", "y"], ["θ", "theta"], ["档位", "level"]];
const CARD_BUTTON = "inline-flex h-6 items-center rounded-md px-2.5 text-xs font-semibold disabled:pointer-events-none";

function buildParts() {
  // One card per part: its state, then per joint the current angle and an editable target (the base: its command).
  document.getElementById("parts").innerHTML = Object.entries(App.config.parts).map(([part, label]) => {
    const joints = App.config.rest_pose[part];
    const rows = joints
      ? Object.keys(joints).map((joint) => `
        <div class="flex items-center gap-1.5 text-[11px]">
          <span class="min-w-0 flex-1 truncate text-gray-500">${joint}</span>
          <span id="now-${part}-${joint}" class="w-12 shrink-0 text-end text-gray-800">…</span>
          <input id="set-${part}-${joint}" data-part="${part}" type="number" step="0.5" inputmode="decimal" aria-label="${label} ${joint} 目标"
            class="h-[22px] w-16 shrink-0 rounded-md border border-gray-200 bg-white px-1.5 text-end text-[11px] text-gray-800 focus:border-blue-600 focus:ring-1 focus:ring-blue-600 focus:outline-none data-[changed=true]:border-blue-600 data-[changed=true]:font-semibold data-[changed=true]:text-blue-600" />
        </div>`).join("")
      : BASE_ROWS.map(([name, key]) => `
        <div class="flex h-[18px] items-center gap-1.5 text-[11px]"><span class="flex-1 text-gray-500">${name}</span><span id="now-base-${key}" class="text-end text-gray-800">…</span></div>`).join("");
    const buttons = joints ? `
      <button type="button" data-reset="${part}" class="${CARD_BUTTON}" disabled>重置</button>
      <button type="button" data-run="${part}" class="${CARD_BUTTON}" disabled>执行</button>` : "";
    return `
    <li class="min-w-0 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
      <div class="flex items-center gap-2"><span class="text-sm font-semibold text-gray-800">${label}</span><span id="state-${part}" class="min-w-0 flex-1 truncate text-xs text-gray-500">…</span>${buttons}</div>
      <div class="mt-2 space-y-0.5">${rows}</div>
    </li>`;
  }).join("");
  document.querySelectorAll("#parts input[data-part]").forEach((input) => input.addEventListener("input", () => {
    edited.add(input.dataset.part);
    paintPartButtons();
  }));
  document.querySelectorAll("[data-run]").forEach((button) => button.addEventListener("click", () => onRunJoints(button.dataset.run)));
  document.querySelectorAll("[data-reset]").forEach((button) => button.addEventListener("click", () => { edited.delete(button.dataset.reset); paintPartButtons(); }));
  paintPartButtons();
}

function paintPartButtons() {
  document.querySelectorAll("[data-run]").forEach((button) => {
    const on = edited.has(button.dataset.run);
    button.disabled = !on;
    button.className = `${CARD_BUTTON} ${on ? "bg-blue-600 text-white hover:bg-blue-700" : "bg-gray-100 text-gray-400"}`;
  });
  document.querySelectorAll("[data-reset]").forEach((button) => {
    const on = edited.has(button.dataset.reset);
    button.disabled = !on;
    button.className = `${CARD_BUTTON} ${on ? "border border-gray-200 bg-white text-gray-800 hover:bg-gray-50" : "bg-gray-100 text-gray-400"}`;
  });
}

// 执行: the part walks to the target boxes (20°/s, inside the calibrated range); only after the change list is confirmed.
async function onRunJoints(part) {
  const label = App.config.parts[part];
  if (!App.latest?.torque[part]) {
    notify(`${label}没有力矩：先在顶栏打开力矩，再执行。`);
    return;
  }
  const joints = {};
  const changes = [];
  for (const [joint, [present]] of Object.entries(App.latest.joints[part])) {
    const value = Number(document.getElementById(`set-${part}-${joint}`).value);
    if (!Number.isFinite(value)) {
      notify(`${label} ${joint} 的目标不是数字。`);
      return;
    }
    joints[joint] = value;
    if (Math.abs(value - present) > 0.5) changes.push(`${joint}：${present.toFixed(1)} → ${value.toFixed(1)}`);
  }
  if (!changes.length) {
    notify(`${label}已经在这些角度上。`);
    return;
  }
  if (!confirm(`${label}会以 20°/s 移动：\n${changes.join("\n")}\n\n移动中可按 Esc / 空格 或「停止运动」停下。开始？`)) return;
  let result;
  try {
    result = await api("POST", `${ROBOT_API}/api/move_joints`, { part, joints });
  } catch (error) {
    notify(`${label}没有移动：${error.message}`);
    return;
  }
  edited.delete(part);
  paintPartButtons();
  notify(result.clamped?.length ? `${label}的 ${result.clamped.join("、")} 超出标定范围，已限制在范围内。` : "");
}

function buildRestPose() {
  document.getElementById("rest-pose").innerHTML = Object.entries(App.config.rest_pose).map(([part, joints]) => `
    <div>
      <h3 class="mb-2 text-xs font-medium text-gray-500">休息位 · ${App.config.parts[part]}</h3>
      <dl class="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-xs">
        ${Object.entries(joints).map(([joint, value]) => `<dt class="text-gray-500">${joint}</dt><dd class="text-end text-gray-800">${value.toFixed(1)}</dd>`).join("")}
      </dl>
    </div>`).join("");
}

// 保存为休息位 / 保存为抓取位: the arms' and head's joints as they are now, then the robot process restarts to use them
// everywhere (parts that still have torque are taken over, so the arms do not drop); the page reloads by itself.
async function onSavePose(name, label) {
  if (!confirm(`把两只手臂和头部现在的关节位置保存为${label}？\n以后上力矩可以从这里开始、断力矩前也可以回到这里。\n保存后机器人进程会重启（约 15 秒，手臂不会掉），页面会自动刷新。`)) return;
  try {
    await api("POST", `${ROBOT_API}/api/save_pose`, { name });
    notify(`${label}已保存，正在重启机器人进程……`);
    await api("POST", "/api/system/restart_robot");
  } catch (error) {
    notify(`没有保存${label}：${error.message}`);
  }
}

function setText(id, text, classes) {
  const element = document.getElementById(id);
  element.textContent = text;
  if (classes !== undefined) element.className = classes;
}

// A card's one-word state (torque is on the tabs' dots and in the ready line).
function partState(state, part) {
  if (part === "base") return [state.torque.base ? "力矩开" : "力矩关", "text-gray-500"];
  const away = state.away_from_rest[part];
  const worst = Math.max(0, ...Object.values(away).map(Math.abs));
  if (state.following.includes(part)) return ["跟随轨迹", "text-purple-700"];
  if (state.parking.includes(part)) return ["回停靠位中", "text-blue-600"];
  if (state.moving[part]) return ["移动中", "text-blue-600"];
  if (edited.has(part)) return ["已改目标", "text-blue-600"];
  if (!Object.keys(away).length) return ["在停靠位", "text-teal-700"];
  return [`离停靠位 ${worst.toFixed(0)}°`, "text-yellow-700"];
}

function render(state) {
  const held = new Set(state.pressed);
  document.querySelectorAll("#part-keys [data-code]").forEach((element) => { element.dataset.held = held.has(element.dataset.code); });
  keyboard.torque = state.torque;
  keyboard.driven = new Set(Object.keys(PART_TABS).filter((part) => App.config.keymap[part].some((key) => held.has(key.code))));
  paintTabs();
  const keys = state.pressed.map(keyName).join(" ");
  setText("keys-state", keys ? `按住：${keys}` : "键盘控制已启用");
  for (const part of Object.keys(App.config.parts)) {
    const [text, color] = partState(state, part);
    setText(`state-${part}`, text, `min-w-0 flex-1 truncate text-xs ${color}`);
    const joints = state.joints[part];
    if (!joints) continue;
    for (const [joint, [present]] of Object.entries(joints)) {
      const now = document.getElementById(`now-${part}-${joint}`);
      const input = document.getElementById(`set-${part}-${joint}`);
      if (!now || !input) continue;
      now.textContent = `${present.toFixed(1)}${joint === "gripper" ? "" : "°"}`;
      // Untouched boxes follow the live joints; a box being typed in is left alone.
      if (!edited.has(part) && document.activeElement !== input) input.value = present.toFixed(1);
      input.dataset.changed = edited.has(part) && Math.abs(Number(input.value) - present) > 0.5;
    }
  }
  const command = state.base_command;
  setText("now-base-x", `${command["x.vel"].toFixed(2)} m/s`);
  setText("now-base-y", `${command["y.vel"].toFixed(2)} m/s`);
  setText("now-base-theta", `${command["theta.vel"].toFixed(0)} °/s`);
  setText("now-base-level", LEVEL_NAMES[state.base_level] ?? `档 ${state.base_level + 1}`);
  // The base's speed level, between 加速 and 减速 on the base tab.
  const level = document.getElementById("base-level");
  if (level) level.textContent = LEVEL_NAMES[state.base_level] ?? `档 ${state.base_level + 1}`;
  const rows = [];
  for (const [part, joints] of Object.entries(state.joints)) {
    rows.push(`<tr class="bg-gray-50/60"><td colspan="3" class="px-3 py-1 font-sans font-medium text-gray-600">${App.config.parts[part]}${state.torque[part] ? "" : "（无力矩，跟随）"}</td></tr>`);
    for (const [joint, [present, target]] of Object.entries(joints)) {
      const behind = state.torque[part] && Math.abs(present - target) > 1;
      rows.push(`<tr><td class="px-3 py-0.5 text-gray-600">${joint}</td><td class="px-3 py-0.5 text-end text-gray-800">${present.toFixed(1)}</td>
        <td class="px-3 py-0.5 text-end ${behind ? "text-blue-600" : "text-gray-400"}">${target.toFixed(1)}</td></tr>`);
    }
  }
  document.getElementById("joints").innerHTML = rows.join("");
}

App.ready.then(() => {
  // On this page every mapped key drives the robot, whichever tab is shown.
  App.mappedCodes = new Set(Object.values(App.config.keymap).flat().map((key) => key.code));
  buildTabs();
  buildKeys();
  buildParts();
  buildRestPose();
  document.getElementById("btn-save-rest").addEventListener("click", () => onSavePose("rest", "休息位"));
  document.getElementById("btn-save-grasp").addEventListener("click", () => onSavePose("grasp_start", "抓取位"));
  // The dock buttons sit in 调试详情's summary: clicking them must not fold it open or shut.
  document.querySelectorAll("summary button").forEach((button) => button.addEventListener("click", (event) => event.preventDefault()));
  App.stateListeners.push(render);
});

// The head caption shows the stream's real size once a frame has arrived (an MJPEG stream may never fire "load").
const headSize = setInterval(() => {
  const image = document.getElementById("head-img");
  if (!image.naturalWidth) return;
  document.getElementById("head-size").textContent = `${image.naturalWidth}×${image.naturalHeight}`;
  clearInterval(headSize);
}, 500);

// The head depth tile: the processed depth (FFS, what planning uses), not the sensor's raw stream.
live("depth-image", `${GRASP_API}/api/depth/refined.jpg`, null, depthNote);
