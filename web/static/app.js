const state = {
  polling: null,
  logPolling: null,
  lastLogId: 0,
};

function $(selector) {
  return document.querySelector(selector);
}

function toast(message, variant = "info") {
  const toastEl = $("#toast");
  toastEl.textContent = message;
  toastEl.dataset.variant = variant;
  toastEl.classList.add("show");
  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => toastEl.classList.remove("show"), 2800);
}

function updateStatusView(status) {
  const page = $(".page");
  page.dataset.status = status.status ?? "idle";
  $("#status-pill").textContent = translateStatus(status.status);
  $("#status-text").textContent = translateStatus(status.status);
  $("#status-mode").textContent = status.mode ?? "-";
  $("#status-protocol").textContent = status.protocol ?? "-";
  $("#status-skip").textContent = status.skipActivation ? "是" : "否";
  $("#status-pid").textContent = status.pid ?? "-";
  $("#status-exit").textContent =
    typeof status.exitCode === "number" ? status.exitCode : "-";
  $("#status-message").textContent = status.message ?? "";

  const startBtn = $("#start-btn");
  const stopBtn = $("#stop-btn");
  const isRunning = status.status === "running" || status.status === "starting";
  startBtn.disabled = isRunning;
  stopBtn.disabled = !isRunning && status.status !== "stopping";
}

function translateStatus(status) {
  switch (status) {
    case "running":
      return "运行中";
    case "starting":
      return "启动中";
    case "stopping":
      return "停止中";
    case "failed":
      return "异常退出";
    case "stopped":
      return "已停止";
    case "idle":
    default:
      return "待机";
  }
}

async function fetchStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error("无法获取状态");
    const data = await response.json();
    updateStatusView(data);
  } catch (error) {
    toast(error.message || "获取状态失败", "error");
  }
}

function formatLog(entry) {
  return `${entry.timestamp} [${entry.source}] ${entry.message}`;
}

async function fetchLogs() {
  try {
    const response = await fetch(`/api/logs?since=${state.lastLogId}`);
    if (!response.ok) throw new Error("无法获取日志");
    const data = await response.json();
    const logs = data.logs ?? [];
    if (logs.length === 0) return;
    const logView = $("#log-view");
    const logCount = $("#log-count");
    const lines = logs.map(formatLog).join("\n");
    if (logView.textContent.trim() === "等待日志...") {
      logView.textContent = lines;
    } else {
      logView.textContent += `\n${lines}`;
    }
    logView.scrollTop = logView.scrollHeight;
    state.lastLogId = logs[logs.length - 1].id;
    const count = parseInt(logCount.dataset.count || "0", 10) + logs.length;
    logCount.dataset.count = String(count);
    logCount.textContent = `${count} 条`;
  } catch (error) {
    toast(error.message || "获取日志失败", "error");
  }
}

async function startApplication(event) {
  event.preventDefault();
  const payload = {
    mode: $("#mode").value,
    protocol: $("#protocol").value,
    skipActivation: $("#skipActivation").checked,
  };

  try {
    const response = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "启动失败");
    }
    toast("应用启动命令已发送", "success");
    state.lastLogId = 0;
    $("#log-view").textContent = "等待日志...";
    const logCount = $("#log-count");
    logCount.dataset.count = "0";
    logCount.textContent = "0 条";
    fetchStatus();
  } catch (error) {
    toast(error.message || "启动失败", "error");
  }
}

async function stopApplication() {
  try {
    const response = await fetch("/api/stop", { method: "POST" });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "停止失败");
    }
    toast("已请求停止应用", "success");
    fetchStatus();
  } catch (error) {
    toast(error.message || "停止失败", "error");
  }
}

async function clearLogs() {
  try {
    const response = await fetch("/api/logs/reset", { method: "POST" });
    if (!response.ok) throw new Error("无法清空日志");
    state.lastLogId = 0;
    $("#log-view").textContent = "等待日志...";
    const logCount = $("#log-count");
    logCount.dataset.count = "0";
    logCount.textContent = "0 条";
    toast("日志已清空", "success");
  } catch (error) {
    toast(error.message || "清空日志失败", "error");
  }
}

function init() {
  $("#control-form").addEventListener("submit", startApplication);
  $("#stop-btn").addEventListener("click", stopApplication);
  $("#clear-log-btn").addEventListener("click", clearLogs);

  fetchStatus();
  fetchLogs();
  state.polling = setInterval(fetchStatus, 2000);
  state.logPolling = setInterval(fetchLogs, 1500);
}

document.addEventListener("DOMContentLoaded", init);
