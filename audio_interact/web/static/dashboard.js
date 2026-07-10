// dashboard.js — 语音交互全链路数据看板
const API = "/audio_interact/api";
let currentSession = null;
let audioEl = null;

const $ = id => document.getElementById(id);
const sessionsEl = $("sessions");
const detailEl   = $("detail");

// ── format helpers ────────────────────────────────────────────
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
function sourceBadge(src) {
  if (src === "websocket_stream") return `<span class="badge ws">WS流</span>`;
  if (src === "segment_upload")   return `<span class="badge seg">分段</span>`;
  return `<span class="badge leg">legacy</span>`;
}
function captureBadge(cp) {
  if (cp === "browser_processed") return `<span class="badge bp">浏览器AEC</span>`;
  if (cp === "pi_alsa_raw")       return `<span class="badge pi">Pi原始</span>`;
  return "";
}
function actionSummary(utt) {
  if (!utt.skill_id) return `<span class="status-wait">—</span>`;
  const task = utt.action_task;
  const err  = utt.action_error;
  if (err)   return `<span class="skill-tag">${utt.skill_id}</span> <span class="status-err">✗ ${err.slice(0,40)}</span>`;
  if (task)  return `<span class="skill-tag">${utt.skill_id}</span> <span class="status-ok">✓</span>`;
  return `<span class="skill-tag">${utt.skill_id}</span> <span class="status-wait">规划中</span>`;
}

// ── session list ──────────────────────────────────────────────
async function loadSessions() {
  sessionsEl.innerHTML = `<div style="padding:16px"><span class="spinner"></span> 加载中…</div>`;
  let items;
  try {
    items = await fetch(`${API}/sessions`).then(r => r.json());
  } catch (e) {
    sessionsEl.innerHTML = `<div class="empty-note">加载失败: ${e}</div>`;
    return;
  }
  if (!items.length) {
    sessionsEl.innerHTML = `<div class="empty-note">暂无 session</div>`;
    return;
  }
  sessionsEl.innerHTML = items.map(s => `
    <div class="session-item" data-id="${s.session_id}" onclick="openSession('${s.session_id}')">
      <div class="meta">
        <span class="sid">${s.session_id.slice(0,14)}</span>
        <span class="day">${s.day}</span>
        <span class="dur">${durLabel(s.duration_ms)}</span>
      </div>
      <div class="texts">${s.texts.join(" · ") || "（无识别文本）"}</div>
      <div class="badges">
        ${sourceBadge(s.source)}${captureBadge(s.capture_point)}
        <span class="badge cnt">${s.utterance_count} 句</span>
      </div>
    </div>
  `).join("");
}

// ── session detail ────────────────────────────────────────────
async function openSession(sessionId) {
  // highlight active
  document.querySelectorAll(".session-item").forEach(el => {
    el.classList.toggle("active", el.dataset.id === sessionId);
  });
  currentSession = sessionId;
  detailEl.innerHTML = `<div style="padding:40px 0;text-align:center"><span class="spinner"></span> 加载链路数据…</div>`;

  let s;
  try {
    s = await fetch(`${API}/sessions/${sessionId}`).then(r => r.json());
  } catch (e) {
    detailEl.innerHTML = `<div class="empty-note">加载失败: ${e}</div>`;
    return;
  }
  renderDetail(s);
}

