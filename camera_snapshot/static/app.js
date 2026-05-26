const elements = {
  statusBadge: document.getElementById("statusBadge"),
  singleButton: document.getElementById("singleButton"),
  screenshotButton: document.getElementById("screenshotButton"),
  inspectButton: document.getElementById("inspectButton"),
  continuousButton: document.getElementById("continuousButton"),
  downloadButton: document.getElementById("downloadButton"),
  intervalSelect: document.getElementById("intervalSelect"),
  gpioSelect: document.getElementById("gpioSelect"),
  refreshButton: document.getElementById("refreshButton"),
  cameraImage: document.getElementById("cameraImage"),
  emptyState: document.getElementById("emptyState"),
  deviceId: document.getElementById("deviceId"),
  frameId: document.getElementById("frameId"),
  updatedAt: document.getElementById("updatedAt"),
  contentLength: document.getElementById("contentLength"),
  captureSource: document.getElementById("captureSource"),
  gpioState: document.getElementById("gpioState"),
  gpioSampledAt: document.getElementById("gpioSampledAt"),
  deviceOnline: document.getElementById("deviceOnline"),
  taskState: document.getElementById("taskState"),
  inspectHost: document.getElementById("inspectHost"),
  inspectTemp: document.getElementById("inspectTemp"),
  inspectThrottle: document.getElementById("inspectThrottle"),
  inspectWifi: document.getElementById("inspectWifi"),
  inspectIp: document.getElementById("inspectIp"),
  inspectGateway: document.getElementById("inspectGateway"),
  inspectUptime: document.getElementById("inspectUptime"),
  inspectDisk: document.getElementById("inspectDisk"),
  inspectService: document.getElementById("inspectService"),
  inspectLoad: document.getElementById("inspectLoad"),
};

let control = { task: null };
let lastUpdatedAt = 0;
let latestMeta = null;
let tickRunning = false;
let focusedTaskId = "";

function formatTime(seconds) {
  if (!seconds) return "-";
  return new Date(seconds * 1000).toLocaleString();
}

function setStatus(text, mode) {
  elements.statusBadge.textContent = text;
  elements.statusBadge.dataset.mode = mode;
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`${url} ${response.status}`);
  }
  return response.json();
}

function apiPath(path) {
  return `api/${path}`;
}

function selectedGpio() {
  return Number(elements.gpioSelect.value || 26);
}

function formatGpio(gpioMeta) {
  const fallbackGpio = selectedGpio();
  if (!gpioMeta) return `GPIO${fallbackGpio} 未采样`;
  const gpio = gpioMeta.gpio === 0 || gpioMeta.gpio ? `GPIO${gpioMeta.gpio}` : `GPIO${fallbackGpio}`;
  if (!gpioMeta.available) return `${gpio} 未采样`;
  const level = gpioMeta.level === "hi" ? "高电平" : gpioMeta.level === "lo" ? "低电平" : gpioMeta.level || "-";
  const value = gpioMeta.value === 0 || gpioMeta.value === 1 ? ` (${gpioMeta.value})` : "";
  return `${gpio} ${level}${value}`;
}

function applyGpioMeta(gpioMeta) {
  elements.gpioState.textContent = formatGpio(gpioMeta);
  elements.gpioSampledAt.textContent = gpioMeta && gpioMeta.sampled_at ? formatTime(gpioMeta.sampled_at) : "-";
}

function setInspectionValue(element, value, mode = "") {
  element.textContent = value || "-";
  if (mode) {
    element.dataset.mode = mode;
  } else {
    delete element.dataset.mode;
  }
}

function clearInspection() {
  [
    elements.inspectHost,
    elements.inspectTemp,
    elements.inspectThrottle,
    elements.inspectWifi,
    elements.inspectIp,
    elements.inspectGateway,
    elements.inspectUptime,
    elements.inspectDisk,
    elements.inspectService,
    elements.inspectLoad,
  ].forEach((element) => setInspectionValue(element, "-"));
}

