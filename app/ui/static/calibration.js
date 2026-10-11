// 相机标定: the ChArUco tab (live detection on three cameras, the current results) and the depth comparison tab
// (head RGB, sensor depth, FFS depth; freeze, export). Only the open tab's pictures are fetched.
"use strict";

const calib = { tab: "charuco", frozen: false, shownAt: null };
// The segmented tabs (as on the keyboard page): the open tab a raised white segment on the grey track.
const TAB_ON = "border-gray-200 bg-white font-semibold text-blue-600 shadow-sm";
const TAB_OFF = "border-transparent text-gray-600 hover:bg-gray-200";

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function setTab(name) {
  calib.tab = name;
  for (const other of ["charuco", "depth"]) {
    document.getElementById(`panel-${other}`).classList.toggle("hidden", other !== name);
    const button = document.getElementById(`tab-${other}`);
    button.className = button.className.replace(TAB_ON, "").replace(TAB_OFF, "").trim() + " " + (other === name ? TAB_ON : TAB_OFF);
  }
}

// One camera's board picture, then the next one once it has arrived (only while its tab is open).
async function detectLoop(camera) {
  while (true) {
    if (calib.tab === "charuco") {
      const response = await fetch(`/api/calibration/detect/${camera}.jpg?t=${Date.now()}`).catch(() => null);
      const label = document.getElementById(`count-${camera}`);
      if (response && response.ok) {
        const url = URL.createObjectURL(await response.blob());
        const image = document.getElementById(`detect-${camera}`);
        const old = image.src;
        image.src = url;
        if (old.startsWith("blob:")) URL.revokeObjectURL(old);
        const count = Number(response.headers.get("X-Corners"));
        const error = response.headers.get("X-Error-Px");
        label.textContent = count ? `已检测 ${count}/54 角点${error ? ` · ${error} px` : ""}` : "没看到标定板";
        label.className = `min-w-0 truncate text-[11px] ${count >= 8 ? "text-emerald-700" : "text-gray-500"}`;
      } else {
        label.textContent = "没有画面";
        label.className = "min-w-0 truncate text-[11px] text-red-600";
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
}

async function loadResults() {
  const response = await fetch("/api/calibration/results").catch(() => null);
  if (!response || !response.ok) return;
  const data = await response.json();
  const board = data.board;
  document.getElementById("board-text").textContent =
    `标定板：ChArUco ${board.squares[0]} × ${board.squares[1]} 格，方格 ${board.square_mm} mm，标记 ${board.marker_mm} mm，${board.dictionary}。`;
  document.getElementById("results").innerHTML = data.results.map((row) => `
    <tr class="align-top">
      <td class="px-4 py-2 font-medium text-gray-800">${escapeHtml(row.camera)}</td>
      <td class="px-4 py-2 text-gray-700">${escapeHtml(row.content)}${row.note ? `<p class="mt-1 max-w-md text-xs text-gray-500">${escapeHtml(row.note)}</p>` : ""}</td>
      <td class="px-4 py-2 font-mono text-xs text-gray-600">${Object.entries(row.figures).map(([key, value]) => `<div>${escapeHtml(key)}：${escapeHtml(value)}</div>`).join("")}</td>
      <td class="px-4 py-2 text-xs text-gray-500">${escapeHtml(row.file)}<br />${escapeHtml(row.modified)}</td>
    </tr>`).join("");
}

// The three comparison pictures, one after another, unless frozen.
async function compareLoop() {
  const sources = { rgb: `${ROBOT_API}/snapshot/head`, raw: `${GRASP_API}/api/depth/raw.jpg`, refined: `${GRASP_API}/api/depth/refined.jpg` };
  while (true) {
    if (calib.tab === "depth" && !calib.frozen) {
      await Promise.all(Object.entries(sources).map(async ([kind, url]) => {
        const response = await fetch(`${url}?t=${Date.now()}`).catch(() => null);
        if (!response || !response.ok) return;
        const image = document.getElementById(`compare-${kind}`);
        const old = image.src;
        image.src = URL.createObjectURL(await response.blob());
        if (old.startsWith("blob:")) URL.revokeObjectURL(old);
        // The depths' captions and the shared colour scale, from the pictures' headers.
        const valid = response.headers.get("X-Valid");
        const range = response.headers.get("X-Range");
        if (valid !== null) document.getElementById(`compare-note-${kind}`).textContent = `有效 ${Math.round(Number(valid) * 100)}%`;
        if (range) document.getElementById("compare-scale").textContent = `蓝 ${range.split("-")[0]} m → 红 ${range.split("-")[1]} m`;
      }));
      calib.shownAt = new Date();
      document.getElementById("compare-time").textContent = `帧时间 ${calib.shownAt.toLocaleTimeString()}（三张图是同一轮取的，深度最多慢一帧 FFS）`;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}

function exportCompare() {
  const images = ["rgb", "raw", "refined"].map((kind) => document.getElementById(`compare-${kind}`));
  if (images.some((image) => !image.naturalWidth)) { notify("还没有三张图，先等画面出来"); return; }
  const width = images.reduce((sum, image) => sum + image.naturalWidth, 0);
  const height = Math.max(...images.map((image) => image.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  let x = 0;
  for (const image of images) { context.drawImage(image, x, 0); x += image.naturalWidth; }
  const link = document.createElement("a");
  const stamp = (calib.shownAt || new Date()).toISOString().replace(/[:.]/g, "-").slice(0, 19);
  link.download = `depth_compare_${stamp}.png`;
  link.href = canvas.toDataURL("image/png");
  link.click();
}

// --- the wrist hand-eye session ---

// A session request (a read without body): the session as it is after it, or null when refused (said on the page).
async function sessionCall(path, body) {
  try {
    const result = await api(body === undefined ? "GET" : "POST", `/api/calibration/${path}`, body);
    if (result.poses) renderSession(result);
    return result;
  } catch (error) {
    notify(error.message);
    return null;
  }
}

function renderSession(state) {
  document.getElementById("session-name").textContent = state.session || "没有会话";
  if (state.session) document.getElementById("session-arm").value = state.arm;
  document.getElementById("btn-capture").disabled = !state.session || state.solving;
  document.getElementById("btn-solve").disabled = state.usable < 6 || state.solving;
  document.getElementById("btn-save").disabled = !state.result || state.solving;
  document.getElementById("session-note").textContent = state.solving ? "正在计算（约 1–2 分钟）……" : state.session ? `有效帧 ${state.usable} / ${state.poses.length}` : "";
  document.getElementById("session-poses").innerHTML = state.poses.map((pose) => `
    <tr class="${pose.usable ? "" : "text-gray-400"}"><td class="py-1 pe-4">${pose.i}</td><td class="py-1 pe-4">${pose.wrist_corners}${pose.usable ? "" : "（太少）"}</td>
      <td class="py-1 pe-4">${pose.head_corners}</td><td class="py-1">${Object.entries(pose.arm).map(([k, v]) => `${k} ${v}`).join("，")}</td></tr>`).join("");
  const box = document.getElementById("session-result");
  const result = state.result;
  box.classList.toggle("hidden", !result && !state.error);
  if (state.error) box.innerHTML = `<p class="font-semibold text-red-700">计算失败：${escapeHtml(state.error)}</p>`;
  else if (result) {
    const motors = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"];
    box.innerHTML = `<p class="font-semibold">结果（${result.views.length} 个视角）</p>
      <p>腕部内参 RMS ${result.rms_px.toFixed(3)} px · 板子散布 ${result.board_spread_mm} mm · 重投影中位：起始 ${result.reprojection_px.start} → 拟合 ${result.reprojection_px.fitted} → 留一 ${result.reprojection_px.held_out} px</p>
      <p>关节偏置（°，加在当前修正上）：${motors.map((m, i) => `${m} ${result.joint_offset_deg[i] >= 0 ? "+" : ""}${result.joint_offset_deg[i]}（留一 ±${result.loo_max_deg[i]}）`).join("，")}</p>
      <p>新的关节修正（°；wrist_roll 不改，板子定不准它）：${motors.map((m) => `${m} ${result.correction_deg[m]}`).join("，")}</p>
      <p>腕部相机镜头在定指上：[${result.lens_mm.join(", ")}] mm</p>
      ${state.saved ? `<p class="mt-1 font-semibold text-emerald-700">已保存为 config/calibration/${escapeHtml(state.saved)}（草稿，没有启用：确认后再换掉机器人正在用的修正）</p>` : ""}`;
  }
}

async function pollSession() {
  const state = await sessionCall("session");
  setTimeout(pollSession, state && state.solving ? 2000 : 5000);
}

App.ready.then(() => {
  document.getElementById("btn-session").addEventListener("click", () => sessionCall("session", { arm: document.getElementById("session-arm").value }));
  document.getElementById("btn-capture").addEventListener("click", async (event) => {
    event.target.disabled = true;
    const result = await sessionCall("capture", {});
    if (result && result.pose) notify(result.pose.usable ? `第 ${result.pose.i} 帧：腕部 ${result.pose.wrist_corners} 个角点，头部 ${result.pose.head_corners} 个` : `第 ${result.pose.i} 帧腕部只看到 ${result.pose.wrist_corners} 个角点，不计入；换个角度再采`);
  });
  document.getElementById("btn-solve").addEventListener("click", () => sessionCall("solve", {}));
  document.getElementById("btn-save").addEventListener("click", async () => { const result = await sessionCall("save", {}); if (result) loadResults(); });
  pollSession();
  document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));
  document.getElementById("btn-freeze").addEventListener("click", (event) => {
    calib.frozen = !calib.frozen;
    event.target.textContent = calib.frozen ? "继续刷新" : "冻结当前帧";
  });
  document.getElementById("btn-export").addEventListener("click", exportCompare);
  loadResults();
  ["head", "left_wrist", "right_wrist"].forEach(detectLoop);
  compareLoop();
});

// The head depth tile in the camera block: the processed depth (FFS), as on the keyboard and task pages.
live("depth-image", `${GRASP_API}/api/depth/refined.jpg`, null, depthNote);
