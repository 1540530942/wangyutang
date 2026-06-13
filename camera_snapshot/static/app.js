const channelNames = {
  camera: "摄像机",
  screen: "屏幕",
  face: "表情",
};

const channelElements = {
  camera: {
    status: document.getElementById("cameraStatus"),
    image: document.getElementById("cameraImage"),
    empty: document.getElementById("cameraEmpty"),
    source: document.getElementById("cameraSource"),
    updatedAt: document.getElementById("cameraUpdatedAt"),
    contentLength: document.getElementById("cameraContentLength"),
    taskState: document.getElementById("cameraTaskState"),
  },
  screen: {
    status: document.getElementById("screenStatus"),
    image: document.getElementById("screenImage"),
    empty: document.getElementById("screenEmpty"),
    source: document.getElementById("screenSource"),
    updatedAt: document.getElementById("screenUpdatedAt"),
    contentLength: document.getElementById("screenContentLength"),
    taskState: document.getElementById("screenTaskState"),
  },
  face: {
    status: document.getElementById("faceStatus"),
    image: document.getElementById("faceImage"),
    empty: document.getElementById("faceEmpty"),
    source: document.getElementById("faceSource"),
    updatedAt: document.getElementById("faceUpdatedAt"),
    contentLength: document.getElementById("faceContentLength"),
    taskState: document.getElementById("faceTaskState"),
  },
};

const elements = {
  statusBadge: document.getElementById("statusBadge"),
  singleButton: document.getElementById("singleButton"),
  screenshotButton: document.getElementById("screenshotButton"),
  faceButton: document.getElementById("faceButton"),
  inspectButton: document.getElementById("inspectButton"),
  continuousButton: document.getElementById("continuousButton"),
  cameraDownloadButton: document.getElementById("cameraDownloadButton"),
  screenDownloadButton: document.getElementById("screenDownloadButton"),
  faceDownloadButton: document.getElementById("faceDownloadButton"),
  intervalSelect: document.getElementById("intervalSelect"),
  gpioSelect: document.getElementById("gpioSelect"),
  refreshButton: document.getElementById("refreshButton"),
  deviceOnline: document.getElementById("deviceOnline"),
  deviceId: document.getElementById("deviceId"),
  frameId: document.getElementById("frameId"),
  gpioState: document.getElementById("gpioState"),
  gpioSampledAt: document.getElementById("gpioSampledAt"),
  inspectHost: document.getElementById("inspectHost"),
  inspectTemp: document.getElementById("inspectTemp"),
  inspectCpu: document.getElementById("inspectCpu"),
  inspectCpuFreq: document.getElementById("inspectCpuFreq"),
  inspectMemory: document.getElementById("inspectMemory"),
  inspectThrottle: document.getElementById("inspectThrottle"),
  inspectWifi: document.getElementById("inspectWifi"),
  inspectIp: document.getElementById("inspectIp"),
  inspectGateway: document.getElementById("inspectGateway"),
  inspectUptime: document.getElementById("inspectUptime"),
  inspectDisk: document.getElementById("inspectDisk"),
  inspectService: document.getElementById("inspectService"),
  inspectLoad: document.getElementById("inspectLoad"),
};

let control = { task: null, tasks: {} };
let latestByKind = { camera: null, screen: null, face: null };
let lastUpdatedAt = { camera: 0, screen: 0, face: 0 };
let focusedTaskByKind = { camera: "", screen: "", face: "" };
let tickRunning = false;

function apiPath(path) {
  return `api/${path}`;
}

function selectedGpio() {
  return Number(elements.gpioSelect.value || 26);
}

