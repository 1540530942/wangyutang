const $ = (selector) => document.querySelector(selector);

const statusEl = $("#status");
const modeTextEl = $("#modeText");
const modeWebBtn = $("#modeWebBtn");
const modeWonderBtn = $("#modeWonderBtn");
const modeVadBtn = $("#modeVadBtn");
const webInputSection = $("#webInputSection");
const wonderSection = $("#wonderSection");
const vadSection = $("#vadSection");
const audioStateEl = $("#audioState");
const actionStateEl = $("#actionState");
const deviceStateEl = $("#deviceState");
const cameraStateEl = $("#cameraState");
const latestTextEl = $("#latestText");
const latestMetaEl = $("#latestMeta");
const latestSkillEl = $("#latestSkill");
const resultsEl = $("#results");
const eventsEl = $("#events");
const tasksEl = $("#tasks");
const recordBtn = $("#recordBtn");
const stopBtn = $("#stopBtn");
const uploadBtn = $("#uploadBtn");
const sendTextBtn = $("#sendTextBtn");
const clearTasksBtn = $("#clearTasksBtn");
const openCameraBtn = $("#openCameraBtn");
const closeCameraBtn = $("#closeCameraBtn");
const showResultsBtn = $("#showResultsBtn");
const showEventsBtn = $("#showEventsBtn");
const fileInput = $("#fileInput");
const webTextInput = $("#webTextInput");
const timerEl = $("#timer");
const previewEl = $("#preview");
const asrResultEl = $("#asrResult");
const asrRawEl = $("#asrRaw");
const meter = $("#meter");
const meterContext = meter?.getContext("2d");
const cameraImageEl = $("#cameraImage");
const cameraPlaceholderEl = $("#cameraPlaceholder");
const cameraMetaEl = $("#cameraMeta");
const voiceVolumeInput = $("#voiceVolumeInput");
const voiceVolumeText = $("#voiceVolumeText");
const saveVoiceVolumeBtn = $("#saveVoiceVolumeBtn");
const startVadBtn = $("#startVadBtn");
const stopVadBtn = $("#stopVadBtn");
const vadLiveStateEl = $("#vadLiveState");
const vadLevelBar = $("#vadLevelBar");

const REQUEST_TIMEOUT_MS = 90000;
const VAD_WS_URL = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/interact/ws/audio`;
const VAD_DEVICE_ID = "web-vad-asr";
const VAD_TARGET_RATE = 16000;
const VAD_THRESHOLD = 0.018;
const VAD_START_FRAMES = 3;
const VAD_END_FRAMES = 20;
const VAD_PRE_FRAMES = 8;
const VAD_MAX_SECONDS = 12;

let manualTimerHandle = null;
let currentInputMode = "wonderechopro";
let recordingStartedAt = 0;
let vadRuntime = null;
let vadRealtimeResult = null;
let actionSettings = {
  unit_distance_cm: 5,
  turn_angle_deg: 5,
  sensitivity: 1,
  voice_volume_percent: 90,
};

async function api(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function postJson(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function fmtTime(value) {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

function fmtAge(seconds) {
  if (!Number.isFinite(seconds)) return "-";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} 秒前`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  return `${Math.round(seconds / 3600)} 小时前`;
}

function statusText(status) {
  const names = {
    enabled: "已开启",
    disabled: "已关闭",
    pending: "等待",
    claimed: "已领取",
    running: "执行中",
    complete: "完成",
    completed: "完成",
    failed: "失败",
    rejected: "拒绝",
    expired: "过期",
    ok: "正常",
    empty: "空识别",
    skipped: "跳过",
    bypass: "旁路",
    manual: "手动",
    stopped: "已停止",
  };
  return names[status] || status || "-";
}

