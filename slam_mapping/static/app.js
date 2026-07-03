const canvas = document.getElementById("mapCanvas");
const ctx = canvas.getContext("2d");
const statusBadge = document.getElementById("statusBadge");

const fields = {
  x: document.getElementById("metricX"),
  y: document.getElementById("metricY"),
  yaw: document.getElementById("metricYaw"),
  distance: document.getElementById("metricDistance"),
  map: document.getElementById("metricMap"),
  occupied: document.getElementById("metricOccupied"),
  source: document.getElementById("metricSource"),
  updated: document.getElementById("metricUpdated"),
};

function setStatus(text, cls) {
  statusBadge.textContent = text;
  statusBadge.className = `badge ${cls || ""}`.trim();
}

function fmt(value, digits = 2) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return value.toFixed(digits);
}

function worldToCanvas(x, y, map) {
  const origin = map.origin || { x_m: -3, y_m: -3 };
  const size = map.size || 1;
  const res = map.resolution_m || 0.05;
  const widthM = size * res;
  const nx = (x - origin.x_m) / widthM;
  const ny = (y - origin.y_m) / widthM;
  return {
    x: nx * canvas.width,
    y: canvas.height - ny * canvas.height,
  };
}

function drawGrid(map) {
  const size = map.size || 0;
  const values = map.values || [];
  ctx.fillStyle = "#eef2f6";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!size || !values.length) return 0;

  const cell = canvas.width / size;
  let occupied = 0;
  for (let row = 0; row < size; row += 1) {
    const line = values[row] || [];
    for (let col = 0; col < size; col += 1) {
      const value = line[col];
      if (value === 100) {
        occupied += 1;
        ctx.fillStyle = "#202833";
      } else if (value === 0) {
        ctx.fillStyle = "#ffffff";
      } else {
        ctx.fillStyle = "#d8dee7";
      }
      ctx.fillRect(col * cell, row * cell, Math.ceil(cell), Math.ceil(cell));
    }
  }

  ctx.strokeStyle = "rgba(100, 116, 139, 0.32)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= size; i += Math.max(1, Math.round(size / 12))) {
    const p = Math.round(i * cell) + 0.5;
    ctx.beginPath();
    ctx.moveTo(p, 0);
    ctx.lineTo(p, canvas.height);
    ctx.moveTo(0, p);
    ctx.lineTo(canvas.width, p);
    ctx.stroke();
  }
  return occupied;
}

