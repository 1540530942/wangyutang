const statusEl = document.querySelector("#deviceStatus");
const deviceInfoEl = document.querySelector("#deviceInfo");
const tasksEl = document.querySelector("#tasks");
const unitSummaryEl = document.querySelector("#unitSummary");
const sonarSummaryEl = document.querySelector("#sonarSummary");
const sonarDistanceValueEl = document.querySelector("#sonarDistanceValue");
const sonarMetaEl = document.querySelector("#sonarMeta");
const sonarRawEl = document.querySelector("#sonarRaw");
const sonarCard = document.querySelector("#sonarCard");
const moveUnitLabelEl = document.querySelector("#moveUnitLabel");
const turnUnitLabelEl = document.querySelector("#turnUnitLabel");
const voiceVolumeOnBtn = document.querySelector("#voiceVolumeOnBtn");
const voiceVolumeOffBtn = document.querySelector("#voiceVolumeOffBtn");
const unitDistanceInput = document.querySelector("#unitDistanceInput");
const turnAngleInput = document.querySelector("#turnAngleInput");
const sensitivityInput = document.querySelector("#sensitivityInput");
const rgbSummaryEl = document.querySelector("#rgbSummary");
const rgbSwatchEl = document.querySelector("#rgbSwatch");
const rgbColorInput = document.querySelector("#rgbColorInput");
const rgbRedInput = document.querySelector("#rgbRedInput");
const rgbGreenInput = document.querySelector("#rgbGreenInput");
const rgbBlueInput = document.querySelector("#rgbBlueInput");
const cameraPreviewToggle = document.querySelector("#cameraPreviewToggle");
const cameraPreview = document.querySelector("#cameraPreview");
const cameraPreviewImage = document.querySelector("#cameraPreviewImage");
const cameraPreviewStatus = document.querySelector("#cameraPreviewStatus");
const cameraSymbol = document.querySelector("#cameraSymbol");
const cameraCompareCanvas = document.querySelector("#cameraCompareCanvas");
const buttons = [...document.querySelectorAll("[data-action]")];

let refreshTimer = null;
let cameraTimer = null;
let sonarTimer = null;
let lastCameraPixels = null;
let lastCameraFrameId = "";
let lastCameraPulseAt = 0;
let lastCameraObjectUrl = "";
let lastSonarReportAt = 0;
let lastSonarRenderAt = 0;
let lastRenderedDistanceTaskId = "";
let lastVoiceVolumePercent = 30;
let settingsDirty = false;
let lastTouchActionAt = 0;
let currentSettings = {
  unit_distance_cm: 1,
  turn_angle_deg: 5,
  sensitivity: 0.5,
  voice_volume_percent: 0,
  rgb_red: 0,
  rgb_green: 0,
  rgb_blue: 0,
};
const settingsInputs = [unitDistanceInput, turnAngleInput, sensitivityInput, rgbRedInput, rgbGreenInput, rgbBlueInput];
const MOTION_ACTIONS = new Set([
  "move_forward",
  "move_backward",
  "move_left",
  "move_right",
  "turn_left",
  "turn_right",
]);
const DISTANCE_ACTIONS = new Set(["front_distance"]);
const ACTIVE_STATUSES = new Set(["pending", "claimed", "running"]);
const CAMERA_HEARTBEAT_MS = 10000;
const CAMERA_POLL_MS = 2000;
const CAMERA_DIFF_THRESHOLD = 0.035;
const SONAR_POLL_MS = 2000;
const REFRESH_IDLE_MS = 1000;
const REFRESH_ACTIVE_MS = 350;
let refreshIntervalMs = 0;

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text || response.statusText;
    try {
      const payload = JSON.parse(text);
      message = payload.detail || payload.error || message;
    } catch {
      // Keep the raw response text when the server did not return JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

function fmtNumber(value) {
  const number = Number(value);
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function fmtDistance(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (Number.isInteger(number)) return String(number);
  return number.toFixed(1);
}

function fmtTime(value) {
  if (!value) return "";
  return new Date(value * 1000).toLocaleTimeString();
}

function fmtSonarTimestamp(value) {
  if (!value) return "";
  return new Date(value * 1000).toLocaleTimeString();
}

function clampRgb(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(255, Math.round(number)));
}

function rgbToHex(red, green, blue) {
  return [red, green, blue].map((value) => clampRgb(value).toString(16).padStart(2, "0")).join("");
}

function hexToRgb(hex) {
  const clean = String(hex || "#000000").replace("#", "");
  if (!/^[0-9a-fA-F]{6}$/.test(clean)) return { red: 0, green: 0, blue: 0 };
  return {
    red: parseInt(clean.slice(0, 2), 16),
    green: parseInt(clean.slice(2, 4), 16),
    blue: parseInt(clean.slice(4, 6), 16),
  };
}

function fmtLatency(task) {
  const claim = task.claim_latency_seconds;
  const done = task.completion_latency_seconds;
  if (done !== null && done !== undefined) return `${done}s`;
  if (claim !== null && claim !== undefined) return `${claim}s claimed`;
  return "";
}

function statusText(task) {
  const latency = fmtLatency(task);
  return latency ? `${task.status} · ${latency}` : task.status;
}

function renderDeviceInfo(device) {
  const parts = [];
  if (device.hostname) parts.push(`主机 ${device.hostname}`);
  if (device.ip_address) parts.push(`IP ${device.ip_address}`);
  if (device.wifi_ssid) parts.push(`Wi-Fi ${device.wifi_ssid}`);
  if (device.gateway) parts.push(`网关 ${device.gateway}`);
  if (!parts.length) parts.push("暂无网络信息");
  deviceInfoEl.textContent = parts.join(" · ");
}

function setCameraStatus(text) {
  cameraPreviewStatus.textContent = text;
}

function setSonarStatus(text) {
  sonarSummaryEl.textContent = text;
}

function flashCameraSymbol() {
  lastCameraPulseAt = Date.now();
  cameraSymbol.classList.add("active");
  window.setTimeout(() => cameraSymbol.classList.remove("active"), 1200);
}

function loadImageBitmap(blob) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
      resolve({ image, url });
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("camera image load failed"));
    };
    image.src = url;
  });
}

