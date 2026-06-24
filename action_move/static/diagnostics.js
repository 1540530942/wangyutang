const onlinePill = document.querySelector("#onlinePill");
const refreshBtn = document.querySelector("#refreshBtn");
const deviceKv = document.querySelector("#deviceKv");
const safetyKv = document.querySelector("#safetyKv");
const servicesPre = document.querySelector("#servicesPre");
const dockerPre = document.querySelector("#dockerPre");
const rosPre = document.querySelector("#rosPre");
const processPre = document.querySelector("#processPre");
const tasksBox = document.querySelector("#tasksBox");

function fmtTime(seconds) {
  if (!seconds) return "-";
  return new Date(seconds * 1000).toLocaleString();
}

function fmtAge(seconds) {
  const value = Number(seconds || 0);
  if (value < 60) return `${value.toFixed(1)}s`;
  return `${Math.round(value / 60)}min`;
}

function lines(value) {
  if (Array.isArray(value)) return value.join("\n") || "-";
  if (typeof value === "string") return value || "-";
  return "-";
}

function kv(container, rows) {
  container.innerHTML = rows
    .map(([key, value]) => `<b>${key}</b><span>${value === undefined || value === "" ? "-" : value}</span>`)
    .join("");
}

async function api(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function renderTasks(tasks) {
  tasksBox.innerHTML = "";
  if (!tasks.length) {
    tasksBox.textContent = "暂无任务";
    return;
  }
  for (const task of tasks) {
    const node = document.createElement("div");
    node.className = `task ${task.status || ""}`;
    const error = task.error ? `<div class="small">错误：${task.error}</div>` : "";
    node.innerHTML = `
      <strong>${task.name_zh || task.skill_id || task.action}</strong>
      <div class="small">${task.id || ""}</div>
      <div class="small">${fmtTime(task.requested_at)} · ${task.status || "-"} · ${task.source || "-"}</div>
      ${error}
    `;
    tasksBox.appendChild(node);
  }
}

async function refresh() {
  const data = await api("../api/diagnostics");
  const device = data.device || {};
  const diagnostics = data.diagnostics || {};
  const online = Boolean(device.online);
  onlinePill.textContent = online ? `在线 ${device.status || "idle"}` : `离线 ${fmtAge(device.age_seconds)}`;
  onlinePill.classList.toggle("ok", online);
  onlinePill.classList.toggle("bad", !online);

  kv(deviceKv, [
    ["设备", device.device_id],
    ["主机", device.hostname],
    ["IP", device.ip_address],
    ["Wi-Fi", device.wifi_ssid],
    ["网关", device.gateway],
    ["最后心跳", fmtTime(device.last_seen_at)],
    ["心跳年龄", fmtAge(device.age_seconds)],
    ["当前任务", device.current_task_id || "-"],
  ]);

  kv(safetyKv, [
    ["底盘熔断", diagnostics.disabled_actions || "未上报"],
    ["诊断上报", fmtTime(diagnostics.reported_at)],
    ["Uptime", diagnostics.uptime],
    ["Throttled", diagnostics.throttled],
    ["网页设置", `${data.settings?.unit_distance_cm ?? "-"}cm / ${data.settings?.turn_angle_deg ?? "-"}° / ${data.settings?.sensitivity ?? "-"}x`],
    ["待处理任务", data.pending_tasks],
  ]);

  servicesPre.textContent = lines(diagnostics.services);
  dockerPre.textContent = lines(diagnostics.docker);
  rosPre.textContent = lines(diagnostics.ros);
  processPre.textContent = lines(diagnostics.top_processes);
  renderTasks(data.recent_tasks || []);
}

refreshBtn.addEventListener("click", () => {
  refresh().catch((error) => {
    onlinePill.textContent = `读取失败 ${error.message}`;
    onlinePill.classList.add("bad");
  });
});

refresh().catch((error) => {
  onlinePill.textContent = `读取失败 ${error.message}`;
  onlinePill.classList.add("bad");
});
setInterval(() => {
  refresh().catch(() => {});
}, 5000);