function formatElapsed(ms) {
  const totalSeconds = ms / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.floor(totalSeconds % 60);
  const tenths = Math.floor((totalSeconds % 1) * 10);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

function drawMeter(level = 0) {
  if (!meter || !meterContext) return;
  meterContext.clearRect(0, 0, meter.width, meter.height);
  meterContext.fillStyle = "#eef3f1";
  meterContext.fillRect(0, 0, meter.width, meter.height);
  meterContext.fillStyle = "#1f7a68";
  meterContext.fillRect(0, meter.height - level * meter.height, meter.width, level * meter.height);
}

function setVadLevel(level = 0) {
  if (!vadLevelBar) return;
  vadLevelBar.style.width = `${Math.max(0, Math.min(1, level)) * 100}%`;
}

function setVadLiveState(text, active = false) {
  if (vadLiveStateEl) vadLiveStateEl.textContent = text;
  if (startVadBtn) startVadBtn.disabled = active;
  if (stopVadBtn) stopVadBtn.disabled = !active;
}

function renderVadRealtimeResult() {
  if (!vadRealtimeResult) {
    asrResultEl.value = "";
    asrRawEl.textContent = "{}";
    return;
  }
  asrResultEl.value = vadRealtimeResult.route_text || vadRealtimeResult.text || "";
  asrRawEl.textContent = JSON.stringify(vadRealtimeResult, null, 2);
}

function startManualTimer() {
  recordingStartedAt = performance.now();
  if (manualTimerHandle) window.clearInterval(manualTimerHandle);
  manualTimerHandle = window.setInterval(() => {
    timerEl.textContent = formatElapsed(performance.now() - recordingStartedAt);
  }, 100);
}

function stopManualTimer() {
  if (manualTimerHandle) window.clearInterval(manualTimerHandle);
  manualTimerHandle = null;
}

function setPreviewSource(url) {
  if (!previewEl) return;
  if (!url) {
    previewEl.removeAttribute("src");
    previewEl.load();
    return;
  }
  const absoluteUrl = url.startsWith("http") ? url : new URL(url, window.location.href).toString();
  if (previewEl.src !== absoluteUrl) {
    previewEl.src = absoluteUrl;
    previewEl.load();
  }
}

function renderVoiceVolume(value) {
  const volume = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
  voiceVolumeInput.value = String(volume);
  voiceVolumeText.textContent = `${volume}%`;
}

async function loadActionSettings() {
  const data = await api("/action/api/settings");
  actionSettings = { ...actionSettings, ...(data.settings || {}) };
  renderVoiceVolume(actionSettings.voice_volume_percent);
}

async function saveVoiceVolume() {
  const volume = Math.max(0, Math.min(100, Number(voiceVolumeInput.value || 0)));
  const payload = { ...actionSettings, voice_volume_percent: volume };
  const data = await postJson("/action/api/settings", payload);
  actionSettings = { ...actionSettings, ...(data.settings || payload) };
  renderVoiceVolume(actionSettings.voice_volume_percent);
  statusEl.textContent = `播报音量已保存为 ${Math.round(actionSettings.voice_volume_percent)}%`;
}

async function callAsr(blob, filename = "recording.wav") {
  const form = new FormData();
  form.append("language", "zh");
  form.append("file", blob, filename);
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  statusEl.textContent = "ASR 识别中";
  try {
    const response = await fetch("./api/asr/transcribe", {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    const text = await response.text();
    let data = null;
    try {
      data = JSON.parse(text);
    } catch {
      data = { text };
    }
    if (!response.ok) throw new Error(data?.detail ? JSON.stringify(data.detail) : text);
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("识别超时，请缩短音频后再试");
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function routeTranscript(asrData, wavPath = "") {
  const data = await postJson("./api/recognize-text", {
    device_id: "web-audio",
    text: asrData.text || "",
    wav_path: wavPath,
    source: "web-recording",
    route_action: true,
    raw: { asr: asrData },
  });
  asrResultEl.value = data.result?.text || asrData.text || "";
  asrRawEl.textContent = JSON.stringify(data, null, 2);
  await refresh();
}

async function transcribeFile(file) {
  if (previewEl) previewEl.src = URL.createObjectURL(file);
  const asrData = await callAsr(file, file.name || "recording.wav");
  await routeTranscript(asrData, file.name || "");
}

async function sendTextCommand() {
  const text = webTextInput?.value?.trim() || "";
  if (!text) throw new Error("请输入网页指令文本");
  statusEl.textContent = "正在发送文本指令";
  const data = await postJson("./api/recognize-text", {
    device_id: "web-text",
    text,
    wav_path: "",
    source: "web-text-input",
    route_action: true,
    raw: { input_mode: "web_input" },
  });
  asrResultEl.value = data.result?.text || text;
  asrRawEl.textContent = JSON.stringify(data, null, 2);
  await refresh();
  if (data.action_error || data.face_error) {
    statusEl.textContent = `指令派发失败：${data.action_error || data.face_error}`;
  } else if (data.action_task || data.face_task) {
    statusEl.textContent = `已派发文本指令：${data.skill?.id || "已匹配"}`;
  } else if (!data.skill) {
    statusEl.textContent = "未匹配到可执行动作，请换一种说法";
  }
}

function downsampleBuffer(samples, sourceRate, targetRate) {
  if (sourceRate === targetRate) return samples;
  const ratio = sourceRate / targetRate;
  const outputLength = Math.max(1, Math.round(samples.length / ratio));
  const output = new Float32Array(outputLength);
  for (let index = 0; index < outputLength; index += 1) {
    const sourceIndex = index * ratio;
    const left = Math.floor(sourceIndex);
    const right = Math.min(left + 1, samples.length - 1);
    const fraction = sourceIndex - left;
    output[index] = samples[left] * (1 - fraction) + samples[right] * fraction;
  }
  return output;
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeString = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (const sample of samples) {
    const value = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, value < 0 ? value * 0x8000 : value * 0x7fff, true);
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

async function sendVadSegment(wavBlob) {
  const sessionId = crypto.randomUUID ? crypto.randomUUID().slice(0, 12) : String(Date.now());
  const socket = new WebSocket(VAD_WS_URL);
  socket.binaryType = "arraybuffer";

  await new Promise((resolve, reject) => {
    socket.onopen = resolve;
    socket.onerror = () => reject(new Error("VAD_ASR WebSocket 连接失败"));
  });

  socket.send(JSON.stringify({ type: "start", session_id: sessionId, device_id: VAD_DEVICE_ID }));
  await new Promise((resolve, reject) => {
    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "ready") resolve();
        else reject(new Error(`VAD_ASR 握手失败: ${event.data}`));
      } catch (error) {
        reject(error);
      }
    };
    socket.onerror = () => reject(new Error("VAD_ASR WebSocket 握手失败"));
  });

  const bytes = new Uint8Array(await wavBlob.arrayBuffer());
  for (let offset = 0; offset < bytes.length; offset += 8192) socket.send(bytes.slice(offset, offset + 8192));
  socket.send(JSON.stringify({ type: "end" }));

  const result = await new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error("VAD_ASR 识别超时")), REQUEST_TIMEOUT_MS);
    socket.onmessage = (event) => {
      window.clearTimeout(timeout);
      try {
        resolve(JSON.parse(event.data));
      } catch {
        resolve({ raw: event.data });
      }
    };
    socket.onerror = () => {
      window.clearTimeout(timeout);
      reject(new Error("VAD_ASR WebSocket 识别失败"));
    };
  });
  socket.close();
  return result;
}

