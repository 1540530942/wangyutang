const elements = {
  statusBadge: document.getElementById("statusBadge"),
  singleButton: document.getElementById("singleButton"),
  screenshotButton: document.getElementById("screenshotButton"),
  faceButton: document.getElementById("faceButton"),
  continuousButton: document.getElementById("continuousButton"),
  downloadButton: document.getElementById("downloadButton"),
  intervalSelect: document.getElementById("intervalSelect"),
  gpioSelect: document.getElementById("gpioSelect"),
  refreshButton: document.getElementById("refreshButton"),
  cameraImage: document.getElementById("cameraImage"),
  emptyState: document.getElementById("emptyState"),
  deviceOnline: document.getElementById("deviceOnline"),
  cameraHealth: document.getElementById("cameraHealth"),
  captureSource: document.getElementById("captureSource"),
  updatedAt: document.getElementById("updatedAt"),
  contentLength: document.getElementById("contentLength"),
  deviceId: document.getElementById("deviceId"),
  frameId: document.getElementById("frameId"),
  gpioState: document.getElementById("gpioState"),
  gpioSampledAt: document.getElementById("gpioSampledAt"),
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
let latestMeta = null;
let lastUpdatedAt = 0;
let focusedTaskId = "";
let tickRunning = false;

function apiPath(path) {
  return `api/${path}`;
}

function selectedGpio() {
  return Number(elements.gpioSelect.value || 26);
}

function setMode(element, mode) {
  if (mode) {
    element.dataset.mode = mode;
  } else {
    delete element.dataset.mode;
  }
}

function setStatus(text, mode = "") {
  elements.statusBadge.textContent = text;
  setMode(elements.statusBadge, mode);
}

function setValue(element, value, mode = "") {
  const text = value || "-";
  element.textContent = text;
  element.title = text;
  setMode(element, mode);
}

function formatTime(seconds) {
  if (!seconds) return "-";
  return new Date(seconds * 1000).toLocaleString();
}

function formatAge(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "-";
  if (seconds < 3) return "刚刚";
  if (seconds < 60) return `${Math.round(seconds)} 秒前`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  return `${Math.round(seconds / 3600)} 小时前`;
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`${url} ${response.status}`);
  }
  return response.json();
}

function formatGpio(gpioMeta) {
  const fallbackGpio = selectedGpio();
  if (!gpioMeta) return `GPIO${fallbackGpio} 未上报`;
  const gpio = gpioMeta.gpio === 0 || gpioMeta.gpio ? `GPIO${gpioMeta.gpio}` : `GPIO${fallbackGpio}`;
  if (!gpioMeta.available) return `${gpio} 未采样`;
  const level = gpioMeta.level === "hi" ? "高电平" : gpioMeta.level === "lo" ? "低电平" : gpioMeta.level || "-";
  const value = gpioMeta.value === 0 || gpioMeta.value === 1 ? ` (${gpioMeta.value})` : "";
  return `${gpio} ${level}${value}`;
}

function applyGpioMeta(gpioMeta) {
  setValue(elements.gpioState, formatGpio(gpioMeta), gpioMeta && gpioMeta.available ? "ok" : "warn");
  setValue(elements.gpioSampledAt, gpioMeta && gpioMeta.sampled_at ? formatTime(gpioMeta.sampled_at) : "-");
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
  ].forEach((element) => setValue(element, "-"));
}

function setTaskButtonsBusy(isBusy) {
  elements.singleButton.disabled = isBusy;
  elements.screenshotButton.disabled = isBusy;
  elements.faceButton.disabled = isBusy;
}

function isTaskActive(task) {
  return task && !["complete", "expired", "stopped"].includes(task.status);
}