function setTaskButtonsBusy(isBusy) {
  elements.singleButton.disabled = isBusy;
  elements.screenshotButton.disabled = isBusy;
  elements.inspectButton.disabled = isBusy;
}

async function loadControl() {
  control = await getJson(apiPath("control"));
  const task = control.task;
  if (task) {
    const total = task.max_frames ? task.max_frames : "∞";
    elements.taskState.textContent = `${task.mode} ${task.status} ${task.uploaded_frames}/${total}`;
    const activeContinuous = task.mode === "continuous" && !["complete", "expired", "stopped"].includes(task.status);
    elements.continuousButton.textContent = activeContinuous ? "停止持续发送" : "持续发送";
    elements.continuousButton.classList.toggle("danger", activeContinuous);
  } else {
    elements.taskState.textContent = "-";
    elements.continuousButton.textContent = "持续发送";
    elements.continuousButton.classList.remove("danger");
  }
}

async function loadDeviceStatus() {
  try {
    const device = await getJson(apiPath("device"));
    if (device.online) {
      const label = device.device_id ? `${device.device_id} 在线` : "在线";
      elements.deviceOnline.textContent = label;
      elements.deviceOnline.dataset.mode = "ok";
      return device;
    }
    elements.deviceOnline.textContent = device.last_seen_at ? "离线" : "未连接";
    elements.deviceOnline.dataset.mode = "bad";
    return device;
  } catch (error) {
    elements.deviceOnline.textContent = "状态未知";
    elements.deviceOnline.dataset.mode = "bad";
    return null;
  }
}

async function loadInspection() {
  try {
    const inspection = await getJson(apiPath("inspection"));
    if (!inspection.available) {
      clearInspection();
      return inspection;
    }
    const temp = Number(inspection.temperature_c);
    const tempLabel = Number.isFinite(temp) ? `${temp.toFixed(1)}°C` : "-";
    const throttled = inspection.throttled || "-";
    const throttleOk = typeof throttled === "string" && throttled.includes("0x0");
    const service = inspection.sender_service || "-";
    setInspectionValue(elements.inspectHost, inspection.hostname);
    setInspectionValue(elements.inspectTemp, tempLabel, Number.isFinite(temp) && temp < 70 ? "ok" : "warn");
    setInspectionValue(elements.inspectThrottle, throttleOk ? "正常" : throttled, throttleOk ? "ok" : "warn");
    setInspectionValue(elements.inspectWifi, inspection.wifi_ssid);
    setInspectionValue(elements.inspectIp, inspection.ip_address);
    setInspectionValue(elements.inspectGateway, inspection.gateway);
    setInspectionValue(elements.inspectUptime, inspection.uptime);
    setInspectionValue(elements.inspectDisk, inspection.disk);
    setInspectionValue(elements.inspectService, service, service === "active" ? "ok" : "bad");
    setInspectionValue(elements.inspectLoad, inspection.load_average);
    return inspection;
  } catch (error) {
    clearInspection();
    return null;
  }
}

async function createTask(mode, extra = {}) {
  const device = await loadDeviceStatus().catch(() => null);
  if (!device || !device.online) {
    setStatus("树莓派离线，无法发送", "bad");
    await loadControl().catch(() => null);
    await refreshLatest(false).catch(() => null);
    return;
  }
  setTaskButtonsBusy(mode !== "continuous");
  applyGpioMeta({ gpio: selectedGpio(), available: false, sampled_at: 0 });
  setStatus("任务已下发", "warn");
  try {
    const result = await getJson(apiPath("capture"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, query_gpio: selectedGpio(), ...extra }),
    });
    await loadControl();
    if (["single", "screenshot", "inspect"].includes(mode) && result.task && result.task.id) {
      focusedTaskId = result.task.id;
      await waitForTaskFrame(result.task.id);
      await loadInspection();
    } else {
      await refreshLatest(true);
    }
  } finally {
    setTaskButtonsBusy(false);
  }
}

async function stopTask() {
  await getJson(apiPath("stop"), { method: "POST" });
  await loadControl();
}