async function handleVadSegment(frames, sampleRate) {
  if (!frames.length) return;
  const totalLength = frames.reduce((sum, frame) => sum + frame.length, 0);
  const merged = new Float32Array(totalLength);
  let offset = 0;
  for (const frame of frames) {
    merged.set(frame, offset);
    offset += frame.length;
  }
  const downsampled = downsampleBuffer(merged, sampleRate, VAD_TARGET_RATE);
  const wavBlob = encodeWav(downsampled, VAD_TARGET_RATE);
  setVadLiveState("正在识别...", true);
  const result = await sendVadSegment(wavBlob);
  vadRealtimeResult = result;
  renderVadRealtimeResult();
  statusEl.textContent = `VAD_ASR: ${result.status || "ok"}`;
  await refresh();
  if (vadRuntime) setVadLiveState("持续接收中", true);
}

async function startVadAsr() {
  if (vadRuntime) return;
  vadRealtimeResult = null;
  renderVadRealtimeResult();
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前浏览器不支持麦克风采集");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  const audioContext = new AudioContext();
  await audioContext.resume();
  const source = audioContext.createMediaStreamSource(stream);
  const processor = audioContext.createScriptProcessor(2048, 1, 1);
  const runtime = {
    audioContext,
    source,
    processor,
    stream,
    preFrames: [],
    speechFrames: [],
    speechCount: 0,
    silenceCount: 0,
    speaking: false,
    sending: false,
    startedAt: 0,
  };
  vadRuntime = runtime;

  processor.onaudioprocess = (event) => {
    if (!vadRuntime) return;
    const input = event.inputBuffer.getChannelData(0);
    const frame = new Float32Array(input);
    const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / frame.length);
    setVadLevel(Math.min(1, rms / (VAD_THRESHOLD * 3)));
    const isSpeech = rms >= VAD_THRESHOLD;

    if (!runtime.speaking) {
      runtime.preFrames.push(frame);
      if (runtime.preFrames.length > VAD_PRE_FRAMES) runtime.preFrames.shift();
      runtime.speechCount = isSpeech ? runtime.speechCount + 1 : 0;
      if (runtime.speechCount >= VAD_START_FRAMES) {
        runtime.speaking = true;
        runtime.startedAt = performance.now();
        runtime.speechFrames = [...runtime.preFrames];
        runtime.silenceCount = 0;
        setVadLiveState("检测到语音", true);
      }
      return;
    }

    runtime.speechFrames.push(frame);
    runtime.silenceCount = isSpeech ? 0 : runtime.silenceCount + 1;
    const durationSeconds = (performance.now() - runtime.startedAt) / 1000;
    const shouldEnd = runtime.silenceCount >= VAD_END_FRAMES || durationSeconds >= VAD_MAX_SECONDS;
    if (shouldEnd && !runtime.sending) {
      const frames = runtime.speechFrames;
      runtime.speaking = false;
      runtime.sending = true;
      runtime.speechFrames = [];
      runtime.preFrames = [];
      runtime.speechCount = 0;
      runtime.silenceCount = 0;
      handleVadSegment(frames, audioContext.sampleRate)
        .catch(showError)
        .finally(() => {
          runtime.sending = false;
          if (vadRuntime) setVadLiveState("持续接收中", true);
        });
    }
  };

  source.connect(processor);
  processor.connect(audioContext.destination);
  setVadLiveState("持续接收中", true);
  statusEl.textContent = "VAD_ASR 持续接收中";
}

