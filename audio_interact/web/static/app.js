// audio_interact web console: WonderEchoPro + browser mic/speaker + VAD_ASR.
// All audio endpoints are same-origin under /audio_interact/ (Caddy strips the
// prefix). Recorded/uploaded audio goes to /api/audio/segment; VAD_ASR streams
// 16k PCM over /ws/audio. The segment endpoint runs ASR -> wake gate ->
// robot_sandbox /api/command server-side and returns the full result + TTS.

const TARGET_RATE = 16000;
const DEVICE_ID = "web-audio";
const RECORD_SECONDS = 4;

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const setStatus = (t) => { statusEl.textContent = t; };

// ---- WS URL from current location (works behind the /audio_interact/ prefix) ----
function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const base = location.pathname.replace(/\/[^/]*$/, "/"); // dir of current page
  return `${proto}://${location.host}${base}ws/audio`;
}

// ---- health ----
async function pollHealth() {
  const el = $("health");
  try {
    const r = await fetch("./api/health", { cache: "no-store" });
    const d = await r.json();
    el.textContent = `● ${d.service || "audio-interact"} · VAD ${d.vad || "?"}`;
    el.className = "health ok";
  } catch {
    el.textContent = "● 服务不可达";
    el.className = "health err";
  }
}

// ---- tabs ----
const sections = {
  wonder: $("wonderSection"),
  browser: $("browserSection"),
  vad: $("vadSection"),
  replay: $("replaySection"),
  tts: $("ttsSection"),
};
let currentMode = "wonder";
function showMode(mode) {
  currentMode = mode;
  sections.wonder.classList.toggle("hidden", mode !== "wonder");
  sections.browser.classList.toggle("hidden", mode !== "browser");
  sections.vad.classList.toggle("hidden", mode !== "vad");
  sections.replay.classList.toggle("hidden", mode !== "replay");
  sections.tts.classList.toggle("hidden", mode !== "vad");  // TTS 合成随 VAD_ASR_TTS 一起显示
  $("modeWonderBtn").classList.toggle("active", mode === "wonder");
  $("modeBrowserBtn").classList.toggle("active", mode === "browser");
  $("modeVadBtn").classList.toggle("active", mode === "vad");
  $("modeReplayBtn").classList.toggle("active", mode === "replay");
  if (mode !== "vad") stopVad();
  if (mode !== "replay") stopReplay();
}
$("modeWonderBtn").onclick = () => showMode("wonder");
$("modeBrowserBtn").onclick = () => showMode("browser");
$("modeVadBtn").onclick = () => showMode("vad");
$("modeReplayBtn").onclick = () => showMode("replay");

// ---- result rendering ----
function renderResult(d) {
  const wake = d.wake_status || "—";
  // segment (/api/audio/segment) nests the skill under command.skill_id;
  // the VAD_ASR streaming path (/ws/audio -> _process) returns it top-level
  // as skill_id. Accept both shapes so all three modes display consistently.
  const skillId = (d.command && d.command.skill_id) || d.skill_id || "";
  const hasText = Boolean(d.text);
  const skill = skillId || (hasText ? "(无技能)" : "—");
  $("rText").textContent = d.text || "（空）";
  const wakeEl = $("rWake");
  wakeEl.textContent = wake;
  wakeEl.className = "out " + (wake === "awake" || wake === "woken" ? "ok" : wake === "asleep" ? "warn" : "");
  $("rSkill").textContent = skill;
  $("rSkill").className = "out " + (skillId ? "ok" : "");
  $("rTts").textContent = d.tts_text || "—";
  $("rRaw").textContent = JSON.stringify(d, null, 2);
  if (d.tts_audio_base64) playBase64Wav(d.tts_audio_base64);
}
function playBase64Wav(b64) {
  try {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
    const a = new Audio(url);
    a.onended = () => URL.revokeObjectURL(url);
    a.play().catch(() => {});
  } catch (e) { console.error(e); }
}

// ---- WAV encode / downsample (ported from robot_sandbox dashboard) ----
function downsample(samples, srcRate, dstRate) {
  if (srcRate === dstRate) return samples;
  const ratio = srcRate / dstRate;
  const out = new Float32Array(Math.max(1, Math.round(samples.length / ratio)));
  for (let i = 0; i < out.length; i += 1) {
    const s = i * ratio, l = Math.floor(s), r = Math.min(l + 1, samples.length - 1);
    out[i] = samples[l] * (1 - (s - l)) + samples[r] * (s - l);
  }
  return out;
}
function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i += 1) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, samples.length * 2, true);
  let o = 44;
  for (const s of samples) { const x = Math.max(-1, Math.min(1, s)); v.setInt16(o, x < 0 ? x * 0x8000 : x * 0x7fff, true); o += 2; }
  return new Blob([buf], { type: "audio/wav" });
}
function encodePcm16(samples) {
  const buf = new ArrayBuffer(samples.length * 2);
  const v = new DataView(buf);
  let o = 0;
  for (const s of samples) { const x = Math.max(-1, Math.min(1, s)); v.setInt16(o, x < 0 ? x * 0x8000 : x * 0x7fff, true); o += 2; }
  return buf;
}