function setMode(element, mode) {
  if (!element) return;
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
  if (!element) return;
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
    elements.inspectCpu,
    elements.inspectCpuFreq,
    elements.inspectMemory,
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

function isTaskActive(task) {
  return task && !["complete", "expired", "stopped", "failed"].includes(task.status);
}

function taskLabel(task) {
  if (!task) return "-";
  const total = task.max_frames ? task.max_frames : "∞";
  return `${task.mode} ${task.status} ${task.uploaded_frames}/${total}`;
}

function taskMode(task) {
  if (!task) return "";
  if (task.status === "complete") return "ok";
  if (task.status === "failed" || task.status === "expired") return "bad";
  if (isTaskActive(task)) return "warn";
  return "";
}

async function loadControl() {
  control = await getJson(apiPath("control"));
  const tasks = control.tasks || { camera: control.task };
  for (const kind of Object.keys(channelElements)) {
    const task = tasks[kind] || null;
    setValue(channelElements[kind].taskState, taskLabel(task), taskMode(task));
  }

  const cameraTask = tasks.camera;
  const activeContinuous = cameraTask && cameraTask.mode === "continuous" && isTaskActive(cameraTask);
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
    const cpuUsage = Number(inspection.cpu_usage_percent);
    const cpuUsageLabel = Number.isFinite(cpuUsage) ? `${cpuUsage.toFixed(1)}%` : "-";
    const cpuFrequency = inspection.cpu_frequency_mhz ? `${inspection.cpu_frequency_mhz} MHz` : "-";
    const throttled = inspection.throttled || "-";
    const throttleOk = typeof throttled === "string" && (throttled.includes("0x0") || throttled === "unavailable");
    const service = inspection.sender_service || "-";

    setValue(elements.inspectHost, inspection.hostname);
    setValue(elements.inspectTemp, tempLabel, Number.isFinite(temp) && temp < 70 ? "ok" : "warn");
    setValue(elements.inspectCpu, cpuUsageLabel, Number.isFinite(cpuUsage) && cpuUsage < 80 ? "ok" : "warn");
    setValue(elements.inspectCpuFreq, cpuFrequency);
    setValue(elements.inspectMemory, inspection.memory);
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

function sourceHealth(kind, meta) {
  if (!meta || !meta.has_image) {
    if (meta && meta.capture_error) return [`采集失败：${meta.capture_error}`, "bad"];
    return ["暂无图片", "warn"];
  }
  const source = meta.capture_source || "unknown";
  const error = meta.capture_error || "";
  if (kind === "camera") {
    if (error) return [`相机异常：${error}`, "bad"];
    if (["screenshot", "screenshot-fallback", "face-screenshot", "smile-face-render", "inspect"].includes(source)) {
      return ["来源异常，不是真相机帧", "bad"];
    }
    if (source === "server-cache") return ["缓存摄像机图", "warn"];
    return ["真实摄像头画面", "ok"];
  }
  if (kind === "screen") {
    return source === "inspect" ? ["巡检截图", "warn"] : ["桌面截图", "ok"];
  }
  if (source === "smile-face-render") return ["离屏渲染", "ok"];
  if (source === "face-screenshot") return ["旧版整屏表情截图", "warn"];
  return ["表情通道图片", "ok"];
}

async function refreshLatest(kind, forceImage = false) {
  const ui = channelElements[kind];
  try {
    const meta = await getJson(apiPath(`latest?kind=${kind}`));
    latestByKind[kind] = meta;

    if (!meta.has_image) {
      ui.empty.hidden = false;
      ui.image.hidden = true;
      setValue(ui.source, "-");
      setValue(ui.updatedAt, "-");
      setValue(ui.contentLength, "-");
      const [label, mode] = sourceHealth(kind, meta);
      setValue(ui.status, label, mode);
      return meta;
    }

    setValue(elements.deviceId, meta.device_id || "-");
    setValue(elements.frameId, meta.frame_id || "-");
    setValue(ui.source, meta.capture_error ? `${meta.capture_source}: ${meta.capture_error}` : meta.capture_source || "unknown", meta.capture_error ? "bad" : "ok");
    setValue(ui.updatedAt, `${formatTime(meta.updated_at)} (${formatAge(Date.now() / 1000 - Number(meta.updated_at || 0))})`);
    setValue(ui.contentLength, meta.content_length ? `${Math.round(meta.content_length / 1024)} KB` : "-");

    ui.empty.hidden = true;
    ui.image.hidden = false;

    if (forceImage || meta.updated_at !== lastUpdatedAt[kind]) {
      lastUpdatedAt[kind] = meta.updated_at;
      ui.image.src = `${apiPath(`latest.jpg?kind=${kind}`)}&t=${Date.now()}`;
    }

    const [label, mode] = sourceHealth(kind, meta);
    setValue(ui.status, label, mode);
    return meta;
  } catch (error) {
    setValue(ui.status, "接口失败", "bad");
    return null;
  }
}

async function refreshAll(forceImage = false) {
  const [cameraMeta, screenMeta, faceMeta, gpioMeta] = await Promise.all([
    refreshLatest("camera", forceImage),
    refreshLatest("screen", forceImage),
    refreshLatest("face", forceImage),
    getJson(apiPath("gpio")).catch(() => null),
  ]);
  const preferredMeta = cameraMeta && cameraMeta.has_image ? cameraMeta : screenMeta && screenMeta.has_image ? screenMeta : faceMeta;
  applyGpioMeta(gpioMeta || (preferredMeta && (preferredMeta.gpio || preferredMeta.led1)) || { gpio: selectedGpio(), available: false, sampled_at: 0 });
}

function setButtonsBusy(kind, isBusy) {
  if (kind === "camera") elements.singleButton.disabled = isBusy;
  if (kind === "screen") {
    elements.screenshotButton.disabled = isBusy;
    elements.inspectButton.disabled = isBusy;
  }
  if (kind === "face") elements.faceButton.disabled = isBusy;
}

async function createTask(kind, mode = "single", extra = {}) {
  const device = await loadDeviceStatus().catch(() => null);
  if (!device || !device.online) {
    setStatus("树莓派离线，无法下发任务", "bad");
    await loadControl().catch(() => null);
    await refreshAll(false).catch(() => null);
    return;
  }

  setButtonsBusy(kind, mode !== "continuous");
  applyGpioMeta({ gpio: selectedGpio(), available: false, sampled_at: 0 });
  setStatus(`${channelNames[kind]}任务已下发`, "warn");
  try {
    const result = await getJson(apiPath("capture"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, mode, query_gpio: selectedGpio(), ...extra }),
    });
    await loadControl();
    if (mode !== "continuous" && result.task && result.task.id) {
      focusedTaskByKind[kind] = result.task.id;
      await waitForTaskFrame(kind, result.task.id);
      await loadInspection();
    } else {
      await refreshLatest(kind, true);
    }
  } catch (error) {
    setStatus(`任务失败：${error.message}`, "bad");
  } finally {
    setButtonsBusy(kind, false);
  }
}

