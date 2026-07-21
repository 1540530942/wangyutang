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

function envLatency(env) {
  const lat = (env && env.latency_ms) || {};
  const order = ["ingest", "agent", "validate", "safety", "dispatch", "execution", "total"];
  const parts = order.filter((k) => lat[k] != null).map((k) => `${k}:${Math.round(lat[k])}`);
  return parts.join(" · ");
}

// Build the ordered stage bars for one turn on the absolute session timeline.
// VAD sits where the speech actually was; processing stages cascade after it
// using their measured durations (ASR → sandbox → execution → TTS).
function turnStages(u, env) {
  const bars = [];
  const marks = [];
  const vs = u.vad_start_ms != null ? u.vad_start_ms : u.audio_start_ms;
  const ve = u.vad_end_ms != null ? u.vad_end_ms : u.audio_end_ms;
  if (vs != null && ve != null && ve > vs) {
    bars.push({ cls: "vad", start: vs, dur: ve - vs, label: "VAD", tip: `VAD 说话段 ${vs}→${ve}ms (${esc(u.vad_source || "")})` });
  }
  let cursor = ve != null ? ve : (vs != null ? vs : 0);
  if (u.asr_elapsed_ms != null) {
    bars.push({ cls: "asr", start: cursor, dur: u.asr_elapsed_ms, label: `ASR ${Math.round(u.asr_elapsed_ms)}ms`, tip: `ASR ${Math.round(u.asr_elapsed_ms)}ms · “${esc(u.text)}”` });
    cursor += u.asr_elapsed_ms;
  }
  marks.push({ cls: "wake" + (u.wake_status === "awake" ? "" : " miss"), at: cursor, tip: `唤醒判定: ${esc(u.wake_status || "unknown")} (${esc(u.status || "")})` });
  const lat = (env && env.latency_ms) || {};
  const planDur = lat.agent != null ? lat.agent : u.route_elapsed_ms;
  const hasPlan = u.skill_id || u.tts_text || env;
  if (planDur != null && hasPlan) {
    bars.push({ cls: "plan", start: cursor, dur: planDur, label: `沙盒 ${Math.round(planDur)}ms`, tip: `沙盒规划 ${Math.round(planDur)}ms · ${u.skill_id ? "skill:" + esc(u.skill_id) : "对话/观察应答"}${env ? " · " + esc(env.envelope_id || "") : ""}` });
    cursor += planDur;
  }
  const task = u.action_task || {};
  const taskInner = task.task || task;
  const taskId = taskInner && taskInner.id ? taskInner.id : "";
  const execErr = u.action_error;
  if (lat.execution != null && (taskId || execErr)) {
    bars.push({ cls: "exec" + (execErr ? " err" : ""), start: cursor, dur: lat.execution, label: `执行 ${Math.round(lat.execution)}ms`, tip: execErr ? `车端执行错误: ${esc(execErr)}` : `车端执行 ${Math.round(lat.execution)}ms · task:${esc(taskId)}` });
    cursor += lat.execution;
  } else if (taskId && !execErr) {
    marks.push({ cls: "wake", at: cursor, tip: `车端已下发 task:${esc(taskId)}` });
  }
  if (u.tts_elapsed_ms != null && u.tts_text) {
    bars.push({ cls: "tts", start: cursor, dur: u.tts_elapsed_ms, label: `TTS ${Math.round(u.tts_elapsed_ms)}ms`, tip: `TTS 播报 ${Math.round(u.tts_elapsed_ms)}ms · “${esc(u.tts_text)}”` });
    cursor += u.tts_elapsed_ms;
  }
  return { bars, marks, end: cursor };
}

function niceStep(totalMs) {
  const targetTicks = 8;
  const raw = totalMs / targetTicks;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) if (m * pow >= raw) return m * pow;
  return 10 * pow;
}

