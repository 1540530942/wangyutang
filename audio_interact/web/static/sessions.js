// sessions.js — 语音交互全链路数据日志

// ── waterfall chart ───────────────────────────────────────────
// durationMs = full session length; all rows share one absolute timeline so
// bars are horizontally aligned and silence gaps are visible.
function renderWaterfall(utts, durationMs) {
  const withTiming = utts.filter(u => u.asr_elapsed_ms != null);
  if (!withTiming.length) {
    return `<div class="wf-legacy-note">历史 session，无阶段耗时埋点——仅显示 VAD 音频时间轴</div>`;
  }

  // Use session total as the shared x-axis; fall back to last utterance end if missing.
  const total = durationMs || Math.max(...withTiming.map(u =>
    (u.vad_end_ms ?? 0) + (u.asr_elapsed_ms ?? 0) + (u.route_elapsed_ms ?? 0) + (u.tts_elapsed_ms ?? 0)
  )) || 1;
  const p = ms => (Math.max(0, ms || 0) / total * 100).toFixed(2);

  const NO_ROUTE = new Set(["asr_only", "empty", "waiting_for_wake_word", "wake_word"]);

  const rows = withTiming.map(u => {
    const vadStart = u.vad_start_ms ?? 0;
    const vadDur   = Math.max(0, (u.vad_end_ms ?? 0) - vadStart);
    const asrMs    = u.asr_elapsed_ms ?? 0;
    const hasRoute = !NO_ROUTE.has(u.status) && u.route_elapsed_ms != null;
    const routeMs  = hasRoute ? u.route_elapsed_ms : null;
    const ttsMs    = u.tts_elapsed_ms ?? null;

    const label = u.text || `（${u.status || u.wake_status || "—"}）`;
    const shortLabel = label.length > 16 ? label.slice(0, 15) + "…" : label;

    // Compact stats line below the bar — no numbers crammed inside bars
    const parts = [`VAD ${vadDur}ms`, `ASR ${asrMs}ms`];
    if (routeMs != null) parts.push(`路由 ${routeMs}ms`);
    if (ttsMs   != null) parts.push(`TTS ${ttsMs}ms`);
    const chainMs = vadDur + asrMs + (routeMs ?? 0) + (ttsMs ?? 0);

    return `<div class="wf-row">
      <div class="wf-label" title="${label}">${shortLabel}</div>
      <div class="wf-col">
        <div class="wf-track">
          <div class="wf-seg wf-gap"   style="width:${p(vadStart)}%" title="等待 ${vadStart}ms"></div>
          <div class="wf-seg wf-vad"   style="width:${p(vadDur)}%"   title="VAD 说话 ${vadDur}ms"></div>
          <div class="wf-seg wf-asr"   style="width:${p(asrMs)}%"    title="ASR 识别 ${asrMs}ms"></div>
          ${routeMs != null ? `<div class="wf-seg wf-route" style="width:${p(routeMs)}%" title="路由+执行 ${routeMs}ms"></div>` : ""}
          ${ttsMs   != null ? `<div class="wf-seg wf-tts"   style="width:${p(ttsMs)}%"   title="TTS 合成 ${ttsMs}ms"></div>`   : ""}
        </div>
        <div class="wf-stats">${parts.join(" · ")} <span class="wf-chain-total">＝ ${chainMs}ms</span></div>
      </div>
    </div>`;
  }).join("");

  // Time-scale ruler aligned with track area
  const ticks = [0, 25, 50, 75, 100].map(pct =>
    `<span class="wf-tick" style="left:${pct}%">${(pct / 100 * total / 1000).toFixed(1)}s</span>`
  ).join("");

  return `
  <div class="wf-legend">
    <span><span class="wf-dot wf-vad-dot"></span>VAD 说话</span>
    <span><span class="wf-dot wf-asr-dot"></span>ASR 识别</span>
    <span><span class="wf-dot wf-route-dot"></span>路由+执行</span>
    <span><span class="wf-dot wf-tts-dot"></span>TTS 合成</span>
    <span class="wf-legend-note">时间轴基准：session 总时长 ${(total / 1000).toFixed(1)}s，各行横向对齐说话时刻</span>
  </div>
  <div class="wf-chart">
    ${rows}
    <div class="wf-ruler">
      <div class="wf-label"></div>
      <div class="wf-col"><div class="wf-ruler-inner">${ticks}</div></div>
    </div>
  </div>`;
}
const API = "/audio_interact/api";
const SESSION_LIST_LIMIT = 500;
let currentSession = null;
let audioEl = null;
let sessionsCache = [];

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
function dateTimeLabel(value) {
  if (!value) return "—";
  const normalized = String(value).replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return String(value).replace("T", " ").replace(/\+.*$/, "").slice(0, 19);
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
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
function sessionTypeBadge(sessionId) {
  if (!sessionId) return "";
  if (sessionId.startsWith("golden-exec-")) return `<span class="badge golden-exec">金标执行</span>`;
  if (sessionId.startsWith("ci-"))          return `<span class="badge ci-test">CI测试</span>`;
  return "";
}
function actionSummary(utt) {
  if (utt.status === "asr_only") return `<span class="status-wait">仅 ASR，未进沙盒</span>`;
  if (!utt.skill_id) return `<span class="status-wait">—</span>`;
  const task = utt.action_task;
  const err  = utt.action_error;
  if (err)   return `<span class="skill-tag">${utt.skill_id}</span> <span class="status-err">✗ ${err.slice(0,40)}</span>`;
  if (task)  return `<span class="skill-tag">${utt.skill_id}</span> <span class="status-ok">✓</span>`;
  return `<span class="skill-tag">${utt.skill_id}</span> <span class="status-wait">规划中</span>`;
}
function hasTraceContent(s) {
  return Number(s.utterance_count || 0) > 0 || (Array.isArray(s.texts) && s.texts.length > 0);
}

function ttsSummary(utt) {
  if (utt.tts_text) return utt.tts_text;
  if (utt.status === "asr_only") return "未调用沙盒，无 TTS";
  if (utt.wake_status === "sleeping") return "未唤醒，无 TTS";
  return "—";
}

// ── session list ──────────────────────────────────────────────
async function loadSessions() {
  sessionsEl.innerHTML = `<div style="padding:16px"><span class="spinner"></span> 加载中…</div>`;
  let items;
  try {
    items = await fetch(`${API}/sessions?limit=${SESSION_LIST_LIMIT}`).then(r => r.json());
  } catch (e) {
    sessionsEl.innerHTML = `<div class="empty-note">加载失败: ${e}</div>`;
    return;
  }
  if (!items.length) {
    sessionsEl.innerHTML = `<div class="empty-note">暂无 session</div>`;
    return;
  }
  sessionsCache = items;
  const hashId = decodeURIComponent((window.location.hash || "").replace(/^#/, ""));
  const visibleItems = items.filter(hasTraceContent);
  const renderItems = visibleItems.length ? visibleItems : items;
  sessionsEl.innerHTML = renderItems.map(s => `
    <div class="session-item" data-id="${s.session_id}" onclick="openSession('${s.session_id}')">
      <div class="meta">
        <span class="sid">${s.session_id.slice(0,14)}</span>
        <span class="day">${dateTimeLabel(s.created_at || s.day)}</span>
        <span class="dur">${durLabel(s.duration_ms)}</span>
      </div>
      <div class="texts">${(Array.isArray(s.texts) ? s.texts : []).join(" · ") || "（无识别文本）"}</div>
      <div class="badges">
        ${sessionTypeBadge(s.session_id)}${sourceBadge(s.source)}${captureBadge(s.capture_point)}
        <span class="badge cnt">${s.utterance_count} 句</span>
      </div>
    </div>
  `).join("");
  const hashTarget = items.find(s => s.session_id === hashId)?.session_id;
  const target = hashTarget || renderItems[0].session_id;
  if (!currentSession && target) {
    await openSession(target, { updateHash: false });
  }
}

// ── session detail ────────────────────────────────────────────
async function openSession(sessionId, opts = {}) {
  // highlight active
  document.querySelectorAll(".session-item").forEach(el => {
    el.classList.toggle("active", el.dataset.id === sessionId);
  });
  currentSession = sessionId;
  if (opts.updateHash !== false) {
    window.history.replaceState(null, "", `#${encodeURIComponent(sessionId)}`);
  }
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
      <td class="tts-text">${ttsSummary(u)}</td>
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
          ${dateTimeLabel(s.created_at || s.day)} · ${durLabel(s.duration_ms)}<br>
          ${sourceBadge(s.source)} ${captureBadge(s.capture_point)}
        </div>
      </div>
      <div class="meta-grid">
        <div class="meta-cell"><div class="k">设备</div><div class="v">${s.device_id || '—'}</div></div>
        <div class="meta-cell"><div class="k">创建时间</div><div class="v">${dateTimeLabel(s.created_at || s.day)}</div></div>
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

    <!-- 阶段耗时瀑布图 -->
    ${utts.length > 0 ? `
    <div class="card">
      <div class="card-head"><h2>阶段耗时瀑布图</h2></div>
      ${renderWaterfall(utts, dur)}
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
window.addEventListener("hashchange", () => {
  const sessionId = decodeURIComponent((window.location.hash || "").replace(/^#/, ""));
  if (!sessionId || sessionId === currentSession) return;
  if (sessionsCache.some(s => s.session_id === sessionId)) {
    openSession(sessionId, { updateHash: false });
  }
});