async function loadControl() {
  control = await getJson(apiPath("control"));
  const task = control.task;
  if (!task) {
    setValue(elements.taskState, "-");
    elements.continuousButton.textContent = "连续发送";
    elements.continuousButton.classList.remove("danger");
    return control;
  }

  const total = task.max_frames ? task.max_frames : "∞";
  const label = `${task.mode} ${task.status} ${task.uploaded_frames}/${total}`;
  const mode = task.status === "complete" ? "ok" : isTaskActive(task) ? "warn" : "";
  setValue(elements.taskState, label, mode);

  const activeContinuous = task.mode === "continuous" && isTaskActive(task);
  elements.continuousButton.textContent = activeContinuous ? "停止连续发送" : "连续发送";
  elements.continuousButton.classList.toggle("danger", activeContinuous);
  return control;
}

async function loadDeviceStatus() {
  try {
    const device = await getJson(apiPath("device"));
    if (device.online) {
      const age = formatAge(Number(device.age_seconds || 0));
      const label = device.device_id ? `${device.device_id} 在线，${age}` : `在线，${age}`;
      setValue(elements.deviceOnline, label, "ok");
    } else if (device.last_seen_at) {
      setValue(elements.deviceOnline, `离线，${formatAge(Number(device.age_seconds || 0))}`, "bad");
    } else {
      setValue(elements.deviceOnline, "未连接", "bad");
    }
    return device;
  } catch (error) {
    setValue(elements.deviceOnline, "状态接口失败", "bad");
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

    setValue(elements.inspectHost, inspection.hostname);
    setValue(elements.inspectTemp, tempLabel, Number.isFinite(temp) && temp < 70 ? "ok" : "warn");
    setValue(elements.inspectThrottle, throttleOk ? "正常" : throttled, throttleOk ? "ok" : "warn");
    setValue(elements.inspectWifi, inspection.wifi_ssid);
    setValue(elements.inspectIp, inspection.ip_address);
    setValue(elements.inspectGateway, inspection.gateway);
    setValue(elements.inspectUptime, inspection.uptime);
    setValue(elements.inspectDisk, inspection.disk);
    setValue(elements.inspectService, service, service === "active" ? "ok" : "bad");
    setValue(elements.inspectLoad, inspection.load_average);
    return inspection;
  } catch (error) {
    clearInspection();
    return null;
  }
}

function updateCameraHealth(meta) {
  if (!meta || !meta.has_image) {
    setValue(elements.cameraHealth, "暂无图片", "warn");
    setValue(elements.captureSource, "-");
    return;
  }

  const source = meta.capture_source || "unknown";
  const error = meta.capture_error || "";
  if (source === "screenshot-fallback") {
    setValue(elements.cameraHealth, error ? `相机异常：${error}` : "相机异常，已用截图兜底", "warn");
  } else if (source === "screenshot") {
    setValue(elements.cameraHealth, "收到屏幕截图，不代表相机正常", "warn");
  } else if (source === "face-screenshot") {
    setValue(elements.cameraHealth, "收到表情截图，不代表相机正常", "warn");
  } else if (source === "inspect") {
    setValue(elements.cameraHealth, "收到巡检截图，不代表相机正常", "warn");
  } else if (source === "server-cache") {
    setValue(elements.cameraHealth, "本地缓存图片", "warn");
  } else {
    setValue(elements.cameraHealth, "真实摄像头画面", "ok");
  }
  setValue(elements.captureSource, error ? `${source}: ${error}` : source, source === "screenshot-fallback" ? "warn" : "ok");
}