function renderWaterfall(utterances, envs) {
  const el = document.getElementById("waterfall");
  const rows = utterances.map((u) => ({ u, ...turnStages(u, envs[u.turn_id]) }));
  const minStart = Math.min(...rows.map((r) => (r.bars[0] ? r.bars[0].start : 0)), 0);
  const maxEnd = Math.max(...rows.map((r) => r.end), 1);
  const span = Math.max(maxEnd - minStart, 1);
  const labelW = 150;
  const avail = Math.max((el.parentElement.clientWidth || 900) - labelW - 24, 480);
  const pxPerMs = avail / span;
  const px = (ms) => (ms - minStart) * pxPerMs;

  const step = niceStep(span);
  let ruler = "";
  for (let t = Math.ceil(minStart / step) * step; t <= maxEnd; t += step) {
    ruler += `<div class="wf-tick" style="left:${px(t)}px">${Math.round(t)}</div>`;
  }

  const body = rows.map((r, i) => {
    const bars = r.bars.map((b) =>
      `<div class="wf-bar ${b.cls}" data-start="${b.start}" data-end="${b.start + b.dur}" style="left:${px(b.start)}px;width:${Math.max(b.dur * pxPerMs, 2)}px" title="${b.tip}">${esc(b.label)}</div>`
    ).join("");
    const marks = r.marks.map((m) =>
      `<div class="wf-mark ${m.cls}" style="left:${px(m.at)}px" title="${m.tip}"></div>`
    ).join("");
    const u = r.u;
    const short = (u.text || "(空)").slice(0, 12);
    return `<div class="wf-row" data-turn="${i}">
      <div class="wf-label" onclick="toggleDetail(${i})"><div class="lt">${esc(short)}</div><div class="ls">${esc(u.turn_id || u.segment_id)}</div></div>
      <div class="wf-track" onclick="seekFromClick(event)">${bars}${marks}</div>
    </div>
    <div class="wf-detail" id="wf-detail-${i}" style="display:none"></div>`;
  }).join("");

  el.innerHTML = `<div class="wf" style="--lbl:${labelW}px"><div class="wf-ruler">${ruler}</div>${body}<div class="wf-playhead" id="wf-playhead" style="display:none;left:${labelW}px"></div></div>`;
  window._wfRows = rows;
  window._wf = { minStart, pxPerMs, labelW };
}

// Move the red playhead to an absolute session-time position (ms) and
// highlight whichever module bars are under it.
function updatePlayhead(ms) {
  const w = window._wf;
  const head = document.getElementById("wf-playhead");
  if (!w || !head) return;
  head.style.display = "block";
  head.style.left = `${w.labelW + (ms - w.minStart) * w.pxPerMs}px`;
  document.querySelectorAll(".wf-bar").forEach((el) => {
    const s = parseFloat(el.dataset.start), e = parseFloat(el.dataset.end);
    el.classList.toggle("active", ms >= s && ms <= e);
  });
}

// Click anywhere on a track to seek the audio to that session time.
function seekFromClick(ev) {
  const w = window._wf;
  const audio = document.querySelector("#player audio");
  if (!w || !audio) return;
  const track = ev.currentTarget;
  const x = ev.clientX - track.getBoundingClientRect().left;
  const ms = w.minStart + x / w.pxPerMs;
  audio.currentTime = Math.max(0, ms / 1000);
}

function toggleDetail(i) {
  const box = document.getElementById(`wf-detail-${i}`);
  if (!box) return;
  if (box.style.display !== "none") { box.style.display = "none"; return; }
  const r = window._wfRows[i];
  const u = r.u;
  const env = (window._wfEnvs || {})[u.turn_id];
  const seq = r.bars.map((b) => `${b.cls}:${Math.round(b.dur)}ms`).join(" → ");
  box.innerHTML = `<div><b>${esc(u.turn_id)}</b> · 文本：“${esc(u.text)}” · 唤醒：${esc(u.wake_status)} · 状态：${esc(u.status || "")}${u.skill_id ? " · skill:" + esc(u.skill_id) : ""}</div>
    <div style="margin-top:4px;color:#6b7280">时序：${esc(seq) || "无处理阶段"}</div>
    ${u.tts_text ? `<div style="margin-top:4px">TTS：“${esc(u.tts_text)}”</div>` : ""}
    ${env ? `<div style="margin-top:4px;color:#6b7280">DecisionEnvelope ${esc(env.envelope_id || "")} · ${esc(envLatency(env))}</div><pre>${esc(JSON.stringify(env, null, 2))}</pre>` : ""}`;
  box.style.display = "block";
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
  window._wfEnvs = envs;
  $("turns").innerHTML = "";
  if ((t.utterances || []).length) {
    renderWaterfall(t.utterances, envs);
    const audio = document.querySelector("#player audio");
    if (audio) {
      const sync = () => updatePlayhead(audio.currentTime * 1000);
      audio.addEventListener("timeupdate", sync);
      audio.addEventListener("seeked", sync);
      audio.addEventListener("play", sync);
    }
  } else {
    document.getElementById("waterfall").innerHTML = '<div class="empty">无轮次数据</div>';
  }

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
