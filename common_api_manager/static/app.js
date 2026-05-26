const statusEl = document.querySelector("#status");
const recordBtn = document.querySelector("#recordBtn");
const stopBtn = document.querySelector("#stopBtn");
const uploadBtn = document.querySelector("#uploadBtn");
const fileInput = document.querySelector("#fileInput");
const timerEl = document.querySelector("#timer");
const previewEl = document.querySelector("#preview");
const resultEl = document.querySelector("#result");
const rawEl = document.querySelector("#raw");
const elapsedEl = document.querySelector("#elapsed");
const meter = document.querySelector("#meter");
const meterContext = meter.getContext("2d");

const TARGET_SAMPLE_RATE = 16000;
const MAX_RECORDING_MS = 15000;
const REQUEST_TIMEOUT_MS = 120000;

let audioContext = null;
let mediaStream = null;
let sourceNode = null;
let processorNode = null;
let recordingStartedAt = 0;
let timerHandle = null;
let samples = [];
let recordingSampleRate = 48000;
let stopping = false;

function setStatus(text, ok = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("ok", ok);
}

function formatElapsed(ms) {
  const totalSeconds = ms / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.floor(totalSeconds % 60);
  const tenths = Math.floor((totalSeconds % 1) * 10);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

function drawMeter(level = 0) {
  const width = meter.width;
  const height = meter.height;
  meterContext.clearRect(0, 0, width, height);
  meterContext.fillStyle = "#f5f7f8";
  meterContext.fillRect(0, 0, width, height);
  meterContext.fillStyle = "#126c64";
  meterContext.fillRect(0, height - level * height, width, level * height);
  meterContext.strokeStyle = "#d7dee2";
  meterContext.strokeRect(0, 0, width, height);
}

function flattenSamples(chunks) {
  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const result = new Float32Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

function resample(input, fromRate, toRate) {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const length = Math.round(input.length / ratio);
  const output = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const sourceIndex = i * ratio;
    const index = Math.floor(sourceIndex);
    const next = Math.min(index + 1, input.length - 1);
    const frac = sourceIndex - index;
    output[i] = input[index] * (1 - frac) + input[next] * frac;
  }
  return output;
}

function encodeWav(floatSamples, sampleRate) {
  const bytesPerSample = 2;
  const blockAlign = bytesPerSample;
  const buffer = new ArrayBuffer(44 + floatSamples.length * bytesPerSample);
  const view = new DataView(buffer);
  let offset = 0;

  function writeString(value) {
    for (let i = 0; i < value.length; i += 1) {
      view.setUint8(offset, value.charCodeAt(i));
      offset += 1;
    }
  }

  writeString("RIFF");
  view.setUint32(offset, 36 + floatSamples.length * bytesPerSample, true);
  offset += 4;
  writeString("WAVE");
  writeString("fmt ");
  view.setUint32(offset, 16, true);
  offset += 4;
  view.setUint16(offset, 1, true);
  offset += 2;
  view.setUint16(offset, 1, true);
  offset += 2;
  view.setUint32(offset, sampleRate, true);
  offset += 4;
  view.setUint32(offset, sampleRate * blockAlign, true);
  offset += 4;
  view.setUint16(offset, blockAlign, true);
  offset += 2;
  view.setUint16(offset, 16, true);
  offset += 2;
  writeString("data");
  view.setUint32(offset, floatSamples.length * bytesPerSample, true);
  offset += 4;

  for (let i = 0; i < floatSamples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, floatSamples[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: "audio/wav" });
}

async function transcribeBlob(blob, filename = "recording.wav") {
  const form = new FormData();
  form.append("language", "zh");
  form.append("file", blob, filename);
  const started = performance.now();
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  setStatus("识别中");
  elapsedEl.textContent = "";

  try {
    const response = await fetch("./api/asr/transcribe", {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    const elapsed = performance.now() - started;
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data?.detail || "识别请求失败");
    }
    resultEl.value = data.text || "";
    rawEl.textContent = JSON.stringify(data, null, 2);
    elapsedEl.textContent = `${(elapsed / 1000).toFixed(2)}s`;
    setStatus(data.text ? "完成" : "未识别到文本", Boolean(data.text));
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("识别超时，请缩短录音后重试");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function startRecording() {
  samples = [];
  stopping = false;
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      channelCount: 1,
    },
  });
  audioContext = new AudioContext();
  recordingSampleRate = audioContext.sampleRate;
  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);
  processorNode.onaudioprocess = (event) => {
    const channel = event.inputBuffer.getChannelData(0);
    samples.push(new Float32Array(channel));
    let sum = 0;
    for (let i = 0; i < channel.length; i += 1) sum += channel[i] * channel[i];
    drawMeter(Math.min(1, Math.sqrt(sum / channel.length) * 8));
  };
  sourceNode.connect(processorNode);
  processorNode.connect(audioContext.destination);
  recordingStartedAt = performance.now();
  timerHandle = window.setInterval(() => {
    const elapsed = performance.now() - recordingStartedAt;
    timerEl.textContent = formatElapsed(elapsed);
    if (elapsed >= MAX_RECORDING_MS && !stopping) {
      stopRecording().catch((error) => {
        setStatus(`识别失败：${error.message}`);
        recordBtn.disabled = false;
        stopBtn.disabled = true;
      });
    }
  }, 100);
  recordBtn.disabled = true;
  stopBtn.disabled = false;
  setStatus("录音中");
}

async function stopRecording() {
  if (stopping) return;
  stopping = true;
  stopBtn.disabled = true;
  if (timerHandle) window.clearInterval(timerHandle);
  if (processorNode) processorNode.disconnect();
  if (sourceNode) sourceNode.disconnect();
  if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
  if (audioContext) await audioContext.close();
  recordBtn.disabled = false;

  const merged = flattenSamples(samples);
  if (!merged.length) {
    setStatus("没有录到声音");
    return;
  }
  const resampled = resample(merged, recordingSampleRate, TARGET_SAMPLE_RATE);
  const blob = encodeWav(resampled, TARGET_SAMPLE_RATE);
  previewEl.src = URL.createObjectURL(blob);
  await transcribeBlob(blob);
}

recordBtn.addEventListener("click", () => {
  startRecording().catch((error) => {
    setStatus(`录音失败：${error.message}`);
    recordBtn.disabled = false;
    stopBtn.disabled = true;
  });
});

stopBtn.addEventListener("click", () => {
  stopRecording().catch((error) => {
    setStatus(`识别失败：${error.message}`);
    recordBtn.disabled = false;
    stopBtn.disabled = true;
  });
});

uploadBtn.addEventListener("click", () => {
  const file = fileInput.files?.[0];
  if (!file) {
    setStatus("请选择 WAV 文件");
    return;
  }
  previewEl.src = URL.createObjectURL(file);
  transcribeBlob(file, file.name).catch((error) => setStatus(`识别失败：${error.message}`));
});

fetch("./api/health")
  .then((response) => response.json())
  .then((data) => setStatus(data.api_key_configured ? "服务就绪" : "缺少 API Key", Boolean(data.api_key_configured)))
  .catch((error) => setStatus(`检查失败：${error.message}`));

drawMeter();
