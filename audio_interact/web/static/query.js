const API = "/audio_interact/api";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ms = (v) => (v == null || v === "" ? "—" : `${Math.round(v)}ms`);

async function loadRecent() {
  try {
    const items = await fetch(`${API}/sessions?limit=30`).then((r) => r.json());
    $("recent-sessions").innerHTML = items.map((s) => `<option value="${esc(s.session_id)}">`).join("");
    $("recent").innerHTML = items.slice(0, 20).map((s) =>
      `<a href="#" onclick="pick('${esc(s.session_id)}');return false" title="${esc(s.created_at)} · ${esc((s.texts || []).join(" / "))}">${esc(s.session_id)}</a>`
    ).join("") || '<span class="empty">暂无会话</span>';
  } catch (e) { $("recent").innerHTML = `<span class="empty">加载失败: ${esc(e)}</span>`; }
}

function pick(sid) { $("sid").value = sid; runQuery(); }

function wakeTag(u) {
  const w = u.wake_status || "unknown";
  return `<span class="tag ${esc(w)}">${esc(w)}</span>`;
}

function stage(name, ok, msVal, val, extraCls) {
  const cls = extraCls || (ok ? "done" : "skip");
  return `<div class="stage ${cls}"><div class="sname">${esc(name)}</div><div class="sms">${esc(msVal)}</div><div class="sval">${val || ""}</div></div>`;
}

function envLatency(env) {
  const lat = (env && env.latency_ms) || {};
  const order = ["ingest", "agent", "validate", "safety", "dispatch", "execution", "total"];
  const parts = order.filter((k) => lat[k] != null).map((k) => `${k}:${Math.round(lat[k])}`);
  return parts.join(" · ");
}

function renderTurn(u, env) {
  const vadMs = u.vad_start_ms != null && u.vad_end_ms != null ? `${u.vad_start_ms}→${u.vad_end_ms}` : "—";
  const hasCmd = !!u.skill_id;
  const task = u.action_task || {};
  const taskInner = task.task || task;
  const taskId = taskInner && taskInner.id ? taskInner.id : "";
  const execErr = u.action_error;
  const stages = [
    stage("① 网关/切分 VAD", u.vad_start_ms != null || u.audio_start_ms != null, vadMs, esc(u.vad_source || "")),
    stage("② ASR 识别", !!u.text, ms(u.asr_elapsed_ms), `“${esc(u.text)}”`),
    stage("③ 唤醒判定", u.wake_status === "awake", u.wake_status === "awake" ? "命中" : "未命中", esc(u.status || "")),
    stage("④ 沙盒规划", hasCmd, env ? envLatency(env) || ms(u.route_elapsed_ms) : ms(u.route_elapsed_ms),
      hasCmd ? `skill: <b>${esc(u.skill_id)}</b>` : "未产生指令"),
    stage("⑤ 车端执行", !!taskId && !execErr, env && env.latency_ms && env.latency_ms.execution != null ? ms(env.latency_ms.execution) : (taskId ? "已下发" : "—"),
      execErr ? `错误: ${esc(execErr)}` : (taskId ? `task: ${esc(taskId)}` : "无动作"), execErr ? "error" : undefined),
    stage("⑥ TTS 播报", !!u.tts_text, ms(u.tts_elapsed_ms), u.tts_text ? `“${esc(u.tts_text)}”` : "无播报"),
  ];
  const envBlock = env
    ? `<details class="raw" style="margin-top:8px"><summary>DecisionEnvelope ${esc(env.envelope_id || "")}（沙盒决策详情）</summary><pre>${esc(JSON.stringify(env, null, 2))}</pre></details>`
    : "";
  return `<div class="turn">
    <div class="turn-head"><span class="tag">${esc(u.turn_id || u.segment_id)}</span><span class="txt">${esc(u.text || "(空)")}</span>${wakeTag(u)}${u.skill_id ? `<span class="tag awake">skill:${esc(u.skill_id)}</span>` : ""}</div>
    <div class="pipeline">${stages.join("")}</div>${envBlock}
  </div>`;
}

function summarizeEvent(ev) {
  const skip = new Set(["ts_ms", "type", "segment_id", "turn_id", "session_id"]);
  const parts = [];
  for (const [k, v] of Object.entries(ev)) {
    if (skip.has(k) || v == null || v === "") continue;
    let sv = typeof v === "object" ? JSON.stringify(v) : String(v);
    if (sv.length > 80) sv = sv.slice(0, 80) + "…";
    parts.push(`${k}=${sv}`);
  }
  return parts.join("  ");
}

async function runQuery() {
  const sid = $("sid").value.trim();
  if (!sid) return;
  history.replaceState(null, "", `?session_id=${encodeURIComponent(sid)}`);
  $("placeholder").style.display = "block";
  $("placeholder").textContent = "查询中…";
  $("result").style.display = "none";
  let t;
  try {
    const resp = await fetch(`${API}/sessions/${encodeURIComponent(sid)}/trace`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    t = await resp.json();
  } catch (e) {
    $("placeholder").textContent = `查询失败: ${e}（确认 Session ID 是否存在）`;
    return;
  }
  $("placeholder").style.display = "none";
  $("result").style.display = "block";

  const metaPairs = [
    ["Session", t.session_id], ["设备", t.device_id], ["接入方式", t.source],
    ["采集点", t.capture_point], ["创建时间", t.created_at], ["时长", ms(t.duration_ms)],
    ["轮次数", (t.utterances || []).length], ["事件数", (t.events || []).length],
    ["Envelope 命中", Object.keys(t.envelopes || {}).length],
  ];
  $("meta").innerHTML = metaPairs.map(([k, v]) => `<div><span class="k">${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
  $("player").innerHTML = t.audio_url ? `<audio controls src="${esc(t.audio_url)}" style="width:100%"></audio>` : "";

  const envs = t.envelopes || {};
  $("turns").innerHTML = (t.utterances || []).map((u) => renderTurn(u, envs[u.turn_id])).join("") || '<div class="empty">无轮次数据</div>';

  $("events").innerHTML = (t.events || []).map((ev) => `<tr>
      <td class="ts">${esc(ev.ts_ms)}</td><td class="etype">${esc(ev.type)}</td>
      <td>${esc(ev.segment_id || ev.turn_id || "")}</td>
      <td>${esc(summarizeEvent(ev))}</td>
      <td><details class="raw"><summary>JSON</summary><pre>${esc(JSON.stringify(ev, null, 2))}</pre></details></td>
    </tr>`).join("") || '<tr><td colspan="5" class="empty">无事件</td></tr>';
}

$("sid").addEventListener("keydown", (e) => { if (e.key === "Enter") runQuery(); });
loadRecent();
const initSid = new URLSearchParams(location.search).get("session_id");
if (initSid) { $("sid").value = initSid; runQuery(); }
