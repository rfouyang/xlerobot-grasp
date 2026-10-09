// 抓取规划: the last plan's candidates (GET /api/planning/report), the re-ranking report, the algorithm for the next
// plan (POST /grasp-api/api/real/algorithms), the plan / semantic / alignment pictures.
"use strict";

const MODE_NAMES = {
  baseline: "基线：GraspGenX 分数", molmo: "GraspMolmo（任务语义点）", densematcher: "DenseMatcher（人的示范接触区域）",
  hog: "HOGraspFlow（示范接近方向）", "molmo+densematcher": "GraspMolmo + DenseMatcher", "molmo+densematcher+hog": "GraspMolmo + DenseMatcher + HOGraspFlow",
};
const shown = { plan: -1, semantic: -1, align: -1, formFilled: false };
const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (value, digits = 3) => (typeof value === "number" ? value.toFixed(digits) : value ?? "—");

function picture(name, available, version, url) {
  const image = document.getElementById(`${name}-image`);
  image.classList.toggle("hidden", !available);
  const empty = document.getElementById(`${name}-empty`);
  if (empty) empty.classList.toggle("hidden", available);
  if (available && shown[name] !== version) {
    shown[name] = version;
    image.src = `${url}?t=${Date.now()}`;
  }
}

function renderFunnel(stats, report) {
  const steps = [["GraspGenX 候选", stats.candidates], ["侧面抓瓶身中段", stats.seated], ["IK 能水平够到", stats.level], ["腕部转动在真机范围内", stats.roll_ok]];
  document.getElementById("plan-arm").textContent = stats.arm ? `${stats.arm === "left" ? "左手" : "右手"} · ${(report.used || []).join(" + ")}` : "";
  document.getElementById("funnel").innerHTML = stats.candidates == null ? '<li class="text-gray-500">还没有规划</li>' : steps.map(([label, value], index) => `
    <li class="flex items-center justify-between rounded-lg px-3 py-1.5 ${index === steps.length - 1 ? "bg-gray-100 font-semibold" : ""}">
      <span class="text-gray-700">${label}</span><span class="font-mono text-gray-800">${value ?? "—"}</span></li>`).join("");
  const selected = report.selected;
  document.getElementById("selected").innerHTML = selected
    ? `<p class="font-semibold">选中候选 #${selected.candidate} · 分数 ${num(selected.score)}</p>` +
      (selected.semantic_rank ? `<p class="mt-1 text-xs">语义排第 ${selected.semantic_rank}，基线排第 ${selected.baseline_rank}</p>` : "")
    : "还没有选中的抓取";
  const rejected = report.desk_rejections || [];
  const box = document.getElementById("rejections");
  box.classList.toggle("hidden", !rejected.length);
  box.innerHTML = `<p class="font-semibold">桌面守卫拒绝 ${rejected.length} 个</p>` + rejected.slice(0, 6).map((row) =>
    `<p>#${row.candidate}：${esc(row.segment)} / ${esc(row.link)} 离桌面 ${(row.clearance_m * 100).toFixed(1)} cm（要 ${(row.required_m * 100).toFixed(1)} cm）</p>`).join("");
}

