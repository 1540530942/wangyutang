const canvas = document.getElementById("face");
const ctx = canvas.getContext("2d");
const controls = document.getElementById("controlPanel");
const statusText = document.getElementById("statusText");
const speakText = document.getElementById("speakText");
const displayMode = new URLSearchParams(window.location.search).get("display") === "1";

if (displayMode) document.body.classList.add("display-mode");

const emotionPresets = {
  neutral: { eyeOpen: 0.95, smile: 0.18, mouthOpen: 0.04, brow: 0, blush: 0.45, sparkle: 0.25, wobble: 0.25 },
  happy: { eyeOpen: 0.78, smile: 0.74, mouthOpen: 0.12, brow: 0.14, blush: 0.85, sparkle: 0.55, wobble: 0.55 },
  joy: { eyeOpen: 1.05, smile: 0.92, mouthOpen: 0.42, brow: 0.22, blush: 1, sparkle: 1, wobble: 0.9 },
  sad: { eyeOpen: 0.58, smile: -0.58, mouthOpen: 0.06, brow: -0.34, blush: 0.25, sparkle: 0.1, wobble: 0.15 },
  angry: { eyeOpen: 0.66, smile: -0.16, mouthOpen: 0.03, brow: -0.68, blush: 0.7, sparkle: 0.25, wobble: 0.45 }
};

const styles = {
  mochi: {
    label: "糯米团",
    bg1: [255, 247, 224], bg2: [246, 235, 255], bg3: [231, 255, 246],
    body: [255, 252, 239], shadow: [236, 200, 218], cheek: [255, 142, 185],
    accent: [255, 186, 112], ink: [86, 61, 86], trim: [255, 255, 255], ears: "nubs", motif: "dango"
  },
  bunny: {
    label: "兔兔屏",
    bg1: [255, 239, 249], bg2: [232, 244, 255], bg3: [255, 246, 218],
    body: [255, 245, 249], shadow: [218, 184, 220], cheek: [255, 135, 179],
    accent: [255, 165, 205], ink: [85, 58, 92], trim: [255, 255, 255], ears: "bunny", motif: "hearts"
  },
  star: {
    label: "星云糖",
    bg1: [247, 239, 255], bg2: [220, 247, 255], bg3: [255, 239, 190],
    body: [248, 245, 255], shadow: [183, 193, 238], cheek: [255, 168, 202],
    accent: [255, 214, 96], ink: [65, 63, 108], trim: [255, 255, 245], ears: "star", motif: "stars"
  },
  panda: {
    label: "熊猫机",
    bg1: [239, 255, 246], bg2: [255, 247, 228], bg3: [232, 238, 255],
    body: [255, 253, 242], shadow: [189, 203, 198], cheek: [255, 148, 162],
    accent: [125, 218, 172], ink: [48, 61, 58], trim: [255, 255, 255], ears: "panda", motif: "leaves"
  }
};

let remoteState = {
  emotion: "neutral", style: "mochi", intensity: 0.65,
  speaking_until: 0, mouth_open_until: 0, blink_nonce: 0,
  message: "", now: Date.now() / 1000, speaking: false, mouth_open: false
};
let renderState = { ...emotionPresets.neutral };
let lastBlinkNonce = 0;
let blinkStart = 0;
let blinkDuration = 150;
let nextBlink = performance.now() + 1600 + Math.random() * 2800;
let doubleBlinkQueued = false;
let lastPollAt = 0;
let connected = false;

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(window.innerWidth * dpr);
  canvas.height = Math.floor(window.innerHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener("resize", resize);
resize();

const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
const mix = (a, b, t) => a + (b - a) * t;
const colorMix = (a, b, t) => a.map((v, i) => Math.round(mix(v, b[i], t)));
const rgba = (c, a = 1) => `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})`;

function roundRect(x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function blobPath(cx, cy, w, h, t, wobble) {
  const k = 0.5522848;
  const ox = w / 2;
  const oy = h / 2;
  const b = 10 * wobble * Math.sin(t * 2.1);
  ctx.beginPath();
  ctx.moveTo(cx, cy - oy - b);
  ctx.bezierCurveTo(cx + ox * k, cy - oy - b, cx + ox + b, cy - oy * k, cx + ox + b, cy);
  ctx.bezierCurveTo(cx + ox + b, cy + oy * k, cx + ox * k, cy + oy + b, cx, cy + oy + b);
  ctx.bezierCurveTo(cx - ox * k, cy + oy + b, cx - ox - b, cy + oy * k, cx - ox - b, cy);
  ctx.bezierCurveTo(cx - ox - b, cy - oy * k, cx - ox * k, cy - oy - b, cx, cy - oy - b);
  ctx.closePath();
}

function forceBlink(duration = 145) {
  blinkStart = performance.now();
  blinkDuration = duration;
}

function blinkFactor(nowMs) {
  if (remoteState.blink_nonce !== lastBlinkNonce) {
    lastBlinkNonce = remoteState.blink_nonce;
    forceBlink(135);
  }
  if (nowMs >= nextBlink) {
    forceBlink(125 + Math.random() * 50);
    doubleBlinkQueued = Math.random() < 0.25;
    nextBlink = nowMs + 2200 + Math.random() * 4300;
  }
  const elapsed = nowMs - blinkStart;
  if (elapsed >= 0 && elapsed <= blinkDuration) return Math.sin((elapsed / blinkDuration) * Math.PI);
  if (doubleBlinkQueued && elapsed > blinkDuration + 90) {
    doubleBlinkQueued = false;
    forceBlink(115);
  }
  return 0;
}

async function pollState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    remoteState = await response.json();
    connected = true;
    lastPollAt = Date.now();
    const style = styles[remoteState.style]?.label || remoteState.style;
    if (statusText) statusText.textContent = `${style} · ${remoteState.emotion}${remoteState.speaking ? " · talking" : ""}`;
  } catch {
    connected = false;
    if (statusText) statusText.textContent = "offline";
  }
}
setInterval(pollState, 360);
pollState();

