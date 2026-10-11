// 系统设置: the self-check report (GET /api/system/check), one card per stage, and the robot process restart.
"use strict";

const MARK = { true: ["✓", "text-emerald-600"], false: ["✗", "text-red-600"], null: ["!", "text-orange-500"] };
const STAGE_LOOK = { true: "border-gray-200", false: "border-red-200", null: "border-orange-200" };
let checking = false;

function escape(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function render(report) {
  const summary = document.getElementById("summary");
  const ready = report.ready;
  summary.className = `flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border p-4 shadow-2xs ${ready ? (report.warnings ? "border-orange-200 bg-orange-50" : "border-emerald-200 bg-emerald-50") : "border-red-200 bg-red-50"}`;
  document.getElementById("summary-title").textContent = ready ? (report.warnings ? `机器人就绪，有 ${report.warnings} 条提醒` : "机器人就绪：可以遥操作和抓取") : `需要处理：${report.problems} 项`;
  const entries = report.stages.flatMap((stage) => stage.items);
  const first = entries.find((entry) => entry.ok === false) || entries.find((entry) => entry.ok === null);
  document.getElementById("summary-text").textContent = first ? `${first.ok === false ? "" : "提醒："}${first.name}：${first.detail}${first.fix ? `。${first.fix}` : ""}` : "所有检查都通过了。";
  document.getElementById("stages").innerHTML = report.stages.map((stage, index) => `
    <section class="flex flex-col rounded-xl border ${STAGE_LOOK[stage.ok]} bg-white shadow-2xs">
      <div class="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <h2 class="text-sm font-bold text-gray-800">${index + 1} ${escape(stage.title)}</h2>
        <span class="text-base font-bold ${MARK[stage.ok][1]}">${MARK[stage.ok][0]}</span>
      </div>
      <ul class="divide-y divide-gray-100">
        ${stage.items.map((entry) => `
        <li class="flex gap-3 px-4 py-2">
          <span class="w-4 shrink-0 font-bold ${MARK[entry.ok][1]}">${MARK[entry.ok][0]}</span>
          <div class="min-w-0">
            <p class="text-sm font-medium text-gray-800">${escape(entry.name)}</p>
            <p class="break-words text-xs text-gray-500">${escape(entry.detail)}</p>
            ${entry.fix ? `<p class="mt-0.5 text-xs text-orange-700">${escape(entry.fix)}</p>` : ""}
          </div>
        </li>`).join("")}
      </ul>
    </section>`).join("");
  document.getElementById("check-time").textContent = `上次自检 ${new Date(report.time * 1000).toLocaleTimeString()}（${report.seconds} s）`;
}

async function check() {
  if (checking) return;
  checking = true;
  document.getElementById("btn-check").disabled = true;
  const response = await fetch("/api/system/check").catch(() => null);
  checking = false;
  document.getElementById("btn-check").disabled = false;
  if (!response || !response.ok) {
    document.getElementById("summary-title").textContent = "自检没做成：网页服务没响应";
    return;
  }
  render(await response.json());
}

async function restartRobot() {
  if (!confirm("重启机器人进程：画面和键盘控制会断开十几秒；还有力矩的部件会被接管、停在原地。继续？")) return;
  document.getElementById("btn-restart-robot").disabled = true;
  notify("正在重启机器人进程……");
  try {
    await api("POST", "/api/system/restart_robot");
    notify("机器人进程已重启。");
  } catch (error) {
    notify(`重启没成功：${error.message}`);
  }
  document.getElementById("btn-restart-robot").disabled = false;
  check();
}

async function loadDevices(result) {
  const data = result || await api("GET", "/api/system/devices").catch(() => null);
  if (!data || !data.mapping) return;
  for (const role of ["left_wrist", "right_wrist"]) {
    const camera = data.cameras.find((item) => item.role === role);
    document.getElementById(`port-${role}`).textContent = `口 ${data.mapping[role]}${camera ? ` · ${camera.device}` : " · 没有相机"}`;
  }
  const state = document.getElementById("match-state");
  state.textContent = data.problems.length ? data.problems.join("；") : data.mapping.confirmed ? `${data.mapping.confirmed} 确认过` : "还没确认";
  state.className = `text-xs ${data.problems.length ? "text-red-600" : data.mapping.confirmed ? "text-emerald-700" : "text-orange-600"}`;
}

App.ready.then(() => {
  document.getElementById("btn-match-confirm").addEventListener("click", async () => {
    try {
      loadDevices(await api("POST", "/api/system/devices/confirm"));
    } catch (error) {
      notify(`没有确认：${error.message}`);
    }
    check();
  });
  document.getElementById("btn-match-swap").addEventListener("click", async () => {
    if (!confirm("交换左右腕相机：机器人进程会重启（画面断开十几秒，有力矩的部件停在原地）。继续？")) return;
    notify("正在交换并重启机器人进程……");
    try {
      loadDevices(await api("POST", "/api/system/devices/swap"));
      notify("已交换。再张开左手夹爪看一次：现在应该在「左腕」那一格。");
    } catch (error) {
      notify(`交换后没有重启成功：${error.message}`);
      loadDevices();
    }
    check();
  });
  loadDevices();
  document.getElementById("btn-check").addEventListener("click", check);
  document.getElementById("btn-restart-robot").addEventListener("click", restartRobot);
  setInterval(() => { if (document.getElementById("auto-check").checked) check(); }, 10000);
  check();
});