async function readCameraPixels(image) {
  const canvas = cameraCompareCanvas;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return context.getImageData(0, 0, canvas.width, canvas.height).data;
}

function changedRatio(previous, current) {
  if (!previous || previous.length !== current.length) return 1;
  let changed = 0;
  const pixels = current.length / 4;
  for (let index = 0; index < current.length; index += 4) {
    const diff =
      Math.abs(previous[index] - current[index]) +
      Math.abs(previous[index + 1] - current[index + 1]) +
      Math.abs(previous[index + 2] - current[index + 2]);
    if (diff > 72) changed += 1;
  }
  return changed / pixels;
}

async function monitorCameraOnce() {
  if (!cameraPreviewToggle.checked) return;
  const latest = await api("/camera/api/latest");
  if (!latest.has_image) {
    setCameraStatus("相机暂无图像");
    return;
  }

  const frameId = String(latest.frame_id || latest.updated_at || "");
  const now = Date.now();
  const heartbeat = now - lastCameraPulseAt >= CAMERA_HEARTBEAT_MS;
  const timeText = latest.updated_at ? new Date(latest.updated_at * 1000).toLocaleTimeString() : "";

  if (frameId && frameId === lastCameraFrameId) {
    if (heartbeat) flashCameraSymbol();
    setCameraStatus(`最新帧 ${latest.frame_id || "-"} · 未更新 · ${timeText}`);
    return;
  }

  const imageResponse = await fetch(`/camera/api/latest.jpg?t=${Date.now()}`, { cache: "no-store" });
  if (!imageResponse.ok) throw new Error(`camera image ${imageResponse.status}`);
  const blob = await imageResponse.blob();
  const { image, url } = await loadImageBitmap(blob);
  const pixels = await readCameraPixels(image);
  const ratio = changedRatio(lastCameraPixels, pixels);
  const significant = Boolean(lastCameraPixels) && ratio >= CAMERA_DIFF_THRESHOLD;
  const firstFrame = !lastCameraPixels;

  if (firstFrame || significant || heartbeat || frameId !== lastCameraFrameId) {
    if (lastCameraObjectUrl) URL.revokeObjectURL(lastCameraObjectUrl);
    lastCameraObjectUrl = url;
    cameraPreviewImage.src = url;
  } else {
    URL.revokeObjectURL(url);
  }

  if (firstFrame || significant || heartbeat) {
    flashCameraSymbol();
  }

  lastCameraPixels = new Uint8ClampedArray(pixels);
  lastCameraFrameId = frameId;
  const percent = (ratio * 100).toFixed(1);
  setCameraStatus(`最新帧 ${latest.frame_id || "-"} · 变化 ${percent}% · ${timeText}`);
}

