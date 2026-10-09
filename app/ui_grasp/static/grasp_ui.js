// XLeRobot grasp console: the page does all data work through /api and the WebSocket.

const ARM_JOINTS = {
  right: ["Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll", "Jaw"],
  left: ["Rotation_2", "Pitch_2", "Elbow_2", "Wrist_Pitch_2", "Wrist_Roll_2", "Jaw_2"],
};
const POSE_LABELS = { zero: "零位", ready: "就绪", open: "张开", close: "闭合" };

const page = { arm: "right", poses: {}, joints: {}, objects: [], object: null, grasps: [], grasp: null };

async function api(path, body) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json();
  return data;
}

function makeButton(label, onClick) {
  const button = document.getElementById("button-template").content.firstElementChild.cloneNode(true);
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function renderArmSwitch() {
  const box = document.getElementById("arm-switch");
  box.replaceChildren();
  for (const [arm, info] of Object.entries(page.poses)) {
    const button = makeButton(info.label, () => { page.arm = arm; renderAll(); });
    button.dataset.active = String(arm === page.arm);
    box.append(button);
  }
}

function renderPoseButtons(boxId, part) {
  const box = document.getElementById(boxId);
  box.replaceChildren();
  for (const pose of page.poses[page.arm][part]) {
    box.append(makeButton(POSE_LABELS[pose] ?? pose, () => move(part, pose)));
  }
}

function renderJoints() {
  const rows = ARM_JOINTS[page.arm].map((name) => {
    const degrees = ((page.joints[name] ?? 0) * 180 / Math.PI).toFixed(1);
    return `<tr><td class="py-1">${name}</td><td class="py-1 text-end">${degrees}</td></tr>`;
  });
  document.getElementById("joint-table").innerHTML = rows.join("");
}

function renderAll() {
  renderArmSwitch();
  renderPoseButtons("arm-poses", "arm");
  renderPoseButtons("gripper-poses", "gripper");
  renderJoints();
}

async function move(part, pose) {
  const status = document.getElementById("move-status");
  const buttons = document.querySelectorAll("#arm-poses button, #gripper-poses button");
  buttons.forEach((button) => { button.disabled = true; });
  status.textContent = `正在移动到「${POSE_LABELS[pose] ?? pose}」…`;
  const result = await api("/api/robot/move", { arm: page.arm, part, pose });
  status.textContent = result.moved ? `已到达「${POSE_LABELS[pose] ?? pose}」` : "规划失败";
  buttons.forEach((button) => { button.disabled = false; });
}

function renderObjects() {
  const box = document.getElementById("object-list");
  box.replaceChildren();
  for (const item of page.objects) {
    const button = makeButton(item.label, () => { page.object = item.name; renderObjects(); });
    button.dataset.active = String(item.name === page.object);
    box.append(button);
  }
}

function renderGrasps() {
  const box = document.getElementById("grasp-list");
  box.replaceChildren();
  for (const grasp of page.grasps) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "flex w-full items-center justify-between px-3 py-1.5 text-start hover:bg-gray-50 data-[active=true]:bg-blue-50 data-[active=true]:text-blue-700";
    row.dataset.active = String(grasp.index === page.grasp);
    row.innerHTML = `<span>#${grasp.index}</span><span>${grasp.score.toFixed(3)}</span>`;
    row.addEventListener("click", () => selectGrasp(grasp.index));
    box.append(row);
  }
}

async function generateGrasps() {
  const status = document.getElementById("grasp-status");
  const button = document.getElementById("btn-generate");
  button.disabled = true;
  status.textContent = "生成中…";
  const result = await api("/api/grasps", { object: page.object });
  page.grasps = result.grasps;
  page.grasp = null;
  status.textContent = `${result.object}：${result.grasps.length} 个不穿桌的抓取，用时 ${result.seconds}s`;
  renderGrasps();
  button.disabled = false;
}

async function selectGrasp(index) {
  const result = await api("/api/grasps/select", { index });
  page.grasp = result.index;
  document.getElementById("grasp-status").textContent = `抓取 #${result.index}：分数 ${result.score}，位置 ${result.position.join(", ")}`;
  renderGrasps();
}

async function executeGrasp() {
  const button = document.getElementById("btn-execute");
  button.disabled = true;
  await api("/api/grasps/execute", {});
  button.disabled = false;
}

function renderResult(result) {
  const box = document.getElementById("execute-result");
  if (!result) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const badge = document.getElementById("execute-badge");
  badge.textContent = result.grasped ? `抓取成功：物体升高 ${result.lifted_cm} cm` : `抓取失败：物体升高 ${result.lifted_cm} cm`;
  badge.className = badge.className.replace(/bg-\S+ text-\S+/g, "") + (result.grasped ? " bg-teal-50 text-teal-700" : " bg-red-50 text-red-700");
  const video = document.getElementById("execute-video");
  if (video.dataset.src !== result.video) {
    video.dataset.src = result.video;
    video.src = result.video;
  }
}

function connectExecution() {
  const socket = new WebSocket(`ws://${location.host}/ws/execution`);
  socket.onmessage = (event) => {
    const status = JSON.parse(event.data);
    document.getElementById("execute-progress").innerHTML = status.progress.map((line) => `<li>${line}</li>`).join("");
    document.getElementById("btn-execute").disabled = status.running;
    renderResult(status.running ? null : status.result);
  };
}

function connectState() {
  const socket = new WebSocket(`ws://${location.host}/ws/robot/state`);
  const badge = document.getElementById("link-badge");
  socket.onopen = () => { badge.textContent = "已连接"; badge.className = badge.className.replace("bg-gray-100 text-gray-600", "bg-teal-50 text-teal-700"); };
  socket.onmessage = (event) => { page.joints = JSON.parse(event.data).joints; renderJoints(); };
  socket.onclose = () => { badge.textContent = "连接断开"; };
}

async function init() {
  document.getElementById("viewer").src = `http://${location.hostname}:${document.body.dataset.viewerPort}`;
  page.poses = await api("/api/robot/poses");
  renderAll();
  connectState();
  page.objects = await api("/api/objects");
  page.object = page.objects[0].name;
  renderObjects();
  document.getElementById("btn-generate").addEventListener("click", generateGrasps);
  document.getElementById("btn-execute").addEventListener("click", executeGrasp);
  connectExecution();
}

init();