function renderRanking(stats, report) {
  const ranked = report.ranked || [];
  const selected = report.selected ? report.selected.candidate : null;
  if (ranked.length) {
    const keys = [...new Set(ranked.flatMap((row) => Object.keys(row)))].filter((key) => !["index", "pixel"].includes(key));
    const order = ["semantic_rank", "candidate", "baseline_rank", ...keys.filter((key) => !["semantic_rank", "candidate", "baseline_rank"].includes(key))];
    document.getElementById("ranking-head").innerHTML = order.map((key) => `<th class="px-3 py-2 text-start text-xs font-medium text-gray-500">${esc(key)}</th>`).join("");
    document.getElementById("ranking").innerHTML = ranked.slice(0, 20).map((row) => `
      <tr class="${row.candidate === selected ? "bg-blue-50 font-semibold text-blue-800" : ""}">${order.map((key) => `<td class="px-3 py-1.5">${esc(num(row[key]))}</td>`).join("")}</tr>`).join("");
    document.getElementById("ranking-note").textContent = `${report.status || ""}，共 ${report.candidates ?? ranked.length} 个可达候选重新排序`;
  } else {
    document.getElementById("ranking-head").innerHTML = ["基线排名", "候选", "GraspGenX 分数"].map((label) => `<th class="px-3 py-2 text-start text-xs font-medium text-gray-500">${label}</th>`).join("");
    document.getElementById("ranking").innerHTML = (stats.kept || []).map((row, index) => `
      <tr class="${row.candidate === selected ? "bg-blue-50 font-semibold text-blue-800" : ""}"><td class="px-3 py-1.5">${index + 1}</td><td class="px-3 py-1.5">#${row.candidate}</td><td class="px-3 py-1.5">${num(row.score)}</td></tr>`).join("")
      || '<tr><td class="px-3 py-3 text-gray-500" colspan="3">还没有规划</td></tr>';
    document.getElementById("ranking-note").textContent = "基线：按 GraspGenX 分数（可达的前 20 个）";
  }
}

function fillForm(data) {
  if (shown.formFilled) return;
  shown.formFilled = true;
  document.getElementById("algo-mode").innerHTML = data.modes.map((mode) => `<option value="${mode}">${esc(MODE_NAMES[mode] || mode)}</option>`).join("");
  document.getElementById("algo-mode").value = data.options.mode;
  document.getElementById("algo-task").value = data.options.task || "";
  document.getElementById("algo-dense").value = data.options.densematcher_prior || "";
  document.getElementById("algo-hog").value = data.options.hog_prior || "";
  showPriorRows();
}

function showPriorRows() {
  const parts = document.getElementById("algo-mode").value.split("+");
  document.getElementById("dense-row").classList.toggle("hidden", !parts.includes("densematcher"));
  document.getElementById("hog-row").classList.toggle("hidden", !parts.includes("hog"));
}

async function poll() {
  const response = await fetch("/api/planning/report").catch(() => null);
  if (!response || !response.ok) return;
  const data = await response.json();
  if (!data.ready) return;
  fillForm(data);
  const report = data.report || {};
  const stats = data.stats || {};
  document.getElementById("algo-state").textContent = `当前：${MODE_NAMES[data.options.mode] || data.options.mode}`;
  document.getElementById("algo-apply").disabled = data.running || data.next > 1;
  renderFunnel(stats, report);
  renderRanking(stats, report);
  const version = JSON.stringify(report.selected || null) + data.next;
  picture("plan", data.plan_image, version, `${GRASP_API}/api/real/plan.jpg`);
  picture("semantic", data.semantic_image, version, `${GRASP_API}/api/real/semantic.jpg`);
  picture("align", data.align_image, JSON.stringify(data.align), `${GRASP_API}/api/real/align.jpg`);
  const align = data.align;
  document.getElementById("align-text").textContent = align
    ? `瓶子左边缘离定指 ${align.gap_cm >= 0 ? "+" : ""}${align.gap_cm} cm，还要左移 ${align.shift_cm} cm（容差内即算对准）。`
    : "对准之后显示：瓶子左边缘离定指多远、还要左移多少。";
}

async function applyAlgorithm(event) {
  event.preventDefault();
  const body = { mode: document.getElementById("algo-mode").value, task: document.getElementById("algo-task").value.trim() || "Pick up the bottle" };
  if (!document.getElementById("dense-row").classList.contains("hidden")) body.densematcher_prior = document.getElementById("algo-dense").value.trim();
  if (!document.getElementById("hog-row").classList.contains("hidden")) body.hog_prior = document.getElementById("algo-hog").value.trim();
  const response = await fetch(`${GRASP_API}/api/real/algorithms`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const result = await response.json().catch(() => ({}));
  notify(response.ok ? `下一次规划用：${MODE_NAMES[body.mode] || body.mode}` : `没有设置成：${result.detail || response.status}`);
  poll();
}

App.ready.then(() => {
  document.getElementById("algo-mode").addEventListener("change", showPriorRows);
  document.getElementById("algo-form").addEventListener("submit", applyAlgorithm);
  poll();
  setInterval(poll, 1500);
});