function setCameraPreviewEnabled(enabled) {
  cameraPreview.classList.toggle("disabled", !enabled);
  if (!enabled) {
    setCameraStatus("未开启");
    if (cameraTimer) clearInterval(cameraTimer);
    cameraTimer = null;
    return;
  }
  setCameraStatus("正在检查相机画面");
  monitorCameraOnce().catch((error) => setCameraStatus(`相机检查失败 ${error.message}`));
  if (cameraTimer) clearInterval(cameraTimer);
  cameraTimer = setInterval(() => {
    monitorCameraOnce().catch((error) => setCameraStatus(`相机检查失败 ${error.message}`));
  }, CAMERA_POLL_MS);
}

function setBusy(action, busy) {
  for (const button of buttons) {
    if (button.dataset.action === action) button.classList.toggle("busy", busy);
  }
}

function sonarEventTime(data = {}) {
  return Number(data.reported_at || data.completed_at || data.sampled_at || 0);
}

function renderSonar(data = {}, options = {}) {
  const eventTime = sonarEventTime(data);
  if (!options.force && lastSonarRenderAt && (!eventTime || eventTime < lastSonarRenderAt)) {
    return;
  }
  if (eventTime) {
    lastSonarRenderAt = Math.max(lastSonarRenderAt, eventTime);
  }
  const available = Boolean(data.available);
  const distance = data.front_distance_estimate_cm;
  const confidence = Number(data.confidence);
  const source = String(data.source || "");
  const deviceId = String(data.device_id || "");
  const reportedAt = Number(data.reported_at || 0);
  const sampledAt = Number(data.sampled_at || 0);
  const raw = String(data.raw || "");
  const distanceText = distance === null || distance === undefined ? "--" : `${fmtDistance(distance)}`;
  sonarDistanceValueEl.textContent = distanceText;
  sonarCard.classList.toggle("sonar-available", available);
  sonarCard.classList.toggle("sonar-unavailable", !available);
  if (available && Number.isFinite(distance)) {
    setSonarStatus(`有效 ${fmtDistance(distance)} cm`);
  } else if (source || raw) {
    setSonarStatus("暂不可用");
  } else {
    setSonarStatus("等待上报");
  }
  const detailParts = [];
  if (source) detailParts.push(`来源 ${source}`);
  if (deviceId) detailParts.push(`设备 ${deviceId}`);
  if (reportedAt) detailParts.push(`上报 ${fmtSonarTimestamp(reportedAt)}`);
  if (sampledAt) detailParts.push(`采样 ${fmtSonarTimestamp(sampledAt)}`);
  if (Number.isFinite(confidence) && confidence > 0) detailParts.push(`置信度 ${(confidence * 100).toFixed(0)}%`);
  sonarMetaEl.textContent = detailParts.length ? detailParts.join(" · ") : "等待设备上报超声波结果";
  sonarRawEl.textContent = raw ? `raw: ${raw}` : "";
}

function renderSonarFromTask(task) {
  if (task.id && task.id === lastRenderedDistanceTaskId) return;
  const output = String(task.output || "");
  const distanceMatch = output.match(/front_distance_estimate_cm=([0-9.]+)/);
  if (!distanceMatch) return;
  const rawMatch = output.match(/raw_mm_samples=([0-9,]+)/);
  const confidenceMatch = output.match(/confidence=([0-9.]+)/);
  if (task.id) lastRenderedDistanceTaskId = task.id;
  renderSonar({
    available: true,
    front_distance_estimate_cm: Number(distanceMatch[1]),
    confidence: confidenceMatch ? Number(confidenceMatch[1]) : 0,
    source: "action-task-log",
    device_id: task.device_id || "",
    reported_at: task.completed_at || task.updated_at || task.requested_at || 0,
    raw: rawMatch ? rawMatch[1] : "",
  }, { force: true });
}