function stopVadAsr() {
  if (!vadRuntime) return;
  const runtime = vadRuntime;
  vadRuntime = null;
  runtime.processor.disconnect();
  runtime.source.disconnect();
  runtime.stream.getTracks().forEach((track) => track.stop());
  runtime.audioContext.close().catch(() => {});
  setVadLevel(0);
  setVadLiveState("已停止", false);
  statusEl.textContent = "VAD_ASR 已停止";
}

function itemRow(primary, secondary, code, className = "item") {
  const row = document.createElement("div");
  row.className = className;
  row.innerHTML = `<div><strong>${primary}</strong><span>${secondary}</span></div><code>${code}</code>`;
  return row;
}

function renderInputMode(mode) {
  currentInputMode = mode || "wonderechopro";
  const isWeb = currentInputMode === "web_input";
  const isWonder = currentInputMode === "wonderechopro";
  const isVad = currentInputMode === "vad_asr";
  webInputSection?.classList.toggle("hidden", !isWeb);
  wonderSection?.classList.toggle("hidden", !isWonder);
  vadSection?.classList.toggle("hidden", !isVad);
  modeTextEl.textContent = isWeb ? "网页输入" : isVad ? "VAD_ASR" : "WonderEchoPro";
  modeWebBtn?.classList.toggle("active", isWeb);
  modeWonderBtn?.classList.toggle("active", isWonder);
  modeVadBtn?.classList.toggle("active", isVad);
}

