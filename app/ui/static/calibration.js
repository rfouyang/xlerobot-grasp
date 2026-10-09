// 相机标定: the ChArUco tab (live detection on three cameras, the current results) and the depth comparison tab
// (head RGB, sensor depth, FFS depth; freeze, export). Only the open tab's pictures are fetched.
"use strict";

const calib = { tab: "charuco", frozen: false, shownAt: null };
const TAB_ON = "bg-blue-50 text-blue-600";
const TAB_OFF = "text-gray-600 hover:bg-gray-100";

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
        label.className = count >= 8 ? "text-emerald-700" : "text-gray-500";
      } else {
        label.textContent = "没有画面";
        label.className = "text-red-600";
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

App.ready.then(() => {
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