function drawTrail(trail, map) {
  if (!Array.isArray(trail) || trail.length < 2) return;
  ctx.strokeStyle = "#d97706";
  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  ctx.beginPath();
  trail.forEach((point, index) => {
    const p = worldToCanvas(point.x_m || 0, point.y_m || 0, map);
    if (index === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.stroke();
}

function drawRobot(pose, map) {
  const position = worldToCanvas(pose.x_m || 0, pose.y_m || 0, map);
  const yaw = ((pose.yaw_deg || 0) * Math.PI) / 180;
  const radius = 18;
  const arrow = 46;

  ctx.fillStyle = "#0f766e";
  ctx.beginPath();
  ctx.arc(position.x, position.y, radius, 0, Math.PI * 2);
  ctx.fill();

  ctx.strokeStyle = "#0f766e";
  ctx.lineWidth = 6;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(position.x, position.y);
  ctx.lineTo(position.x + Math.cos(yaw) * arrow, position.y - Math.sin(yaw) * arrow);
  ctx.stroke();

  ctx.fillStyle = "#2563eb";
  ctx.beginPath();
  ctx.arc(position.x + Math.cos(yaw) * arrow, position.y - Math.sin(yaw) * arrow, 7, 0, Math.PI * 2);
  ctx.fill();
}

function fallbackMap(state) {
  const config = state.config || {};
  const sizeM = config.size_m || 6;
  const resolution = config.resolution_m || 0.05;
  const size = Math.max(4, Math.round(sizeM / resolution));
  return {
    resolution_m: resolution,
    size,
    origin: { x_m: -sizeM / 2, y_m: -sizeM / 2 },
    values: Array.from({ length: size }, () => Array.from({ length: size }, () => -1)),
  };
}

function render(state) {
  const pose = state.pose || {};
  const map = state.map || fallbackMap(state);
  const occupied = drawGrid(map);
  drawTrail(state.trail || [], map);
  drawRobot(pose, map);

  fields.x.textContent = `${fmt(pose.x_m)} m`;
  fields.y.textContent = `${fmt(pose.y_m)} m`;
  fields.yaw.textContent = `${fmt(pose.yaw_deg, 1)} deg`;
  fields.distance.textContent = `${fmt(pose.distance_travelled_cm, 1)} cm`;
  fields.map.textContent = state.map_available ? `${map.size} x ${map.size}` : "off";
  fields.occupied.textContent = String(occupied);
  fields.source.textContent = pose.source || "--";
  fields.updated.textContent = pose.updated_at ? new Date(pose.updated_at * 1000).toLocaleString() : "--";
}

async function refresh() {
  try {
    const response = await fetch("api/state?include_map=true", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const state = await response.json();
    render(state);
    setStatus("live", "ok");
  } catch (error) {
    setStatus("error", "err");
  }
}

refresh();

// --- Real-time tracking toggle: faster polling when enabled ---
let pollTimer = null;
const realtimeToggle = document.getElementById("realtimeToggle");
function applyPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const period = realtimeToggle && realtimeToggle.checked ? 500 : 1500;
  pollTimer = setInterval(refresh, period);
}
if (realtimeToggle) realtimeToggle.addEventListener("change", applyPolling);
applyPolling();

// --- Vehicle control: open-loop (action_move) or closed-loop (/api/move) ---
const cmdStatus = document.getElementById("cmdStatus");
const stepCm = document.getElementById("stepCm");
const closedLoopToggle = document.getElementById("closedLoopToggle");

function setCmdStatus(text, cls) {
  if (!cmdStatus) return;
  cmdStatus.textContent = text;
  cmdStatus.className = `cmd-status ${cls || ""}`.trim();
}

function isClosedLoop() {
  return closedLoopToggle && closedLoopToggle.checked;
}

async function sendCommandOpenLoop(action, settingsOverride) {
  const body = { action, source: "slam-web", ttl_seconds: 30 };
  if (settingsOverride) body.settings_override = settingsOverride;
  const resp = await fetch("/action/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || String(resp.status));
  return data;
}

async function sendCommandClosedLoop(action, distanceCm, angleDeg) {
  const body = { action, closed_loop: true, source: "slam-web" };
  if (distanceCm != null) body.distance_cm = distanceCm;
  if (angleDeg != null) body.angle_deg = angleDeg;
  const resp = await fetch("api/move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || !data.ok) throw new Error(data.error || String(resp.status));
  return data;
}

async function sendCommand(action, settingsOverride) {
  const cl = isClosedLoop();
  setCmdStatus(`${cl ? "闭环" : "下发"} ${action}…`, "pending");
  try {
    let data;
    if (action === "emergency_stop" || !cl) {
      data = await sendCommandOpenLoop(action, settingsOverride);
      const label = cl ? "" : "";
      setCmdStatus(`已下发 ${action}`, "ok");
    } else {
      const cm = settingsOverride && settingsOverride.unit_distance_cm;
      const deg = settingsOverride && settingsOverride.turn_angle_deg;
      data = await sendCommandClosedLoop(action, cm, deg);
      const iters = data.iterations || 1;
      const err = data.error != null ? `误差${(data.error * 100).toFixed(1)}cm` : "";
      const conv = data.converged ? "已收敛" : "未收敛";
      setCmdStatus(`闭环完成 ${iters}轮 ${conv} ${err}`, data.converged ? "ok" : "warn");
    }
    refresh();
  } catch (error) {
    setCmdStatus(`错误: ${error.message}`, "err");
  }
}

document.querySelectorAll(".controls .btn[data-cmd]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const cmd = btn.dataset.cmd;
    if (cmd === "emergency_stop") {
      sendCommand("emergency_stop");
      return;
    }
    const override = {};
    if (cmd.startsWith("move_")) {
      const cm = Math.max(1, Math.min(50, Number(stepCm && stepCm.value) || 10));
      override.unit_distance_cm = cm;
    } else if (cmd.startsWith("turn_")) {
      override.turn_angle_deg = Number(btn.dataset.angle) || 45;
    }
    sendCommand(cmd, override);
  });
});

const resetBtn = document.getElementById("resetBtn");
if (resetBtn) {
  resetBtn.addEventListener("click", async () => {
    setCmdStatus("复位里程…", "pending");
    try {
      await fetch("api/reset", { method: "POST" });
      setCmdStatus("已复位", "ok");
      refresh();
    } catch (error) {
      setCmdStatus(`错误: ${error.message}`, "err");
    }
  });
}