async function refreshSonar() {
  try {
    const sonar = await api("/camera/api/sonar");
    lastSonarReportAt = Number(sonar.reported_at || 0);
    renderSonar(sonar);
  } catch (error) {
    setSonarStatus("读取失败");
    if (!lastSonarRenderAt) {
      sonarMetaEl.textContent = `超声波数据读取失败：${error.message}`;
      sonarRawEl.textContent = "";
      sonarDistanceValueEl.textContent = "--";
      sonarCard.classList.remove("sonar-available");
      sonarCard.classList.add("sonar-unavailable");
    }
  }
}

function setActionLocked(actionIds, locked) {
  for (const button of buttons) {
    const action = button.dataset.action;
    if (actionIds.has(action)) {
      button.disabled = locked;
    }
  }
}

function setMotionLocked(locked) {
  setActionLocked(MOTION_ACTIONS, locked);
}

function getFormSettings() {
  return {
    unit_distance_cm: Number(unitDistanceInput.value || 1),
    turn_angle_deg: Number(turnAngleInput.value || 5),
    sensitivity: Number(sensitivityInput.value || 0.5),
    voice_volume_percent: Number(currentSettings.voice_volume_percent || 0),
    rgb_red: clampRgb(rgbRedInput.value),
    rgb_green: clampRgb(rgbGreenInput.value),
    rgb_blue: clampRgb(rgbBlueInput.value),
  };
}

function isEditingSettings() {
  return settingsDirty || settingsInputs.includes(document.activeElement);
}

function renderSettings(settings, options = {}) {
  const updateInputs = options.updateInputs !== false;
  currentSettings = { ...currentSettings, ...settings };
  if (currentSettings.voice_volume_percent > 0) {
    lastVoiceVolumePercent = currentSettings.voice_volume_percent;
  }
  if (updateInputs) {
    unitDistanceInput.value = fmtNumber(currentSettings.unit_distance_cm);
    turnAngleInput.value = fmtNumber(currentSettings.turn_angle_deg);
    sensitivityInput.value = fmtNumber(currentSettings.sensitivity);
    rgbRedInput.value = clampRgb(currentSettings.rgb_red);
    rgbGreenInput.value = clampRgb(currentSettings.rgb_green);
    rgbBlueInput.value = clampRgb(currentSettings.rgb_blue);
  }
  const displaySettings = updateInputs ? currentSettings : { ...currentSettings, ...getFormSettings() };
  const red = clampRgb(displaySettings.rgb_red);
  const green = clampRgb(displaySettings.rgb_green);
  const blue = clampRgb(displaySettings.rgb_blue);
  const hex = `#${rgbToHex(red, green, blue)}`;
  const voiceVolume = Math.max(0, Math.min(100, Math.round(Number(displaySettings.voice_volume_percent || 0))));
  rgbColorInput.value = hex;
  rgbSwatchEl.style.backgroundColor = hex;
  rgbSummaryEl.textContent = red || green || blue ? `当前 ${hex.toUpperCase()} · R${red} G${green} B${blue}` : "默认关闭";
  unitSummaryEl.textContent = `距离 ${fmtNumber(displaySettings.unit_distance_cm)} cm · 转向 ${fmtNumber(displaySettings.turn_angle_deg)}° · 灵敏度 ${fmtNumber(displaySettings.sensitivity)}x`;
  moveUnitLabelEl.textContent = `按一次执行 ${fmtNumber(displaySettings.unit_distance_cm)} cm`;
  if (turnUnitLabelEl) {
    turnUnitLabelEl.textContent = `按一次转 ${fmtNumber(displaySettings.turn_angle_deg)}°`;
  }
  voiceVolumeOnBtn.disabled = voiceVolume > 0;
  voiceVolumeOffBtn.disabled = voiceVolume <= 0;
  voiceVolumeOnBtn.textContent = "打开音量";
  voiceVolumeOffBtn.textContent = voiceVolume > 0 ? "关闭音量" : "音量已关";
  voiceVolumeOnBtn.title = voiceVolume > 0 ? `当前音量 ${voiceVolume}%` : `恢复到 ${fmtNumber(lastVoiceVolumePercent || 90)}%`;
  voiceVolumeOffBtn.title = voiceVolume > 0 ? "将音量设为 0%" : "当前已关闭";
}

