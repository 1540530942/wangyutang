const form = document.querySelector("#ideaForm");
const statusEl = document.querySelector("#ideaStatus");

const sessionId = (() => {
  const key = "future_ideas_session";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const value = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  localStorage.setItem(key, value);
  return value;
})();

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await response.text() || response.statusText);
  }
  return response.json();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const consent = document.querySelector("#ideaConsent").checked;
  if (!consent) {
    statusEl.textContent = "需要同意保存后才能提交";
    return;
  }
  statusEl.textContent = "提交中";
  try {
    await postJson("/api/visitor/register", {
      visitor_name: document.querySelector("#ideaName").value.trim(),
      contact: document.querySelector("#ideaContact").value.trim(),
      purpose: `[future-system-idea] ${document.querySelector("#ideaPurpose").value.trim()}`,
      note: document.querySelector("#ideaNote").value.trim(),
      consent,
      session_id: sessionId,
    });
    await postJson("/api/visitor/event", {
      session_id: sessionId,
      event_type: "future_idea_submit",
      page: location.pathname,
      target: "ideaForm",
      detail: document.querySelector("#ideaPurpose").value.trim(),
      duration_seconds: 0,
      consent,
    }).catch(() => {});
    form.reset();
    document.querySelector("#ideaConsent").checked = true;
    statusEl.textContent = "已保存，谢谢你的想法";
  } catch (error) {
    statusEl.textContent = `提交失败：${error.message}`;
  }
});