async function postJson(url, body = {}) {
  await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  await pollState();
}

if (controls) {
  controls.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.emotion) postJson("/api/face/emotion", { emotion: button.dataset.emotion, intensity: 0.9, source: "web", message: button.textContent.trim() });
    if (button.dataset.style) postJson("/api/face/style", { style: button.dataset.style, source: "web", message: styles[button.dataset.style].label });
  });
  document.getElementById("blinkButton")?.addEventListener("click", () => postJson("/api/face/blink"));
  document.getElementById("resetButton")?.addEventListener("click", () => postJson("/api/face/reset"));
  document.getElementById("speakButton")?.addEventListener("click", () => postJson("/api/face/speak", { text: speakText?.value || "你好呀，我变可爱啦", emotion: "joy", source: "web" }));
}

function drawBackground(w, h, t, palette) {
  const g = ctx.createLinearGradient(0, 0, w, h);
  g.addColorStop(0, rgba(palette.bg1, 1));
  g.addColorStop(0.48, rgba(palette.bg2, 1));
  g.addColorStop(1, rgba(palette.bg3, 1));
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);

  ctx.save();
  ctx.globalAlpha = 0.32;
  for (let i = 0; i < 34; i += 1) {
    const x = (i * 137 + t * 16) % (w + 80) - 40;
    const y = (i * 83 + Math.sin(t + i) * 14) % (h + 60) - 30;
    drawMotif(x, y, 8 + (i % 4) * 4, palette, i);
  }
  ctx.restore();

  const rg = ctx.createRadialGradient(w * 0.5, h * 0.55, 20, w * 0.5, h * 0.55, Math.max(w, h) * 0.52);
  rg.addColorStop(0, "rgba(255,255,255,0.46)");
  rg.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = rg;
  ctx.fillRect(0, 0, w, h);
}