function downloadLatestImage() {
  if (!latestMeta || !latestMeta.has_image) {
    setStatus("暂无图片", "warn");
    return;
  }
  const timestamp = latestMeta.updated_at ? new Date(latestMeta.updated_at * 1000) : new Date();
  const stamp = timestamp.toISOString().replace(/[:.]/g, "-");
  const device = latestMeta.device_id || "turbopi";
  const link = document.createElement("a");
  link.href = `${apiPath("latest.jpg")}?download=${Date.now()}`;
  link.download = `${device}-${stamp}.jpg`;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function refreshLatest(forceImage) {
  try {
    const [meta, gpioMeta] = await Promise.all([
      getJson(apiPath("latest")),
      getJson(apiPath("gpio")).catch(() => null),
    ]);
    if (!meta.has_image) {
      setStatus(control.task ? "等待上传" : "空闲", control.task ? "warn" : "idle");
      elements.emptyState.hidden = false;
      elements.cameraImage.hidden = true;
      applyGpioMeta(gpioMeta || meta.gpio || { gpio: selectedGpio(), available: false, sampled_at: 0 });
      latestMeta = meta;
      return meta;
    }

    latestMeta = meta;
    elements.deviceId.textContent = meta.device_id || "-";
    elements.frameId.textContent = meta.frame_id || "-";
    elements.updatedAt.textContent = formatTime(meta.updated_at);
    elements.contentLength.textContent = meta.content_length ? `${Math.round(meta.content_length / 1024)} KB` : "-";
    const captureSource = meta.capture_source || "-";
    const captureError = meta.capture_error || "";
    elements.captureSource.textContent = captureError ? `${captureSource}: ${captureError}` : captureSource;
    if (captureSource === "screenshot-fallback") {
      elements.captureSource.dataset.mode = "warn";
    } else {
      delete elements.captureSource.dataset.mode;
    }
    applyGpioMeta(gpioMeta || meta.gpio || meta.led1);
    elements.emptyState.hidden = true;
    elements.cameraImage.hidden = false;

    if (forceImage || meta.updated_at !== lastUpdatedAt) {
      lastUpdatedAt = meta.updated_at;
      elements.cameraImage.src = `${apiPath("latest.jpg")}?t=${Date.now()}`;
    }

    const ageSeconds = Date.now() / 1000 - Number(meta.updated_at || 0);
    if (captureSource === "screenshot-fallback") {
      setStatus("相机不可用，已回退截图", "warn");
    } else {
      setStatus(ageSeconds < 5 ? "实时" : `${Math.round(ageSeconds)} 秒前`, ageSeconds < 5 ? "ok" : "warn");
    }
    return meta;
  } catch (error) {
    setStatus("连接失败", "bad");
    return null;
  }
}

async function waitForTaskFrame(taskId) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await loadControl().catch(() => null);
    const meta = await refreshLatest(true);
    if (meta && meta.task_id === taskId) {
      focusedTaskId = "";
      setStatus("已更新", "ok");
      return meta;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  setStatus("等待上传超时", "warn");
  return null;
}

elements.singleButton.addEventListener("click", () => createTask("single"));
elements.screenshotButton.addEventListener("click", () => createTask("screenshot"));
elements.inspectButton.addEventListener("click", () => createTask("inspect"));
elements.downloadButton.addEventListener("click", downloadLatestImage);
elements.continuousButton.addEventListener("click", async () => {
  const task = control.task;
  const activeContinuous = task && task.mode === "continuous" && !["complete", "expired", "stopped"].includes(task.status);
  if (activeContinuous) {
    await stopTask();
    return;
  }
  await createTask("continuous", { interval_ms: Number(elements.intervalSelect.value) });
});
elements.refreshButton.addEventListener("click", () => refreshLatest(true));

async function tick() {
  if (tickRunning) return;
  tickRunning = true;
  try {
    await loadControl();
    await loadDeviceStatus();
    await loadInspection();
    await refreshLatest(Boolean(focusedTaskId));
  } catch (error) {
    setStatus("控制失败", "bad");
  } finally {
    tickRunning = false;
  }
}

tick();
setInterval(tick, 800);