function renderDetail(s) {
  const utts = s.utterances || [];
  const dur  = s.duration_ms || 0;

  // ── timeline bars ──────────────────────────────────────────
  const timelineBars = utts.map(u => {
    const start = u.vad_start_ms ?? u.audio_start_ms ?? 0;
    const end   = u.vad_end_ms   ?? u.audio_end_ms   ?? start;
    if (!dur) return "";
    const left  = (start / dur * 100).toFixed(2);
    const width = Math.max(0.5, ((end - start) / dur * 100)).toFixed(2);
    const cls   = wakeClass(u.wake_status);
    return `<div class="timeline-seg ${cls}" style="left:${left}%;width:${width}%"
      title="${u.text || '—'} (${msToSec(start)}~${msToSec(end)})"
      onclick="seekAudio(${start / 1000})"></div>`;
  }).join("");

  // ── utterance rows ─────────────────────────────────────────
  const rows = utts.map((u, i) => {
    const startSec = (u.vad_start_ms ?? u.audio_start_ms ?? 0) / 1000;
    return `<tr class="clickable" onclick="seekAudio(${startSec})">
      <td>${i + 1}</td>
      <td>
        <div class="vad-range">${msToSec(u.vad_start_ms ?? u.audio_start_ms)} → ${msToSec(u.vad_end_ms ?? u.audio_end_ms)}</div>
        ${u.vad_source === "fixed_window" ? '<div style="font-size:10px;color:#999">定长上传</div>' : ''}
      </td>
      <td class="asr-text">${u.text || '<span style="color:#aaa">（空）</span>'}</td>
      <td><span class="wake-pill ${wakeClass(u.wake_status)}">${u.wake_status || '—'}</span></td>
      <td>${u.status ? `<span class="status-${u.status === 'ok' ? 'ok' : u.status === 'waiting_for_wake_word' ? 'wait' : 'err'}">${u.status}</span>` : '—'}</td>
      <td>${actionSummary(u)}</td>
      <td class="tts-text">${u.tts_text || '—'}</td>
    </tr>`;
  }).join("");

  detailEl.innerHTML = `
    <!-- 元信息 -->
    <div class="card">
      <div class="card-head">
        <div>
          <div class="label">Session</div>
          <h2>${s.session_id}</h2>
        </div>
        <div style="text-align:right;font-size:12px;color:var(--muted)">
          ${s.day} · ${durLabel(s.duration_ms)}<br>
          ${sourceBadge(s.source)} ${captureBadge(s.capture_point)}
        </div>
      </div>
      <div class="meta-grid">
        <div class="meta-cell"><div class="k">设备</div><div class="v">${s.device_id || '—'}</div></div>
        <div class="meta-cell"><div class="k">时长</div><div class="v">${durLabel(s.duration_ms)}</div></div>
        <div class="meta-cell"><div class="k">句数</div><div class="v">${utts.length}</div></div>
        <div class="meta-cell"><div class="k">采集点</div><div class="v">${s.capture_point || '—'}</div></div>
        <div class="meta-cell"><div class="k">proc=raw</div><div class="v">${s.proc_same_as_raw ?? '—'}</div></div>
        <div class="meta-cell"><div class="k">来源</div><div class="v">${s.source || '—'}</div></div>
      </div>
    </div>

    <!-- 音频播放器 -->
    ${s.audio_url ? `
    <div class="card">
      <div class="card-head"><h2>原始音频</h2><span style="font-size:12px;color:var(--muted)">点击时间轴/行可定位播放</span></div>
      <audio id="audioPlayer" controls src="${s.audio_url}"></audio>
      <div class="timeline-wrap" id="timeline">
        ${timelineBars}
        <div class="timeline-cursor" id="cursor" style="left:0%"></div>
      </div>
    </div>` : ""}

    <!-- 链路追踪表 -->
    <div class="card">
      <div class="card-head"><h2>链路追踪 · 音频 → VAD → ASR → 沙盒</h2></div>
      ${utts.length === 0
        ? '<div class="empty-note">本 session 无识别记录</div>'
        : `<table class="utbl">
            <thead><tr>
              <th>#</th><th>VAD 起止</th><th>ASR 文本</th><th>唤醒</th><th>状态</th><th>沙盒技能 / 动作</th><th>TTS 播报</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>`
      }
    </div>
  `;

  // bind audio cursor
  audioEl = document.getElementById("audioPlayer");
  if (audioEl) {
    audioEl.addEventListener("timeupdate", () => {
      const cursor = document.getElementById("cursor");
      if (!cursor || !s.duration_ms) return;
      const pct = (audioEl.currentTime / (s.duration_ms / 1000)) * 100;
      cursor.style.left = pct.toFixed(2) + "%";
    });
  }
}

function seekAudio(sec) {
  if (!audioEl) return;
  audioEl.currentTime = sec;
  audioEl.play().catch(() => {});
}

// ── init ──────────────────────────────────────────────────────
loadSessions();