async function postSegment(wavBlob, filename = "web.wav") {
  const form = new FormData();
  form.append("file", wavBlob, filename);
  form.append("device_id", DEVICE_ID);
  const r = await fetch("./api/audio/segment", { method: "POST", body: form });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail ? JSON.stringify(d.detail) : "segment failed");
  return d;
}

// ---- upload file (web模式 便捷选项) ----
$("uploadInput").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    setStatus("上传识别中…");
    const d = await postSegment(file, file.name || "upload.wav");
    renderResult(d);
    setStatus(`识别完成：${d.text || "（空）"}`);
  } catch (err) { setStatus("上传失败：" + err.message); }
  e.target.value = "";
};

// ---- WonderEchoPro settings ----
async function loadSettings() {
  try {
    const d = await (await fetch("./api/settings", { cache: "no-store" })).json();
    const s = d.settings || {};
    $("wonderStatus").textContent = `当前输入模式：${s.input_mode || "?"} · 手动采集：${s.manual_recording_enabled ? "开" : "关"}`;
    $("manualStartBtn").disabled = Boolean(s.manual_recording_enabled);
    $("manualStopBtn").disabled = !s.manual_recording_enabled;
  } catch { $("wonderStatus").textContent = "读取设置失败"; }
}
$("applyWonderBtn").onclick = async () => {
  await fetch("./api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ input_mode: "wonderechopro" }) });
  setStatus("已设为 WonderEchoPro 模式"); loadSettings();
};
$("manualStartBtn").onclick = async () => {
  await fetch("./api/manual-recording/start", { method: "POST" });
  setStatus("已开始采集"); loadSettings();
};
$("manualStopBtn").onclick = async () => {
  await fetch("./api/manual-recording/stop", { method: "POST" });
  setStatus("已停止采集"); loadSettings();
};

// ---- Continuous VAD streaming (shared by web模式 and VAD_ASR_TTS) ----
// ui = { stateEl, barEl, startBtn, stopBtn }
let vad = null;
function _setState(ui, t, live) { if (ui.stateEl) { ui.stateEl.textContent = t; ui.stateEl.style.color = live ? "var(--ok)" : "var(--muted)"; } }
function _setBar(ui, v) { if (ui.barEl) ui.barEl.style.width = `${Math.min(100, Math.round(v * 100))}%`; }

async function startVadStream(ui, route = true) {
  if (vad) return;
  if (!navigator.mediaDevices?.getUserMedia) { setStatus("此浏览器不支持麦克风采集（需 HTTPS + 授权）"); return; }
  _setState(ui, "启动中…", true);
  setStatus("正在请求麦克风权限…");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    const ctx = new AudioContext();
    await ctx.resume();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(2048, 1, 1);
    const socket = new WebSocket(wsUrl());
    socket.binaryType = "arraybuffer";
    vad = { ctx, src, proc, stream, socket, ready: false, ui };
    if (ui.startBtn) ui.startBtn.disabled = true;
    if (ui.stopBtn) ui.stopBtn.disabled = false;

    socket.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer && ev.data.byteLength > 0) {
        const url = URL.createObjectURL(new Blob([ev.data], { type: "audio/wav" }));
        const a = new Audio(url); a.onended = () => URL.revokeObjectURL(url); a.play().catch(() => {});
        return;
      }
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "stream_ready") { vad.ready = true; _setState(ui, "流式接收中", true); setStatus("已连接云端 VAD"); }
      else if (m.type === "vad") _setBar(ui, Number(m.probability || 0));
      else if (m.type === "speech_start") _setState(ui, "检测到语音", true);
      else if (m.type === "speech_end" || m.type === "asr_started") _setState(ui, "识别中…", true);
      else if (m.type === "result" || m.type === "error") { renderResult(m); setStatus(`${m.status || m.stage || "ok"}`); _setState(ui, vad ? "流式接收中" : "已停止", Boolean(vad)); }
      else if (m.type === "stream_stopped") _setState(ui, "已停止", false);
    };

    await new Promise((res, rej) => { socket.onopen = res; socket.onerror = () => rej(new Error("WebSocket 连接失败")); });
    socket.send(JSON.stringify({ type: "start_stream", session_id: (crypto.randomUUID ? crypto.randomUUID().slice(0, 12) : String(Date.now())), device_id: DEVICE_ID, sample_rate: TARGET_RATE, route }));

    proc.onaudioprocess = (e) => {
      if (!vad || socket.readyState !== WebSocket.OPEN || !vad.ready) return;
      const input = e.inputBuffer.getChannelData(0);
      const frame = new Float32Array(input);
      const rms = Math.sqrt(frame.reduce((s, x) => s + x * x, 0) / frame.length);
      _setBar(ui, Math.min(1, rms * 6));
      socket.send(encodePcm16(downsample(frame, ctx.sampleRate, TARGET_RATE)));
    };
    // Route proc through a permanently-muted gain node so the AudioContext
    // graph stays connected without looping mic audio back to the speakers.
    // Without this, AEC on the browser side silences the mic entirely.
    const muteGain = ctx.createGain();
    muteGain.gain.value = 0;
    src.connect(proc);
    proc.connect(muteGain);
    muteGain.connect(ctx.destination);
    _setState(ui, "连接云端 VAD…", true);
  } catch (e) { setStatus("启动失败：" + e.message); stopVadStream(); }
}
function stopVadStream() {
  if (!vad) return;
  const r = vad; vad = null;
  try { if (r.socket.readyState === WebSocket.OPEN) { r.socket.send(JSON.stringify({ type: "stop_stream" })); setTimeout(() => r.socket.close(), 200); } } catch {}
  try { r.proc.disconnect(); r.src.disconnect(); r.stream.getTracks().forEach((t) => t.stop()); r.ctx.close().catch(() => {}); } catch {}
  _setBar(r.ui, 0); _setState(r.ui, "未启动", false);
  if (r.ui.startBtn) r.ui.startBtn.disabled = false;
  if (r.ui.stopBtn) r.ui.stopBtn.disabled = true;
}
const stopVad = stopVadStream;  // back-compat for showMode/replay