async function refreshLatest(forceImage = false) {
  try {
    const [meta, gpioMeta] = await Promise.all([
      getJson(apiPath("latest")),
      getJson(apiPath("gpio")).catch(() => null),
    ]);
    latestMeta = meta;

    if (!meta.has_image) {
      elements.emptyState.hidden = false;
      elements.cameraImage.hidden = true;
      setValue(elements.updatedAt, "-");
      setValue(elements.contentLength, "-");
      setValue(elements.deviceId, "-");
      setValue(elements.frameId, "-");
      updateCameraHealth(meta);
      applyGpioMeta(gpioMeta || meta.gpio || { gpio: selectedGpio(), available: false, sampled_at: 0 });
      setStatus(control.task && isTaskActive(control.task) ? "等待上传" : "无图片", control.task ? "warn" : "idle");
      return meta;
    }

    setValue(elements.deviceId, meta.device_id || "-");
    setValue(elements.frameId, meta.frame_id || "-");
    setValue(elements.updatedAt, `${formatTime(meta.updated_at)} (${formatAge(Date.now() / 1000 - Number(meta.updated_at || 0))})`);
    setValue(elements.contentLength, meta.content_length ? `${Math.round(meta.content_length / 1024)} KB` : "-");
    updateCameraHealth(meta);
    applyGpioMeta(gpioMeta || meta.gpio || meta.led1);

    elements.emptyState.hidden = true;
    elements.cameraImage.hidden = false;

    if (forceImage || meta.updated_at !== lastUpdatedAt) {
      lastUpdatedAt = meta.updated_at;
      elements.cameraImage.src = `${apiPath("latest.jpg")}?t=${Date.now()}`;
    }

    const ageSeconds = Date.now() / 1000 - Number(meta.updated_at || 0);
    if (meta.capture_source === "screenshot-fallback") {
      setStatus("相机异常", "warn");
    } else {
      setStatus(ageSeconds < 10 ? "画面已更新" : formatAge(ageSeconds), ageSeconds < 30 ? "ok" : "warn");
    }
    return meta;
  } catch (error) {
    setStatus("连接失败", "bad");
    setValue(elements.cameraHealth, "接口请求失败", "bad");
    return null;
  }
}

async function createTask(mode, extra = {}) {
  const device = await loadDeviceStatus().catch(() => null);
  if (!device || !device.online) {
    setStatus("树莓派离线，无法下发任务", "bad");
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
    if (["single", "screenshot", "face", "inspect"].includes(mode) && result.task && result.task.id) {
      focusedTaskId = result.task.id;
      await waitForTaskFrame(result.task.id);
      await loadInspection();
    } else {
      await refreshLatest(true);
    }
  } catch (error) {
    setStatus(`任务失败：${error.message}`, "bad");
  } finally {
    setTaskButtonsBusy(false);
  }
}

async function stopTask() {
  try {
    await getJson(apiPath("stop"), { method: "POST" });
    await loadControl();
    setStatus("已停止", "warn");
  } catch (error) {
    setStatus(`停止失败：${error.message}`, "bad");
  }
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

async function waitForTaskFrame(taskId) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await loadControl().catch(() => null);
    const meta = await refreshLatest(true);
    if (meta && meta.task_id === taskId) {
      focusedTaskId = "";
      setStatus("图片已更新", "ok");
      return meta;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  setStatus("等待上传超时", "warn");
  return null;
}

async function tick() {
  if (tickRunning) return;
  tickRunning = true;
  try {
    await loadControl();
    await loadDeviceStatus();
    await loadInspection();
    await refreshLatest(Boolean(focusedTaskId));
  } catch (error) {
    setStatus("控制接口失败", "bad");
  } finally {
    tickRunning = false;
  }
}

elements.singleButton.addEventListener("click", () => createTask("single"));
elements.screenshotButton.addEventListener("click", () => createTask("screenshot"));
elements.faceButton.addEventListener("click", () => createTask("face"));
elements.downloadButton.addEventListener("click", downloadLatestImage);
elements.continuousButton.addEventListener("click", async () => {
  const task = control.task;
  const activeContinuous = task && task.mode === "continuous" && isTaskActive(task);
  if (activeContinuous) {
    await stopTask();
    return;
  }
  await createTask("continuous", { interval_ms: Number(elements.intervalSelect.value) });
});
elements.refreshButton.addEventListener("click", () => refreshLatest(true));

tick();
setInterval(tick, 1000);