async function stopTask(kind) {
  try {
    await getJson(apiPath(`stop?kind=${kind}`), { method: "POST" });
    await loadControl();
    setStatus("已停止", "warn");
  } catch (error) {
    setStatus(`停止失败：${error.message}`, "bad");
  }
}

function downloadLatestImage(kind) {
  const meta = latestByKind[kind];
  if (!meta || !meta.has_image) {
    setStatus("暂无图片", "warn");
    return;
  }
  const timestamp = meta.updated_at ? new Date(meta.updated_at * 1000) : new Date();
  const stamp = timestamp.toISOString().replace(/[:.]/g, "-");
  const device = meta.device_id || "turbopi";
  const link = document.createElement("a");
  link.href = `${apiPath(`latest.jpg?kind=${kind}`)}&download=${Date.now()}`;
  link.download = `${device}-${kind}-${stamp}.jpg`;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function waitForTaskFrame(kind, taskId) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    await loadControl().catch(() => null);
    const meta = await refreshLatest(kind, true);
    if (meta && meta.task_id === taskId) {
      focusedTaskByKind[kind] = "";
      setStatus(`${channelNames[kind]}图片已更新`, "ok");
      return meta;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  setStatus(`${channelNames[kind]}等待上传超时`, "warn");
  return null;
}

async function tick() {
  if (tickRunning) return;
  tickRunning = true;
  try {
    await loadControl();
    await loadDeviceStatus();
    await loadInspection();
    await refreshAll(Object.values(focusedTaskByKind).some(Boolean));
  } catch (error) {
    setStatus("控制接口失败", "bad");
  } finally {
    tickRunning = false;
  }
}

elements.singleButton.addEventListener("click", () => createTask("camera", "single"));
elements.screenshotButton.addEventListener("click", () => createTask("screen", "single"));
elements.inspectButton.addEventListener("click", () => createTask("screen", "inspect"));
elements.faceButton.addEventListener("click", () => createTask("face", "single"));
elements.cameraDownloadButton.addEventListener("click", () => downloadLatestImage("camera"));
elements.screenDownloadButton.addEventListener("click", () => downloadLatestImage("screen"));
elements.faceDownloadButton.addEventListener("click", () => downloadLatestImage("face"));
elements.continuousButton.addEventListener("click", async () => {
  const task = control.tasks && control.tasks.camera;
  const activeContinuous = task && task.mode === "continuous" && isTaskActive(task);
  if (activeContinuous) {
    await stopTask("camera");
    return;
  }
  await createTask("camera", "continuous", { interval_ms: Number(elements.intervalSelect.value) });
});
elements.refreshButton.addEventListener("click", () => refreshAll(true));

tick();
setInterval(tick, 1000);