// web模式: 浏览器麦克风连续 VAD 交互
const WEB_VAD_UI = { stateEl: $("webVadState"), barEl: $("webVadBar"), startBtn: $("webStartBtn"), stopBtn: $("webStopBtn") };
$("webStartBtn").onclick = () => startVadStream(WEB_VAD_UI, true);   // 真实派发+执行
$("webStopBtn").onclick = () => stopVadStream();

// VAD_ASR_TTS: 流式测试/调试(只 ASR+唤醒,不调沙盒/不执行)
const VAD_TEST_UI = { stateEl: $("vadState"), barEl: $("vadBar"), startBtn: $("startVadBtn"), stopBtn: $("stopVadBtn") };
$("startVadBtn").onclick = () => startVadStream(VAD_TEST_UI, false);  // 仅识别,不执行
$("stopVadBtn").onclick = () => stopVadStream();

// ---- 仿真回灌 (replay a long recording through the VAD pipeline) ----
let replay = null;
function setReplayState(t, live) { $("replayState").textContent = t; $("replayState").style.color = live ? "var(--ok)" : "var(--muted)"; }
function setReplayBar(v) { $("replayBar").style.width = `${Math.min(100, Math.round(v * 100))}%`; }
function appendReplayLog(d) {
  const skillId = (d.command && d.command.skill_id) || d.skill_id || "";
  const box = document.createElement("div");
  box.className = "replay-item";
  const ok = skillId ? "ok" : "";
  box.innerHTML =
    `<div class="replay-text">${d.text || "（空）"}</div>` +
    `<div class="replay-meta">唤醒 <b>${d.wake_status || "—"}</b> · 技能 <b class="${ok}">${skillId || "—"}</b> · TTS ${d.tts_text || "—"}</div>`;
  $("replayLog").prepend(box);
}

