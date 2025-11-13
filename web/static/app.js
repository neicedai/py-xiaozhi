const state = {
  statusTimer: null,
  logTimer: null,
  lastLogId: 0,
};

let audioClient = null;

function $(selector) {
  return document.querySelector(selector);
}

function toast(message, variant = "info") {
  const toastEl = $("#toast");
  if (!toastEl) return;
  toastEl.textContent = message;
  toastEl.dataset.variant = variant;
  toastEl.classList.add("show");
  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => toastEl.classList.remove("show"), 2800);
}

async function postJSON(url, body = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || detail.message || "操作失败");
  }
  return response.json().catch(() => ({}));
}

class WebAudioClient {
  constructor() {
    this.socket = null;
    this.connected = false;
    this.shouldReconnect = true;
    this.reconnectTimer = null;
    this.pendingResolvers = [];
    this.audioContext = null;
    this.mediaStream = null;
    this.sourceNode = null;
    this.processorNode = null;
    this.silenceNode = null;
    this.micBuffer = new Int16Array(0);
    this.nextPlaybackTime = 0;
    this.streaming = false;
    this.config = {
      inputSampleRate: 16000,
      outputSampleRate: 24000,
      frameSamples: 320,
    };
  }

  connect() {
    if (
      this.socket &&
      (this.socket.readyState === WebSocket.OPEN ||
        this.socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}/ws/audio`;
    try {
      this.socket = new WebSocket(url);
    } catch (error) {
      toast("无法连接音频服务", "error");
      return;
    }
    this.socket.binaryType = "arraybuffer";
    this.socket.onopen = () => this._handleOpen();
    this.socket.onmessage = (event) => this._handleMessage(event);
    this.socket.onerror = () => {
      if (this.socket && this.socket.readyState !== WebSocket.CLOSED) {
        this.socket.close();
      }
    };
    this.socket.onclose = () => this._handleClose();
  }

  _handleOpen() {
    this.connected = true;
    this._resolvePending(true);
    updateWebAudioView({
      connected: true,
      microphoneStreaming: this.streaming,
      statusText: this.streaming ? "已连接·麦克风传输中" : "已连接",
    });
  }

  _handleClose() {
    this.connected = false;
    this._resolvePending(false);
    this.stopStreaming(false);
    updateWebAudioView({ connected: false, statusText: "未连接" });
    this.socket = null;
    if (this.shouldReconnect) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = setTimeout(() => this.connect(), 2000);
    }
  }

  _handleMessage(event) {
    if (typeof event.data === "string") {
      this._handleJson(event.data);
    } else if (event.data instanceof ArrayBuffer) {
      this._playSpeaker(event.data);
    }
  }

  _handleJson(text) {
    let payload;
    try {
      payload = JSON.parse(text);
    } catch (error) {
      console.warn("无法解析音频消息", error);
      return;
    }
    switch (payload.type) {
      case "config":
        if (payload.inputSampleRate) {
          this.config.inputSampleRate = payload.inputSampleRate;
        }
        if (payload.outputSampleRate) {
          this.config.outputSampleRate = payload.outputSampleRate;
        }
        if (payload.frameSamples) {
          this.config.frameSamples = payload.frameSamples;
        }
        break;
      case "device_state":
        // 可用于后续扩展
        break;
      case "pong":
        break;
      default:
        break;
    }
  }

  _resolvePending(value) {
    const callbacks = [...this.pendingResolvers];
    this.pendingResolvers.length = 0;
    callbacks.forEach((cb) => {
      try {
        cb(value);
      } catch (error) {
        console.warn("音频等待回调异常", error);
      }
    });
  }

  ensureContext() {
    if (this.audioContext) {
      return this.audioContext;
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) {
      toast("浏览器不支持 Web Audio", "error");
      return null;
    }
    this.audioContext = new Ctx();
    this.nextPlaybackTime = this.audioContext.currentTime;
    return this.audioContext;
  }

  unlock() {
    const context = this.ensureContext();
    if (!context) return;
    if (context.state === "suspended") {
      context.resume().catch(() => {});
    }
  }

  async waitUntilConnected(timeout = 4000) {
    if (this.connected && this.socket && this.socket.readyState === WebSocket.OPEN) {
      return true;
    }
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pendingResolvers = this.pendingResolvers.filter((cb) => cb !== resolver);
        resolve(false);
      }, timeout);
      const resolver = (value) => {
        clearTimeout(timer);
        resolve(value);
      };
      this.pendingResolvers.push(resolver);
      this.connect();
    });
  }

  async startStreaming() {
    if (this.streaming) return;
    if (!this.connected || !this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error("浏览器音频未连接");
    }
    const context = this.ensureContext();
    if (!context) {
      throw new Error("无法初始化音频上下文");
    }
    if (context.state === "suspended") {
      try {
        await context.resume();
      } catch (error) {
        console.warn("恢复音频上下文失败", error);
      }
    }
    if (!navigator?.mediaDevices?.getUserMedia) {
      throw new Error("浏览器不支持麦克风或权限不可用");
    }
    const insecureOrigin =
      !window.isSecureContext &&
      !["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
    if (insecureOrigin) {
      throw new Error("麦克风访问需要通过 HTTPS 或 localhost 打开页面");
    }
    try {
      const constraints = {
        audio: {
          channelCount: { ideal: 1 },
          noiseSuppression: { ideal: false },
          echoCancellation: { ideal: false },
          autoGainControl: { ideal: false },
        },
      };
      this.mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (error) {
      if (
        error &&
        ["OverconstrainedError", "ConstraintNotSatisfiedError", "NotFoundError"].includes(
          error.name,
        )
      ) {
        try {
          this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (fallbackError) {
          throw new Error(this._describeGetUserMediaError(fallbackError));
        }
      } else {
        throw new Error(this._describeGetUserMediaError(error));
      }
    }
    this.sourceNode = context.createMediaStreamSource(this.mediaStream);
    this.processorNode = context.createScriptProcessor(4096, 1, 1);
    this.processorNode.onaudioprocess = (event) => this._processAudio(event);
    this.silenceNode = context.createGain();
    this.silenceNode.gain.value = 0;

    this.sourceNode.connect(this.processorNode);
    this.processorNode.connect(this.silenceNode);
    this.silenceNode.connect(context.destination);

    this.micBuffer = new Int16Array(0);
    this._sendMicControl(true);
    this.streaming = true;
    updateWebAudioView({
      connected: this.connected,
      microphoneStreaming: true,
      statusText: "已连接·麦克风传输中",
    });
  }

  stopStreaming(sendControl = true) {
    if (!this.streaming && !this.mediaStream) {
      return;
    }
    if (sendControl) {
      this._sendMicControl(false);
    }
    this.streaming = false;
    this.micBuffer = new Int16Array(0);
    if (this.processorNode) {
      try {
        this.processorNode.disconnect();
      } catch (error) {
        console.warn("断开处理节点失败", error);
      }
      this.processorNode.onaudioprocess = null;
      this.processorNode = null;
    }
    if (this.sourceNode) {
      try {
        this.sourceNode.disconnect();
      } catch (error) {
        console.warn("断开音频源失败", error);
      }
      this.sourceNode = null;
    }
    if (this.silenceNode) {
      try {
        this.silenceNode.disconnect();
      } catch (error) {
        console.warn("断开静音节点失败", error);
      }
      this.silenceNode = null;
    }
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((track) => track.stop());
      this.mediaStream = null;
    }
    updateWebAudioView({
      connected: this.connected,
      microphoneStreaming: false,
      statusText: this.connected ? "已连接" : "未连接",
    });
  }

  _sendMicControl(active) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    try {
      this.socket.send(JSON.stringify({ type: "mic", active }));
    } catch (error) {
      console.warn("发送麦克风控制失败", error);
    }
  }

  _processAudio(event) {
    if (!this.streaming || !this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return;
    }
    const inputBuffer = event.inputBuffer.getChannelData(0);
    const downsampled = this._downsampleBuffer(
      inputBuffer,
      event.inputBuffer.sampleRate,
      this.config.inputSampleRate,
    );
    if (!downsampled || downsampled.length === 0) {
      return;
    }
    this.micBuffer = this._concatInt16(this.micBuffer, downsampled);
    const frameSize = this.config.frameSamples || 320;
    const totalFrames = Math.floor(this.micBuffer.length / frameSize);
    if (totalFrames === 0) {
      return;
    }
    for (let i = 0; i < totalFrames; i += 1) {
      const start = i * frameSize;
      const chunk = this.micBuffer.subarray(start, start + frameSize);
      const buffer = chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength);
      try {
        this.socket.send(buffer);
      } catch (error) {
        console.warn("发送麦克风音频失败", error);
        break;
      }
    }
    const remainder = this.micBuffer.length % frameSize;
    if (remainder > 0) {
      const start = this.micBuffer.length - remainder;
      this.micBuffer = this.micBuffer.slice(start);
    } else {
      this.micBuffer = new Int16Array(0);
    }
  }

  _downsampleBuffer(buffer, sampleRate, targetRate) {
    if (!buffer || buffer.length === 0) {
      return null;
    }
    if (targetRate === sampleRate) {
      return this._floatToInt16(buffer);
    }
    const sampleRateRatio = sampleRate / targetRate;
    const newLength = Math.round(buffer.length / sampleRateRatio);
    if (!Number.isFinite(newLength) || newLength <= 0) {
      return null;
    }
    const result = new Int16Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
      let accum = 0;
      let count = 0;
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
        accum += buffer[i];
        count += 1;
      }
      const sample = count > 0 ? accum / count : 0;
      result[offsetResult] = this._floatSampleToInt16(sample);
      offsetResult += 1;
      offsetBuffer = nextOffsetBuffer;
    }
    return result;
  }

  _floatToInt16(buffer) {
    const result = new Int16Array(buffer.length);
    for (let i = 0; i < buffer.length; i += 1) {
      result[i] = this._floatSampleToInt16(buffer[i]);
    }
    return result;
  }

  _floatSampleToInt16(sample) {
    const clamped = Math.max(-1, Math.min(1, sample));
    return clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }

  _concatInt16(current, append) {
    if (!current || current.length === 0) {
      return new Int16Array(append);
    }
    if (!append || append.length === 0) {
      return new Int16Array(current);
    }
    const result = new Int16Array(current.length + append.length);
    result.set(current, 0);
    result.set(append, current.length);
    return result;
  }

  _describeGetUserMediaError(error) {
    if (!error) {
      return "无法访问麦克风";
    }
    const name = error.name || "";
    if (name === "NotAllowedError" || name === "SecurityError") {
      return "麦克风权限被拒绝，请在浏览器中授权访问";
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return "未检测到麦克风设备或已被其他程序占用";
    }
    if (name === "OverconstrainedError" || name === "ConstraintNotSatisfiedError") {
      const constraint = error.constraint || (error.constraints && error.constraints[0]);
      if (constraint) {
        return `当前设备不支持所请求的麦克风参数 (${constraint})`;
      }
      return "当前设备不支持请求的麦克风参数";
    }
    if (name === "NotReadableError" || name === "TrackStartError") {
      return "浏览器无法访问麦克风，可能被系统或其他应用占用";
    }
    return error.message || "无法访问麦克风";
  }

  _playSpeaker(buffer) {
    const context = this.ensureContext();
    if (!context) return;
    if (context.state === "suspended") {
      context.resume().catch(() => {});
    }
    const pcm = new Int16Array(buffer);
    if (pcm.length === 0) return;

    const sampleRate = this.config.outputSampleRate || context.sampleRate;
    const audioBuffer = context.createBuffer(1, pcm.length, sampleRate);
    const channel = audioBuffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i += 1) {
      channel[i] = pcm[i] / 0x8000;
    }
    const source = context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(context.destination);

    const startTime = Math.max(this.nextPlaybackTime, context.currentTime + 0.01);
    source.start(startTime);
    this.nextPlaybackTime = startTime + audioBuffer.duration;
  }

  handleStatusFromServer(info) {
    if (!info) return;
    updateWebAudioView(info);
  }
}

function translateRuntime(runtime) {
  if (!runtime) return { status: "idle", text: "未启动" };
  if (runtime.running) {
    return { status: "running", text: "运行中" };
  }
  if (typeof runtime.exitCode === "number" && runtime.exitCode !== 0) {
    return { status: "error", text: "异常退出" };
  }
  if (runtime.exitCode === 0) {
    return { status: "idle", text: "已停止" };
  }
  return { status: "warning", text: "待机" };
}

function translateDeviceState(state) {
  switch (state) {
    case "listening":
      return "聆听中";
    case "speaking":
      return "播报中";
    case "idle":
      return "待命";
    case "connecting":
      return "连接中";
    default:
      return state || "-";
  }
}

function translateListeningMode(mode) {
  switch (mode) {
    case "realtime":
      return "实时";
    case "auto_stop":
      return "自动停止";
    case "manual":
      return "手动";
    default:
      return "-";
  }
}

function updateWebAudioView(info) {
  const el = $("#web-audio-status");
  if (!el) return;
  const data = info || {};
  const connected = Boolean(data.connected);
  const streaming = Boolean(
    data.microphoneStreaming || data.microphoneActive || data.streaming,
  );
  let text = data.statusText || "未连接";
  let stateName = "disconnected";
  if (connected && streaming) {
    text = data.statusText || "已连接·麦克风传输中";
    stateName = "streaming";
  } else if (connected) {
    text = data.statusText || "已连接";
    stateName = "connected";
  }
  el.textContent = text;
  el.dataset.state = stateName;
  if (data.lastMicrophoneAt) {
    el.dataset.lastMic = data.lastMicrophoneAt;
  } else {
    delete el.dataset.lastMic;
  }
}

function updateRuntimeView(payload) {
  const page = $(".page");
  const runtime = payload.runtime || {};
  const device = payload.application || {};
  const ui = payload.ui || {};
  const camera = payload.camera || {};
  const webAudio = payload.webAudio || {};

  const runtimeInfo = translateRuntime(runtime);
  page.dataset.runtime = runtimeInfo.status;
  $("#runtime-status").textContent = runtimeInfo.text;
  $("#runtime-message").textContent = runtime.message || runtimeInfo.text;

  $("#device-state").textContent = translateDeviceState(device.deviceState);
  $("#listening-mode").textContent = translateListeningMode(device.listeningMode);
  $("#keep-listening").textContent = device.keepListening ? "是" : "否";
  $("#audio-opened").textContent = device.audioOpened ? "已连接" : "未连接";

  $("#ui-status").textContent = ui.statusText || "等待连接...";
  $("#ui-text").textContent = ui.currentText || "尚未收到任何对话";
  $("#ui-button").textContent = `按钮状态：${ui.buttonText || "-"}`;
  $("#ui-emotion").textContent = ui.emotion || "-";

  const cameraStatus = $("#camera-status");
  cameraStatus.textContent = camera.status || "摄像头状态未知";
  cameraStatus.classList.toggle("pill-muted", !camera.active);

  updateWebAudioView(webAudio);
}

async function fetchStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error("无法获取状态");
    const data = await response.json();
    updateRuntimeView(data);
    audioClient?.handleStatusFromServer(data.webAudio);
  } catch (error) {
    toast(error.message || "获取状态失败", "error");
  }
}

function formatLog(entry) {
  const name = entry.name ? ` [${entry.name}]` : "";
  return `${entry.timestamp} [${entry.level}]${name} ${entry.message}`;
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

async function resetLogs() {
  try {
    await postJSON("/api/logs/reset");
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

function cameraPreview() {
  const img = $("#camera-preview");
  img.src = `/api/camera/preview?t=${Date.now()}`;
  img.onerror = () => {
    img.removeAttribute("src");
    toast("无法获取摄像头预览", "error");
  };
}

async function fetchCameraStatus() {
  try {
    const response = await fetch("/api/camera/status");
    if (!response.ok) throw new Error("无法获取摄像头状态");
    const data = await response.json();
    const cameraStatus = $("#camera-status");
    cameraStatus.textContent = data.status || "摄像头状态未知";
    cameraStatus.classList.toggle("pill-muted", !data.active);
  } catch (error) {
    toast(error.message || "刷新摄像头状态失败", "error");
  }
}

async function capturePhoto() {
  const question = $("#camera-question").value;
  try {
    const result = await postJSON("/api/camera/capture", { question });
    $("#camera-result").textContent = result.result || "已执行完成";
    cameraPreview();
    toast("拍照命令已发送", "success");
  } catch (error) {
    toast(error.message || "拍照失败", "error");
  }
}

async function ensureAudioReady() {
  if (!audioClient) {
    toast("音频通道未就绪", "error");
    return false;
  }
  const connected = await audioClient.waitUntilConnected();
  if (!connected) {
    toast("浏览器音频未连接", "error");
    return false;
  }
  try {
    await audioClient.startStreaming();
    return true;
  } catch (error) {
    toast(error.message || "无法启动麦克风", "error");
    return false;
  }
}

async function startManualConversation() {
  if (!(await ensureAudioReady())) return;
  try {
    await postJSON("/api/conversation/manual/start");
    toast("已开始手动聆听", "success");
  } catch (error) {
    audioClient?.stopStreaming();
    toast(error.message || "操作失败", "error");
  }
}

async function stopManualConversation() {
  audioClient?.stopStreaming();
  try {
    await postJSON("/api/conversation/manual/stop");
    toast("已结束手动聆听", "success");
  } catch (error) {
    toast(error.message || "操作失败", "error");
  }
}

async function startAutoConversation() {
  if (!(await ensureAudioReady())) return;
  try {
    await postJSON("/api/conversation/auto/start");
    toast("持续聆听已开启", "success");
  } catch (error) {
    audioClient?.stopStreaming();
    toast(error.message || "操作失败", "error");
  }
}

async function stopAutoConversation() {
  audioClient?.stopStreaming();
  try {
    await postJSON("/api/conversation/auto/stop");
    toast("持续聆听已关闭", "success");
  } catch (error) {
    toast(error.message || "操作失败", "error");
  }
}

async function abortSpeaking() {
  try {
    await postJSON("/api/conversation/abort");
    toast("已尝试打断播报", "success");
  } catch (error) {
    toast(error.message || "操作失败", "error");
  }
}

async function sendText() {
  const input = $("#text-input");
  const text = input.value.trim();
  if (!text) {
    toast("请输入要发送的文本", "warning");
    return;
  }
  try {
    await postJSON("/api/conversation/send-text", { text });
    toast("文本已发送", "success");
  } catch (error) {
    toast(error.message || "发送失败", "error");
  }
}

async function sendWake() {
  const input = $("#text-input");
  const text = input.value.trim() || "小智小智";
  try {
    await postJSON("/api/conversation/wake", { text });
    toast("已模拟唤醒", "success");
  } catch (error) {
    toast(error.message || "唤醒失败", "error");
  }
}

function clearText() {
  $("#text-input").value = "";
}

async function openCamera() {
  try {
    await postJSON("/api/camera/open");
    toast("摄像头已开启", "success");
    fetchCameraStatus();
    cameraPreview();
  } catch (error) {
    toast(error.message || "开启失败", "error");
  }
}

async function closeCamera() {
  try {
    await postJSON("/api/camera/close");
    toast("摄像头已关闭", "success");
    fetchCameraStatus();
  } catch (error) {
    toast(error.message || "关闭失败", "error");
  }
}

function bindEvents() {
  $("#manual-start").addEventListener("click", startManualConversation);
  $("#manual-stop").addEventListener("click", stopManualConversation);
  $("#auto-start").addEventListener("click", startAutoConversation);
  $("#auto-stop").addEventListener("click", stopAutoConversation);
  $("#abort-speaking").addEventListener("click", abortSpeaking);
  $("#send-text").addEventListener("click", sendText);
  $("#wake-now").addEventListener("click", sendWake);
  $("#clear-text").addEventListener("click", clearText);
  $("#clear-log-btn").addEventListener("click", resetLogs);
  $("#camera-open").addEventListener("click", openCamera);
  $("#camera-close").addEventListener("click", closeCamera);
  $("#camera-refresh").addEventListener("click", cameraPreview);
  $("#camera-status-refresh").addEventListener("click", fetchCameraStatus);
  $("#camera-capture").addEventListener("click", capturePhoto);

  document.addEventListener(
    "click",
    () => {
      audioClient?.unlock();
    },
    { once: true },
  );
}

function init() {
  audioClient = new WebAudioClient();
  audioClient.connect();

  bindEvents();
  fetchStatus();
  fetchLogs();
  fetchCameraStatus();
  state.statusTimer = setInterval(fetchStatus, 3000);
  state.logTimer = setInterval(fetchLogs, 2000);
}

document.addEventListener("DOMContentLoaded", init);