function drawMotif(x, y, s, palette, i) {
  ctx.fillStyle = i % 2 ? rgba(palette.cheek, 0.55) : rgba(palette.accent, 0.42);
  if (palette.motif === "hearts") {
    ctx.beginPath();
    ctx.moveTo(x, y + s * 0.35);
    ctx.bezierCurveTo(x - s, y - s * 0.35, x - s * 0.4, y - s, x, y - s * 0.35);
    ctx.bezierCurveTo(x + s * 0.4, y - s, x + s, y - s * 0.35, x, y + s * 0.35);
    ctx.fill();
  } else if (palette.motif === "stars") {
    star(x, y, s, s * 0.45, 5);
    ctx.fill();
  } else if (palette.motif === "leaves") {
    ctx.beginPath();
    ctx.ellipse(x, y, s * 0.75, s * 0.35, -0.7, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.beginPath();
    ctx.arc(x, y, s * 0.45, 0, Math.PI * 2);
    ctx.fill();
  }
}

function star(cx, cy, outer, inner, points) {
  ctx.beginPath();
  for (let i = 0; i < points * 2; i += 1) {
    const r = i % 2 ? inner : outer;
    const a = -Math.PI / 2 + (i * Math.PI) / points;
    const x = cx + Math.cos(a) * r;
    const y = cy + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

function drawAccessories(cx, cy, scale, palette, t) {
  ctx.save();
  ctx.fillStyle = rgba(palette.body, 1);
  ctx.strokeStyle = rgba(palette.ink, 0.18);
  ctx.lineWidth = 4 * scale;
  if (palette.ears === "bunny") {
    for (const side of [-1, 1]) {
      ctx.save();
      ctx.translate(cx + side * 128 * scale, cy - 140 * scale);
      ctx.rotate(side * 0.18);
      roundRect(-28 * scale, -112 * scale, 56 * scale, 132 * scale, 28 * scale);
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = rgba(palette.cheek, 0.45);
      roundRect(-14 * scale, -86 * scale, 28 * scale, 86 * scale, 14 * scale);
      ctx.fill();
      ctx.restore();
    }
  } else if (palette.ears === "panda") {
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.arc(cx + side * 145 * scale, cy - 100 * scale, 44 * scale, 0, Math.PI * 2);
      ctx.fillStyle = rgba(palette.ink, 0.92);
      ctx.fill();
    }
  } else if (palette.ears === "star") {
    for (const side of [-1, 1]) {
      star(cx + side * 150 * scale, cy - 120 * scale + Math.sin(t * 2) * 4, 34 * scale, 15 * scale, 5);
      ctx.fillStyle = rgba(palette.accent, 0.92);
      ctx.fill();
    }
  } else {
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.arc(cx + side * 140 * scale, cy - 112 * scale, 28 * scale, 0, Math.PI * 2);
      ctx.fillStyle = rgba(palette.body, 1);
      ctx.fill(); ctx.stroke();
    }
  }
  ctx.restore();
}

function drawFaceBody(cx, cy, scale, palette, t, wobble) {
  drawAccessories(cx, cy, scale, palette, t);
  ctx.save();
  ctx.shadowColor = rgba(palette.shadow, 0.42);
  ctx.shadowBlur = 28 * scale;
  ctx.shadowOffsetY = 14 * scale;
  blobPath(cx, cy, 380 * scale, 286 * scale, t, wobble);
  const g = ctx.createLinearGradient(cx, cy - 150 * scale, cx, cy + 150 * scale);
  g.addColorStop(0, rgba(palette.trim, 1));
  g.addColorStop(0.58, rgba(palette.body, 1));
  g.addColorStop(1, rgba(colorMix(palette.body, palette.shadow, 0.18), 1));
  ctx.fillStyle = g;
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.lineWidth = 4 * scale;
  ctx.strokeStyle = rgba(palette.ink, 0.16);
  ctx.stroke();
  ctx.restore();
}

function drawEyes(cx, cy, scale, state, palette, blink, t) {
  const open = clamp(state.eyeOpen * (1 - blink * 0.98), 0.04, 1.08);
  const eyeY = cy - 24 * scale;
  for (const side of [-1, 1]) {
    const x = cx + side * 82 * scale;
    ctx.save();
    ctx.translate(x, eyeY);
    ctx.rotate(side * state.brow * 0.14);
    const ew = 78 * scale;
    const eh = 88 * scale * open;
    ctx.fillStyle = rgba(palette.ink, 0.96);
    ctx.beginPath();
    ctx.ellipse(0, 0, ew * 0.42, eh * 0.5, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.92)";
    ctx.beginPath();
    ctx.ellipse(-ew * 0.11, -eh * 0.16, ew * 0.12, Math.max(2, eh * 0.13), -0.5, 0, Math.PI * 2);
    ctx.fill();
    if (remoteState.emotion === "joy") {
      ctx.fillStyle = rgba(palette.accent, 0.9);
      star(ew * 0.13, -eh * 0.03, 9 * scale, 4 * scale, 5);
      ctx.fill();
    }
    ctx.restore();
  }

  ctx.strokeStyle = rgba(palette.ink, 0.75);
  ctx.lineWidth = 7 * scale;
  ctx.lineCap = "round";
  for (const side of [-1, 1]) {
    const x = cx + side * 82 * scale;
    const browY = cy - 92 * scale + state.brow * -16 * scale;
    ctx.beginPath();
    ctx.moveTo(x - side * 34 * scale, browY - side * state.brow * 18 * scale);
    ctx.lineTo(x + side * 26 * scale, browY + side * state.brow * 18 * scale);
    ctx.stroke();
  }
}

function drawCheeks(cx, cy, scale, state, palette, t) {
  ctx.save();
  ctx.globalAlpha = 0.28 + state.blush * 0.44;
  ctx.fillStyle = rgba(palette.cheek, 1);
  for (const side of [-1, 1]) {
    ctx.beginPath();
    ctx.ellipse(cx + side * 130 * scale, cy + 38 * scale, 34 * scale, 18 * scale, side * 0.08, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawMouth(cx, cy, scale, state, palette, mouthPulse) {
  const y = cy + 66 * scale;
  const w = 120 * scale;
  const curve = state.smile;
  const open = clamp(state.mouthOpen + mouthPulse, 0, 1);
  ctx.save();
  ctx.strokeStyle = rgba(palette.ink, 0.9);
  ctx.fillStyle = rgba(palette.cheek, 0.92);
  ctx.lineWidth = 8 * scale;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  if (open > 0.18) {
    ctx.beginPath();
    ctx.ellipse(cx, y + curve * 8 * scale, w * 0.32, (18 + open * 38) * scale, 0, 0, Math.PI * 2);
    ctx.fillStyle = rgba(palette.ink, 0.9);
    ctx.fill();
    ctx.fillStyle = rgba(palette.cheek, 0.88);
    ctx.beginPath();
    ctx.ellipse(cx, y + (18 + open * 24) * scale, w * 0.18, 10 * scale, 0, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.beginPath();
    ctx.moveTo(cx - w * 0.42, y);
    ctx.quadraticCurveTo(cx, y + curve * 58 * scale, cx + w * 0.42, y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawSparkles(cx, cy, scale, state, palette, t) {
  ctx.save();
  ctx.globalAlpha = state.sparkle;
  ctx.fillStyle = rgba(palette.accent, 0.85);
  for (let i = 0; i < 7; i += 1) {
    const a = i * 1.9 + t * 0.8;
    const r = (180 + (i % 3) * 34) * scale;
    star(cx + Math.cos(a) * r, cy + Math.sin(a * 0.8) * r * 0.55, (7 + i % 3 * 3) * scale, (3 + i % 2 * 2) * scale, 5);
    ctx.fill();
  }
  ctx.restore();
}

function drawMessage(w, h, palette) {
  if (displayMode || !remoteState.message) return;
  ctx.save();
  ctx.font = "18px 'Cute Pixel Local', 'Trebuchet MS', sans-serif";
  ctx.textAlign = "center";
  ctx.fillStyle = rgba(palette.ink, 0.58);
  ctx.fillText(remoteState.message.slice(0, 34), w / 2, h - 34);
  ctx.restore();
}

function draw(nowMs) {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const t = nowMs / 1000;
  const palette = styles[remoteState.style] || styles.mochi;
  const target = emotionPresets[remoteState.emotion] || emotionPresets.neutral;
  for (const key of Object.keys(renderState)) renderState[key] = mix(renderState[key], target[key], 0.085);
  const blink = blinkFactor(nowMs);
  const serverNow = remoteState.now || Date.now() / 1000;
  const localNow = serverNow + (Date.now() - lastPollAt) / 1000;
  const speaking = remoteState.speaking_until > localNow;
  const forcedMouth = remoteState.mouth_open_until > localNow;
  const mouthPulse = speaking ? 0.18 + 0.34 * Math.abs(Math.sin(t * 13.5)) + 0.08 * Math.abs(Math.sin(t * 22)) : (forcedMouth ? 0.26 : 0);
  const scale = Math.min(w / 640, h / 520) * (displayMode ? 1.02 : 0.88);
  const cx = w * 0.5;
  const cy = h * 0.52;

  drawBackground(w, h, t, palette);
  drawSparkles(cx, cy, scale, renderState, palette, t);
  drawFaceBody(cx, cy, scale, palette, t, renderState.wobble);
  drawEyes(cx, cy, scale, renderState, palette, blink, t);
  drawCheeks(cx, cy, scale, renderState, palette, t);
  drawMouth(cx, cy, scale, renderState, palette, mouthPulse);
  drawMessage(w, h, palette);

  if (!displayMode) {
    ctx.save();
    ctx.font = "13px 'Cute Pixel Local', 'Trebuchet MS', sans-serif";
    ctx.fillStyle = connected ? rgba(palette.ink, 0.46) : "rgba(180, 70, 80, 0.7)";
    ctx.fillText(`${connected ? "SYNC" : "OFFLINE"} · ${palette.label} · ${remoteState.emotion}`, 22, 28);
    ctx.restore();
  }
  requestAnimationFrame(draw);
}

requestAnimationFrame(draw);
