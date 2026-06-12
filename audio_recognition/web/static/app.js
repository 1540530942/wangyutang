const $ = (selector) => document.querySelector(selector);

const statusEl = $("#status");
const modeTextEl = $("#modeText");
const modeWebBtn = $("#modeWebBtn");
const modeWonderBtn = $("#modeWonderBtn");
const webInputSection = $("#webInputSection");
const wonderSection = $("#wonderSection");
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

const REQUEST_TIMEOUT_MS = 90000;

let manualTimerHandle = null;
let currentInputMode = "wonderechopro";
let recordingStartedAt = 0;
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
  webInputSection?.classList.toggle("hidden", !isWeb);
  wonderSection?.classList.toggle("hidden", isWeb);
  modeTextEl.textContent = isWeb ? "网页输入" : "WonderEchoPro";
  modeWebBtn?.classList.toggle("active", isWeb);
  modeWonderBtn?.classList.toggle("active", !isWeb);
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
  const manualRecording = Boolean(settings.manual_recording_enabled);
  const actionSettingsFromHealth = action.health?.settings || {};
  if (Number.isFinite(Number(actionSettingsFromHealth.voice_volume_percent))) {
    actionSettings = { ...actionSettings, ...actionSettingsFromHealth };
    if (document.activeElement !== voiceVolumeInput) renderVoiceVolume(actionSettings.voice_volume_percent);
  }

  renderInputMode(inputMode);
  statusEl.textContent = latest ? `更新 ${fmtAge(data.age_seconds)}` : "等待语音";
  statusEl.classList.toggle("online", Boolean(latest));

  if (inputMode === "web_input") {
    audioStateEl.textContent = "网页输入";
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

  if (latest) {
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