async function loadSettings() {
  const data = await api("./api/settings");
  renderSettings(data.settings || {});
}

async function saveSettings() {
  const payload = getFormSettings();
  const data = await api("./api/settings", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  settingsDirty = false;
  renderSettings(data.settings || payload);
  await refresh();
}

async function setVoiceVolume(enabled) {
  const nextVolume = enabled
    ? (currentSettings.voice_volume_percent > 0 ? currentSettings.voice_volume_percent : lastVoiceVolumePercent || 30)
    : 0;
  if (enabled && nextVolume > 0) {
    lastVoiceVolumePercent = nextVolume;
  }
  currentSettings = { ...currentSettings, voice_volume_percent: nextVolume };
  settingsDirty = false;
  await saveSettings();
}

async function refresh() {
  const health = await api("./api/health");
  if (health.settings) {
    if (isEditingSettings()) {
      currentSettings = { ...currentSettings, ...health.settings };
      renderSettings(getFormSettings(), { updateInputs: false });
    } else {
      renderSettings(health.settings);
    }
  }
  const device = health.device || {};
  statusEl.textContent = device.online
    ? `在线 ${device.device_id || ""} · ${device.status || "idle"}`
    : `离线 ${Math.round(device.age_seconds || 0)}s`;
  statusEl.classList.toggle("online", Boolean(device.online));
  statusEl.classList.toggle("running", device.status === "running");
  renderDeviceInfo(device);

  const data = await api("./api/tasks");
  tasksEl.innerHTML = "";
  const activeMotion = data.tasks.some((task) => MOTION_ACTIONS.has(task.skill_id) && ACTIVE_STATUSES.has(task.status));
  const activeDistance = data.tasks.some((task) => DISTANCE_ACTIONS.has(task.skill_id) && ACTIVE_STATUSES.has(task.status));
  const activeTask = data.tasks.some((task) => ACTIVE_STATUSES.has(task.status));
  const latestDistanceTask = data.tasks.find(
    (task) => task.skill_id === "front_distance" && task.status === "complete" && task.output,
  );
  if (
    latestDistanceTask
    && latestDistanceTask.id !== lastRenderedDistanceTaskId
    && Number(latestDistanceTask.completed_at || 0) >= lastSonarReportAt
  ) {
    renderSonarFromTask(latestDistanceTask);
  }
  if (activeTask && refreshIntervalMs !== REFRESH_ACTIVE_MS) {
    scheduleFastRefresh(REFRESH_ACTIVE_MS);
  } else if (!activeTask && refreshIntervalMs !== REFRESH_IDLE_MS) {
    scheduleFastRefresh(REFRESH_IDLE_MS);
  }
  setMotionLocked(activeMotion);
  setActionLocked(DISTANCE_ACTIONS, activeDistance);
  for (const task of data.tasks.slice(0, 12)) {
    const row = document.createElement("div");
    row.className = `task task-${task.status}`;
    const distance = task.unit_distance_cm ? `${fmtNumber(task.unit_distance_cm)}cm` : "";
    const angle = task.turn_angle_deg ? `${fmtNumber(task.turn_angle_deg)}°` : "";
    row.innerHTML = `
      <div>
        <strong>${task.name_zh || task.skill_id}</strong>
        <code>${task.id}</code>
        <div>${fmtTime(task.requested_at)} ${distance} ${angle}</div>
      </div>
      <div>${statusText(task)}</div>
    `;
    if (task.output || task.error) {
      const log = document.createElement("pre");
      log.className = "task-log";
      log.textContent = task.error ? `${task.output || ""}\n${task.error}`.trim() : task.output;
      row.appendChild(log);
    }
    tasksEl.appendChild(row);
  }
}

function scheduleFastRefresh(intervalMs = REFRESH_IDLE_MS) {
  if (refreshTimer && refreshIntervalMs === intervalMs) return;
  if (refreshTimer) clearInterval(refreshTimer);
  refreshIntervalMs = intervalMs;
  refreshTimer = setInterval(() => {
    refresh().catch((error) => console.error(error));
  }, intervalMs);
}

function scheduleSonarRefresh() {
  if (sonarTimer) clearInterval(sonarTimer);
  refreshSonar().catch((error) => {
    sonarSummaryEl.textContent = `读取失败 ${error.message}`;
  });
  sonarTimer = setInterval(() => {
    refreshSonar().catch((error) => {
      sonarSummaryEl.textContent = `读取失败 ${error.message}`;
    });
  }, SONAR_POLL_MS);
}

async function createTask(action) {
  const button = buttons.find((item) => item.dataset.action === action);
  if (button && button.disabled) return;
  let verificationCode = "";
  if (action === "remote_shutdown") {
    verificationCode = window.prompt("请输入远程关机验证码（提示：123）");
    if (verificationCode === null) return;
    if (verificationCode !== "123") {
      statusEl.textContent = "验证码错误，已取消远程关机";
      return;
    }
  }
  setBusy(action, true);
  try {
    if (action === "rgb_on" && settingsDirty) {
      await saveSettings();
    }
    const data = await api("./api/tasks", {
      method: "POST",
      body: JSON.stringify({
        action,
        source:
          action === "emergency_stop"
            ? "web-emergency"
            : action === "remote_shutdown"
              ? "web-shutdown"
              : "web",
        verification_code: verificationCode,
      }),
    });
    if (data.task) {
      statusEl.textContent = `${data.task.name_zh || data.task.skill_id} 已下发`;
    }
    scheduleFastRefresh(REFRESH_ACTIVE_MS);
    await refresh();
  } catch (error) {
    statusEl.textContent = `发送失败 ${error.message}`;
    statusEl.classList.remove("online");
  } finally {
    window.setTimeout(() => setBusy(action, false), 350);
  }
}

function releasePressed(button) {
  button.classList.remove("pressed");
}

buttons.forEach((button) => {
  button.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "touch" || event.pointerType === "pen") {
      button.classList.add("pressed");
    }
  });
  button.addEventListener("pointerup", (event) => {
    if (event.pointerType === "touch" || event.pointerType === "pen") {
      lastTouchActionAt = Date.now();
      releasePressed(button);
      event.preventDefault();
      button.blur();
      createTask(button.dataset.action);
    }
  });
  button.addEventListener("pointercancel", () => releasePressed(button));
  button.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "touch" || event.pointerType === "pen") releasePressed(button);
  });
  button.addEventListener("click", (event) => {
    if (Date.now() - lastTouchActionAt < 700) {
      event.preventDefault();
      return;
    }
    createTask(button.dataset.action);
  });
});

