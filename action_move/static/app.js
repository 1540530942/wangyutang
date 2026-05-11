const statusEl = document.querySelector("#deviceStatus");
const tasksEl = document.querySelector("#tasks");

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

function fmtTime(value) {
  if (!value) return "";
  return new Date(value * 1000).toLocaleString();
}

async function refresh() {
  const health = await api("./api/health");
  const device = health.device || {};
  statusEl.textContent = device.online
    ? `在线 ${device.device_id || ""}`
    : `离线 ${Math.round(device.age_seconds || 0)}s`;
  statusEl.classList.toggle("online", Boolean(device.online));

  const data = await api("./api/tasks");
  tasksEl.innerHTML = "";
  for (const task of data.tasks.slice(0, 12)) {
    const row = document.createElement("div");
    row.className = "task";
    row.innerHTML = `
      <div>
        <strong>${task.name_zh || task.skill_id}</strong>
        <code>${task.id}</code>
        <div>${fmtTime(task.requested_at)}</div>
      </div>
      <div>${task.status}</div>
    `;
    tasksEl.appendChild(row);
  }
}

async function createTask(action) {
  await api("./api/tasks", {
    method: "POST",
    body: JSON.stringify({ action, source: "web" }),
  });
  await refresh();
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => createTask(button.dataset.action));
});

document.querySelector("#refreshBtn").addEventListener("click", refresh);
refresh();
setInterval(refresh, 5000);