$("replayInput").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file || replay) return;
  try {
    setStatus("解码回灌音频…");
    const buf = await file.arrayBuffer();
    const ctx = new AudioContext();
    const audio = await ctx.decodeAudioData(buf);
    const samples = downsample(audio.getChannelData(0), audio.sampleRate, TARGET_RATE);
    await ctx.close().catch(() => {});
    $("replayLog").innerHTML = "";

    const socket = new WebSocket(wsUrl());
    socket.binaryType = "arraybuffer";
    replay = { socket, cancelled: false };
    $("replayStopBtn").disabled = false;
    setReplayState("连接云端 VAD…", true);

    socket.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer && ev.data.byteLength > 0) {
        const url = URL.createObjectURL(new Blob([ev.data], { type: "audio/wav" }));
        const a = new Audio(url); a.onended = () => URL.revokeObjectURL(url); a.play().catch(() => {});
        return;
      }
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "vad") setReplayBar(Number(m.probability || 0));
      else if (m.type === "speech_start") setReplayState("检测到语音…", true);
      else if (m.type === "speech_end" || m.type === "asr_started") setReplayState("识别中…", true);
      else if (m.type === "result") { renderResult(m); appendReplayLog(m); setReplayState("回灌中…", true); }
    };

    await new Promise((res, rej) => { socket.onopen = res; socket.onerror = () => rej(new Error("WebSocket 连接失败")); });
    socket.send(JSON.stringify({ type: "start_stream", session_id: (crypto.randomUUID ? crypto.randomUUID().slice(0, 12) : String(Date.now())), device_id: DEVICE_ID, sample_rate: TARGET_RATE }));
    // wait for stream_ready
    await new Promise((res) => { const h = (ev) => { try { if (JSON.parse(ev.data).type === "stream_ready") { socket.removeEventListener("message", h); res(); } } catch {} }; socket.addEventListener("message", h); });

    setReplayState("回灌中…", true);
    setStatus(`回灌 ${(samples.length / TARGET_RATE).toFixed(1)}s 音频…`);
    // Stream PCM in ~128ms chunks, paced ~3x real-time so VAD can segment.
    const chunk = 2048;
    for (let i = 0; i < samples.length; i += chunk) {
      if (!replay || replay.cancelled || socket.readyState !== WebSocket.OPEN) break;
      const slice = samples.subarray(i, i + chunk);
      const rms = Math.sqrt(slice.reduce((s, x) => s + x * x, 0) / slice.length);
      setReplayBar(Math.min(1, rms * 6));
      socket.send(encodePcm16(slice));
      await new Promise((r) => setTimeout(r, 40));
    }
    // trailing silence so the final utterance flushes
    if (replay && !replay.cancelled && socket.readyState === WebSocket.OPEN) {
      const silence = new Float32Array(TARGET_RATE);
      socket.send(encodePcm16(silence));
      await new Promise((r) => setTimeout(r, 2500));
    }
    setStatus("回灌完成");
    setReplayState("完成", false);
    stopReplay();
  } catch (err) {
    setStatus("回灌失败：" + err.message);
    stopReplay();
  }
};
$("replayStopBtn").onclick = () => stopReplay();
function stopReplay() {
  if (!replay) return;
  const r = replay; replay = null; r.cancelled = true;
  try { if (r.socket.readyState === WebSocket.OPEN) { r.socket.send(JSON.stringify({ type: "stop_stream" })); setTimeout(() => r.socket.close(), 200); } } catch {}
  setReplayBar(0);
  $("replayStopBtn").disabled = true;
}

// ---- TTS 合成播报 (VAD_ASR_TTS mode) ----
$("ttsSpeakBtn").onclick = async () => {
  const text = ($("ttsInput").value || "").trim();
  if (!text) { setStatus("请输入要播报的文本"); return; }
  $("ttsSpeakBtn").disabled = true;
  $("ttsState").textContent = "合成中…";
  try {
    const r = await fetch("./api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) { let e = {}; try { e = await r.json(); } catch {} throw new Error(e.detail || `HTTP ${r.status}`); }
    const url = URL.createObjectURL(await r.blob());
    const p = $("ttsSpeakPlayer");
    p.src = url; p.play().catch(() => {});
    $("ttsState").textContent = "已播报";
    setStatus("TTS 播报完成");
  } catch (e) {
    $("ttsState").textContent = "失败";
    setStatus("TTS 失败：" + e.message);
  } finally {
    $("ttsSpeakBtn").disabled = false;
  }
};
$("ttsInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("ttsSpeakBtn").click(); });

// ---- WonderEchoPro result polling ----
// The browser isn't involved in Pi WebSocket sessions, so poll the sessions
// API to surface Pi results in the shared result panel.
let _wepLastSessId = "";
async function pollWonderResult() {
  if (currentMode !== "wonder") return;
  try {
    const list = await (await fetch("./api/sessions?limit=10", { cache: "no-store" })).json();
    const latest = (Array.isArray(list) ? list : []).find(s => s.device_id === "turbopi-01");
    if (!latest || latest.session_id === _wepLastSessId) return;
    _wepLastSessId = latest.session_id;
    if (!latest.texts || latest.texts.length === 0) return;
    const det = await (await fetch(`./api/sessions/${latest.session_id}`, { cache: "no-store" })).json();
    const utt = (det.utterances || [])[0];
    if (utt) { renderResult(utt); setStatus(`Pi 识别：${utt.text}`); }
  } catch {}
}

// ---- init ----
pollHealth(); loadSettings(); showMode("wonder");
setInterval(pollHealth, 15000);
setInterval(pollWonderResult, 3000);