document.querySelector("#refreshBtn").addEventListener("click", refresh);
document.querySelector("#saveSettingsBtn").addEventListener("click", (event) => {
  event.preventDefault();
  saveSettings().catch((error) => {
    statusEl.textContent = `保存失败 ${error.message}`;
  });
});
document.querySelector("#settingsForm").addEventListener("submit", (event) => {
  event.preventDefault();
  saveSettings().catch((error) => {
    statusEl.textContent = `保存失败 ${error.message}`;
  });
});
settingsInputs.forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
    renderSettings(getFormSettings(), { updateInputs: false });
  });
});
rgbColorInput.addEventListener("input", () => {
  const rgb = hexToRgb(rgbColorInput.value);
  rgbRedInput.value = rgb.red;
  rgbGreenInput.value = rgb.green;
  rgbBlueInput.value = rgb.blue;
  settingsDirty = true;
  renderSettings(getFormSettings(), { updateInputs: false });
});
document.querySelector("#rgbSettingsForm").addEventListener("submit", (event) => {
  event.preventDefault();
  saveSettings().catch((error) => {
    statusEl.textContent = `保存失败 ${error.message}`;
  });
});
cameraPreviewToggle.addEventListener("change", () => {
  setCameraPreviewEnabled(cameraPreviewToggle.checked);
});
voiceVolumeOnBtn.addEventListener("click", () => {
  setVoiceVolume(true).catch((error) => {
    statusEl.textContent = `保存失败 ${error.message}`;
  });
});
voiceVolumeOffBtn.addEventListener("click", () => {
  setVoiceVolume(false).catch((error) => {
    statusEl.textContent = `保存失败 ${error.message}`;
  });
});

loadSettings()
  .then(refresh)
  .catch((error) => {
    statusEl.textContent = `检查失败 ${error.message}`;
  });
scheduleFastRefresh();
scheduleSonarRefresh();