function renderDashboard(data) {
  const settings = data.settings || {};
  const action = data.action || {};
  const camera = data.camera || {};
  const device = action.health?.device || {};
  const latest = data.latest;
  const latestEvent = data.latest_event;
  const latestTask = action.tasks?.[0];
  const cameraLatest = camera.latest || {};
  const cameraTask = camera.control?.task;
  const inputMode = settings.input_mode || "wonderechopro";
  const latestMatchesInputMode = inputMode !== "vad_asr";
  const displayLatest = latest && latestMatchesInputMode;
  const manualRecording = Boolean(settings.manual_recording_enabled);
  const actionSettingsFromHealth = action.health?.settings || {};
  if (Number.isFinite(Number(actionSettingsFromHealth.voice_volume_percent))) {
    actionSettings = { ...actionSettings, ...actionSettingsFromHealth };
    if (document.activeElement !== voiceVolumeInput) renderVoiceVolume(actionSettings.voice_volume_percent);
  }

  renderInputMode(inputMode);
  statusEl.textContent = inputMode === "vad_asr"
    ? vadRuntime
      ? "VAD_ASR 持续接收中"
      : vadRealtimeResult
        ? "VAD_ASR 已识别"
        : "等待 VAD_ASR"
    : displayLatest
      ? `更新 ${fmtAge(data.age_seconds)}`
      : "等待语音";
  statusEl.classList.toggle("online", Boolean(displayLatest || (inputMode === "vad_asr" && (vadRuntime || vadRealtimeResult))));

  if (inputMode === "web_input") {
    audioStateEl.textContent = "网页输入";
  } else if (inputMode === "vad_asr") {
    audioStateEl.textContent = "VAD_ASR";
  } else {
    audioStateEl.textContent = manualRecording ? "采集中" : "待采集";
  }
  if (latestEvent) audioStateEl.textContent = statusText(latestEvent.status);

  recordBtn.disabled = inputMode !== "wonderechopro" ? true : manualRecording;
  stopBtn.disabled = inputMode !== "wonderechopro" ? true : !manualRecording;
  if (manualRecording && !manualTimerHandle) startManualTimer();
  if (!manualRecording && manualTimerHandle) stopManualTimer();
  if (!manualRecording) {
    const latestDuration = Number(latest?.audio_duration_seconds || 0);
    timerEl.textContent = latestDuration > 0 ? formatElapsed(latestDuration * 1000) : "00:00.0";
  }

  actionStateEl.textContent = action.online ? "在线" : "离线";
  deviceStateEl.textContent = device.online ? (device.status || "在线") : "离线";
  cameraStateEl.textContent = camera.online && camera.health?.device?.online ? "在线" : camera.online ? "待画面" : "离线";

  if (displayLatest) {
    latestTextEl.textContent = latest.text || "无文本";
    latestSkillEl.textContent = latest.skill_id || "未匹配";
    latestMetaEl.textContent = `${latest.device_id || ""} | ${fmtTime(latest.reported_at)} | ${latest.wav_path || ""}`;
    setPreviewSource(latest.audio_url || "");
    asrResultEl.value = latest.text || "";
    asrRawEl.textContent = JSON.stringify(
      {
        ...latest.raw,
        audio_url: latest.audio_url || "",
        audio_duration_seconds: latest.audio_duration_seconds || 0,
      },
      null,
      2
    );
  } else if (inputMode === "web_input") {
    latestTextEl.textContent = "暂无识别内容";
    latestSkillEl.textContent = "未匹配";
    latestMetaEl.textContent = "当前是网页输入模式，可以直接发送文本或上传音频。";
    setPreviewSource("");
  } else if (inputMode === "vad_asr") {
    latestTextEl.textContent = "等待 VAD_ASR 唤醒";
    latestSkillEl.textContent = "你好瓦力";
    latestMetaEl.textContent = "Pi 端 Silero VAD 分段上传到 /interact/ws/audio；唤醒后识别结果会展示在这里。";
    setPreviewSource("");
    renderVadRealtimeResult();
  } else {
    latestTextEl.textContent = "暂无识别内容";
    latestSkillEl.textContent = "未匹配";
    latestMetaEl.textContent = manualRecording
      ? "树莓派正在采集 WonderEchoPro 音频。"
      : "点击开始采集后，树莓派会录制一段 WonderEchoPro 音频。";
    setPreviewSource("");
  }

  if (latestTask?.status === "complete") actionStateEl.textContent = "最近完成";
  if (latestTask?.status === "running" || latestTask?.status === "claimed") actionStateEl.textContent = "执行中";

  tasksEl.innerHTML = "";
  if (action.error) {
    tasksEl.appendChild(itemRow("动作服务不可用", action.error, "error", "item danger-row"));
  } else if (!action.tasks?.length) {
    tasksEl.appendChild(itemRow("暂无动作任务", "识别到运动指令后会出现在这里", "idle"));
  } else {
    for (const task of action.tasks.slice(0, 8)) {
      tasksEl.appendChild(
        itemRow(
          task.name_zh || task.skill_id || "动作",
          `${task.source || ""} | ${fmtTime(task.requested_at)} | ${task.error || task.output || "等待回报"}`,
          statusText(task.status),
          `item task-${task.status || "pending"}`
        )
      );
    }
  }

  const cameraOpen = cameraTask && !["complete", "expired", "stopped"].includes(cameraTask.status);
  const hasImage = Boolean(cameraLatest.has_image);
  cameraImageEl.style.display = hasImage && cameraOpen ? "block" : "none";
  cameraPlaceholderEl.style.display = hasImage && cameraOpen ? "none" : "grid";
  if (hasImage && cameraOpen) cameraImageEl.src = `./api/camera/latest.jpg?t=${Date.now()}`;
  cameraPlaceholderEl.textContent = cameraOpen ? "等待摄像头上传画面" : "摄像头未打开";
  cameraMetaEl.textContent = camera.error
    ? `摄像头服务不可用: ${camera.error}`
    : `${cameraOpen ? "已打开" : "已关闭"} | ${cameraLatest.device_id || "无设备"} | ${fmtTime(cameraLatest.updated_at)}`;

  resultsEl.innerHTML = "";
  for (const result of (data.results || []).slice(0, 12)) {
    resultsEl.appendChild(
      itemRow(
        result.text || "无文本",
        `${result.device_id || ""} | ${fmtTime(result.reported_at)}`,
        result.skill_id || "未匹配",
        result.skill_id ? "item command-row" : "item"
      )
    );
  }

  eventsEl.innerHTML = "";
  for (const event of (data.events || []).slice(0, 12)) {
    eventsEl.appendChild(itemRow(event.stage || "event", event.message || "", statusText(event.status), `item event-${event.status || "ok"}`));
  }
}

