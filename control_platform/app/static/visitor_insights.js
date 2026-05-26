const sessionId = (() => {
  const key = "visitor_insights_session";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const value = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  localStorage.setItem(key, value);
  return value;
})();

const startedAt = Date.now();
let adminCode = "";
let consentGranted = localStorage.getItem("visitor_insights_consent") === "1";

const form = document.querySelector("#visitorForm");
const statusEl = document.querySelector("#registerStatus");
const consentEl = document.querySelector("#visitorConsent");
const adminPanel = document.querySelector("#adminPanel");
const statsEl = document.querySelector("#stats");
const registrationRows = document.querySelector("#registrationRows");
const eventRows = document.querySelector("#eventRows");

consentEl.checked = consentGranted;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const map = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return map[char];
  });
}

function fmtTime(value) {
  if (!value) return "";
  return new Date(value * 1000).toLocaleString();
}

async function postJson(path, payload, headers = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

async function sendEvent(eventType, target = "", detail = "") {
  if (!consentGranted) return;
  await postJson("/api/visitor/event", {
    session_id: sessionId,
    event_type: eventType,
    page: location.pathname,
    target,
    detail,
    duration_seconds: Math.round((Date.now() - startedAt) / 1000),
    consent: true,
  }).catch(() => {});
}

function renderStats(summary) {
  const cards = [
    ["登记数", summary.registration_count || 0],
    ["会话数", summary.session_count || 0],
    ["平均停留", `${summary.avg_duration_seconds || 0}s`],
    ["事件数", Object.values(summary.event_counts || {}).reduce((sum, value) => sum + value, 0)],
  ];
  statsEl.innerHTML = cards
    .map(([label, value]) => `<div class="stat-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
}

function renderRows(data) {
  registrationRows.innerHTML = (data.registrations || [])
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(fmtTime(item.created_at))}</td>
          <td>${escapeHtml(item.visitor_name)}</td>
          <td>${escapeHtml(item.contact)}</td>
          <td>${escapeHtml(item.purpose)}</td>
          <td>${escapeHtml(item.client_ip)}</td>
        </tr>
      `,
    )
    .join("");
  eventRows.innerHTML = (data.events || [])
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(fmtTime(item.created_at))}</td>
          <td>${escapeHtml(item.event_type)}</td>
          <td>${escapeHtml(item.page)}</td>
          <td>${escapeHtml(item.target)}</td>
          <td>${escapeHtml(item.duration_seconds)}s</td>
        </tr>
      `,
    )
    .join("");
}

async function loadAdmin() {
  if (!adminCode) {
    adminCode = window.prompt("请输入管理码") || "";
  }
  if (!adminCode) return;
  const response = await fetch("/api/visitor/summary", {
    headers: { "X-Visitor-Admin-Code": adminCode },
    cache: "no-store",
  });
  if (!response.ok) {
    adminCode = "";
    throw new Error("管理码无效");
  }
  const data = await response.json();
  adminPanel.hidden = false;
  renderStats(data.summary || {});
  renderRows(data);
}

consentEl.addEventListener("change", () => {
  consentGranted = consentEl.checked;
  localStorage.setItem("visitor_insights_consent", consentGranted ? "1" : "0");
  if (consentGranted) sendEvent("consent_enabled", "visitorConsent");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  consentGranted = consentEl.checked;
  localStorage.setItem("visitor_insights_consent", consentGranted ? "1" : "0");
  try {
    await postJson("/api/visitor/register", {
      visitor_name: document.querySelector("#visitorName").value.trim(),
      contact: document.querySelector("#visitorContact").value.trim(),
      purpose: document.querySelector("#visitorPurpose").value.trim(),
      note: document.querySelector("#visitorNote").value.trim(),
      consent: consentGranted,
      session_id: sessionId,
    });
    statusEl.textContent = "已登记";
    await sendEvent("register_submit", "visitorForm");
    form.reset();
    consentEl.checked = consentGranted;
  } catch (error) {
    statusEl.textContent = `登记失败：${error.message}`;
  }
});

document.querySelector("#adminUnlockBtn").addEventListener("click", () => {
  loadAdmin().catch((error) => {
    statusEl.textContent = error.message;
  });
});

document.querySelector("#refreshAdminBtn").addEventListener("click", () => {
  loadAdmin().catch((error) => {
    statusEl.textContent = error.message;
  });
});

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target.closest("button,a,input,textarea") : null;
  if (!target) return;
  const label = target.id || target.textContent?.trim() || target.tagName;
  sendEvent("click", label.slice(0, 120));
});

window.addEventListener("beforeunload", () => {
  if (!consentGranted) return;
  const payload = {
    session_id: sessionId,
    event_type: "page_leave",
    page: location.pathname,
    target: "",
    detail: "",
    duration_seconds: Math.round((Date.now() - startedAt) / 1000),
    consent: true,
  };
  navigator.sendBeacon("/api/visitor/event", new Blob([JSON.stringify(payload)], { type: "application/json" }));
});

sendEvent("page_view");
window.setTimeout(() => sendEvent("dwell_15s"), 15000);
