// vad_asr.js — VAD+ASR 金标测试集页面
const API = "/audio_interact/api";
const audioEls = {};  // case_id → <audio>

function msToSec(ms) {
  if (ms == null) return "—";
  return (ms / 1000).toFixed(2) + "s";
}
function durLabel(ms) {
  if (!ms) return "—";
  return ms >= 60000 ? `${(ms/60000).toFixed(1)}m` : `${(ms/1000).toFixed(1)}s`;
}
function wakeClass(w) {
  if (w === "awake")     return "awake";
  if (w === "wake_word") return "wake_word";
  return "sleeping";
}
function wakeLabel(w) {
  if (w === "awake")     return "已唤醒";
  if (w === "wake_word") return "唤醒词";
  return "未唤醒";
}
function statusLabel(s) {
  if (s === "ok")                   return `<span class="status-ok">✓ ${s}</span>`;
  if (s === "waiting_for_wake_word") return `<span class="status-wait">等待唤醒</span>`;
  if (s === "wake_word")             return `<span class="status-ok">唤醒</span>`;
  return `<span class="status-wait">${s || "—"}</span>`;
}

function renderCase(c) {
  const audioUrl = c.audio_url || `${API}/sessions/${c.audio_source_session || c.session_id}/audio/mic_proc_16k.wav`;
  const dur = c.duration_ms || 0;

  // VAD timeline bars
  const timelineBars = (c.utterances || []).map(u => {
    if (!dur) return "";
    const left  = (u.vad_start_ms / dur * 100).toFixed(2);
    const width = Math.max(0.5, ((u.vad_end_ms - u.vad_start_ms) / dur * 100)).toFixed(2);
    const cls   = wakeClass(u.expected_wake_status);
    return `<div class="timeline-seg ${cls}"
      style="left:${left}%;width:${width}%"
      title="${u.expected_text} (${msToSec(u.vad_start_ms)}~${msToSec(u.vad_end_ms)})"
      onclick="seekAudio('${c.case_id}', ${u.vad_start_ms / 1000})"></div>`;
  }).join("");

  // utterance rows
  const rows = (c.utterances || []).map(u => `
    <tr class="clickable" onclick="seekAudio('${c.case_id}', ${u.vad_start_ms / 1000})">
      <td>${u.index + 1}</td>
      <td><span style="font-size:11px;color:var(--muted)">${msToSec(u.vad_start_ms)} → ${msToSec(u.vad_end_ms)}</span></td>
      <td style="font-weight:600">${u.expected_text || "—"}</td>
      <td><span class="wake-pill ${wakeClass(u.expected_wake_status)}">${wakeLabel(u.expected_wake_status)}</span></td>
      <td>${statusLabel(u.expected_status)}</td>
      <td>${u.expected_skill_id ? `<span class="skill-tag">${u.expected_skill_id}</span>` : '<span class="status-wait">—</span>'}</td>
      <td style="color:var(--muted);font-size:12px">${u.notes || ""}</td>
    </tr>
  `).join("");

  const modeBadge = c.mode === "simplex"
    ? `<span class="mode-badge mode-simplex">单工 SIMPLEX</span>`
    : `<span class="mode-badge mode-duplex">双工 DUPLEX</span>`;

  return `
    <div class="case-card">
      <div class="case-head">
        <span class="gold-star">★</span>
        <span class="case-id">${c.case_id}</span>
        ${modeBadge}
        <span class="badge bp" style="font-size:11px">${c.capture_point === "browser_processed" ? "浏览器AEC" : c.capture_point}</span>
        <span class="badge ws" style="font-size:11px">${c.source === "websocket_stream" ? "WS流" : c.source}</span>
        <span class="badge cnt" style="font-size:11px">${(c.utterances||[]).length} 句</span>
        <span style="margin-left:auto;font-size:12px;color:var(--muted)">时长 ${durLabel(c.duration_ms)} · ${c.day || c.created_at}</span>
      </div>
      <div class="case-sid">
        Session: <a href="/dashboard/detail" onclick="event.preventDefault();window.open('/dashboard/detail#${c.session_id}','_blank')">${c.session_id}</a>
        &nbsp;|&nbsp; 设备: ${c.device_id}
      </div>
      <div style="font-size:12px;color:var(--muted);margin:8px 0">${c.description || ""}</div>

      <div class="audio-row">
        <audio id="audio-${c.case_id}" controls src="${audioUrl}" preload="none" style="height:36px"></audio>
        <span style="font-size:11px;color:var(--muted)">点击时间轴或表格行可定位播放</span>
      </div>
      <div class="timeline-wrap" id="timeline-${c.case_id}">
        ${timelineBars}
        <div class="timeline-cursor" id="cursor-${c.case_id}" style="left:0%"></div>
      </div>

      <table class="expect-table" style="margin-top:16px">
        <thead><tr>
          <th>#</th><th>VAD 起止（预期）</th><th>ASR 文本（预期）</th>
          <th>唤醒状态</th><th>沙盒状态</th><th>技能</th><th>备注</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function seekAudio(caseId, sec) {
  const el = document.getElementById(`audio-${caseId}`);
  if (!el) return;
  el.currentTime = sec;
  el.play().catch(() => {});
}

function bindAudioCursor(c) {
  const el = document.getElementById(`audio-${c.case_id}`);
  const dur = c.duration_ms || 0;
  if (!el || !dur) return;
  el.addEventListener("timeupdate", () => {
    const cursor = document.getElementById(`cursor-${c.case_id}`);
    if (!cursor) return;
    cursor.style.left = (el.currentTime / (dur / 1000) * 100).toFixed(2) + "%";
  });
}

async function loadCases() {
  const container = document.getElementById("cases-container");
  let cases;
  try {
    cases = await fetch(`${API}/golden`).then(r => r.json());
  } catch (e) {
    container.innerHTML = `<div class="loading-note">加载失败: ${e}</div>`;
    return;
  }
  if (!cases.length) {
    container.innerHTML = `<div class="loading-note">暂无金标数据</div>`;
    return;
  }
  container.innerHTML = cases.map(renderCase).join("");
  cases.forEach(c => bindAudioCursor(c));
}

loadCases();