async function refresh() {
  const data = await api("./api/dashboard");
  renderDashboard(data);
}

function showError(error) {
  statusEl.textContent = error.message || String(error);
}

$("#refreshBtn").addEventListener("click", () => refresh().catch(showError));

modeWebBtn?.addEventListener("click", () => {
  stopVadAsr();
  modeWebBtn.disabled = true;
  postJson("./api/settings", {
    input_mode: "web_input",
    manual_recording_enabled: false,
  })
    .then(refresh)
    .catch(showError)
    .finally(() => {
      modeWebBtn.disabled = false;
    });
});

modeWonderBtn?.addEventListener("click", () => {
  stopVadAsr();
  modeWonderBtn.disabled = true;
  postJson("./api/settings", {
    input_mode: "wonderechopro",
    manual_recording_enabled: false,
  })
    .then(refresh)
    .catch(showError)
    .finally(() => {
      modeWonderBtn.disabled = false;
    });
});

modeVadBtn?.addEventListener("click", () => {
  vadRealtimeResult = null;
  renderVadRealtimeResult();
  modeVadBtn.disabled = true;
  postJson("./api/settings", {
    input_mode: "vad_asr",
    manual_recording_enabled: false,
  })
    .then(refresh)
    .catch(showError)
    .finally(() => {
      modeVadBtn.disabled = false;
    });
});

