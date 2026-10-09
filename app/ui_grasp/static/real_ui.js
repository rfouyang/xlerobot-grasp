// The real-robot tab: one button per step, in order; the status is polled.
"use strict";

const realState = { status: null, busy: false, planShownFor: -1, alignShownFor: -1 };

async function realApi(path, body) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = response.ok ? await response.json() : { ok: false, error: `${response.status} ${await response.text()}` };
  return data;
}

function renderReal(status) {
  realState.status = status;
  const list = document.getElementById("real-stages");
  list.replaceChildren();
  status.stages.forEach((stage, index) => {
    const done = index < status.next;
    const current = index === status.next;
    const item = document.createElement("li");
    item.className = "flex items-center gap-x-2";
    const mark = document.createElement("span");
    mark.className = `inline-flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
      done ? "bg-teal-100 text-teal-700" : current && status.failed ? "bg-red-100 text-red-700" : current ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-400"}`;
    mark.textContent = done ? "✓" : current && status.failed ? "!" : String(index + 1);
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = stage.label + (current && status.running ? "（执行中…）" : "");
    button.disabled = !current || status.running || status.failed || realState.busy;
    button.className = `flex-1 rounded-lg border px-3 py-1.5 text-start text-xs font-medium shadow-2xs disabled:pointer-events-none ${
      current && !status.failed ? "border-blue-600 bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-60" : "border-gray-200 bg-white text-gray-500 disabled:opacity-60"}`;
    button.addEventListener("click", () => runStage(stage));
    item.append(mark, button);
    list.append(item);
  });
  const progress = document.getElementById("real-progress");
  progress.replaceChildren(...status.progress.map((line) => Object.assign(document.createElement("li"), { textContent: line })));
  progress.scrollTop = progress.scrollHeight;
  if (status.plan_image && realState.planShownFor !== status.next) {
    realState.planShownFor = status.next;
    document.getElementById("real-plan").classList.remove("hidden");
    document.getElementById("real-plan-image").src = `/api/real/plan.jpg?t=${Date.now()}`;
  }
  if (!status.plan_image) document.getElementById("real-plan").classList.add("hidden");
  // Each look during the alignment adds a progress line: show the newest image.
  if (status.align_image && realState.alignShownFor !== status.progress.length) {
    realState.alignShownFor = status.progress.length;
    document.getElementById("real-align").classList.remove("hidden");
    document.getElementById("real-align-image").src = `/api/real/align.jpg?t=${Date.now()}`;
  }
  if (!status.align_image) document.getElementById("real-align").classList.add("hidden");
}

async function runStage(stage) {
  const moves = !["plan"].includes(stage.name);
  if (moves && !confirm(`真机执行：${stage.label}。确认周围安全？（遥操作页 Esc / 本页「停止」随时可停）`)) return;
  realState.busy = true;
  renderReal(realState.status);
  await realApi("/api/real/stage", { name: stage.name });
  realState.busy = false;
  await pollReal();
}

async function pollReal() {
  renderReal(await realApi("/api/real/status"));
}

function initReal() {
  document.getElementById("teleop-link").href = `http://${location.hostname}:8010`;
  document.getElementById("btn-real-stop").addEventListener("click", () => realApi("/api/real/stop", {}));
  document.getElementById("btn-real-reset").addEventListener("click", async () => renderReal(await realApi("/api/real/reset", {})));
  pollReal();
  setInterval(pollReal, 500);
}

initReal();