startVadBtn?.addEventListener("click", () => {
  startVadAsr().catch(showError);
});

stopVadBtn?.addEventListener("click", () => {
  stopVadAsr();
});

clearTasksBtn.addEventListener("click", () => {
  clearTasksBtn.disabled = true;
  postJson("./api/tasks/clear")
    .then(refresh)
    .catch(showError)
    .finally(() => {
      clearTasksBtn.disabled = false;
    });
});

openCameraBtn.addEventListener("click", () => {
  openCameraBtn.disabled = true;
  postJson("./api/camera/open")
    .then(refresh)
    .catch(showError)
    .finally(() => {
      openCameraBtn.disabled = false;
    });
});

closeCameraBtn.addEventListener("click", () => {
  closeCameraBtn.disabled = true;
  postJson("./api/camera/close")
    .then(refresh)
    .catch(showError)
    .finally(() => {
      closeCameraBtn.disabled = false;
    });
});

recordBtn.addEventListener("click", () => {
  recordBtn.disabled = true;
  statusEl.textContent = "正在开启采集";
  postJson("./api/manual-recording/start")
    .then(refresh)
    .catch((error) => {
      recordBtn.disabled = false;
      showError(error);
    });
});

stopBtn.addEventListener("click", () => {
  stopBtn.disabled = true;
  statusEl.textContent = "正在停止采集";
  postJson("./api/manual-recording/stop")
    .then(refresh)
    .catch((error) => {
      stopBtn.disabled = false;
      showError(error);
    });
});

uploadBtn.addEventListener("click", () => {
  const file = fileInput.files?.[0];
  if (!file) {
    statusEl.textContent = "请选择音频文件";
    return;
  }
  uploadBtn.disabled = true;
  transcribeFile(file)
    .catch(showError)
    .finally(() => {
      uploadBtn.disabled = false;
    });
});

sendTextBtn?.addEventListener("click", () => {
  sendTextBtn.disabled = true;
  sendTextCommand()
    .catch(showError)
    .finally(() => {
      sendTextBtn.disabled = false;
    });
});

voiceVolumeInput?.addEventListener("input", () => {
  renderVoiceVolume(voiceVolumeInput.value);
});

saveVoiceVolumeBtn?.addEventListener("click", () => {
  saveVoiceVolume().catch(showError);
});

showResultsBtn.addEventListener("click", () => {
  showResultsBtn.classList.add("active");
  showEventsBtn.classList.remove("active");
  resultsEl.classList.remove("hidden");
  eventsEl.classList.add("hidden");
});

showEventsBtn.addEventListener("click", () => {
  showEventsBtn.classList.add("active");
  showResultsBtn.classList.remove("active");
  eventsEl.classList.remove("hidden");
  resultsEl.classList.add("hidden");
});

drawMeter();
loadActionSettings().catch(console.error);
refresh().catch(showError);
window.setInterval(() => refresh().catch(console.error), 2000);
