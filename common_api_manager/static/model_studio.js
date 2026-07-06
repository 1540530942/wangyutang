const $ = (id) => document.getElementById(id);
const API_PREFIX = window.location.pathname.startsWith('/common/') ? '/common' : '';
const state = { health: null, selection: null };

function toast(message, bad = false) {
  const node = $('toast');
  node.textContent = message;
  node.classList.toggle('bad', bad);
  node.classList.add('show');
  window.setTimeout(() => node.classList.remove('show'), 3200);
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function setBusy(button, busy, label) {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label : button.dataset.label;
}

async function requestJson(url, options = {}) {
  const response = await fetch(API_PREFIX + url, options);
  const text = await response.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch (error) { data = { text }; }
  if (!response.ok) {
    const detail = data.detail || data.error || text || `HTTP ${response.status}`;
    throw new Error(typeof detail === 'string' ? detail : pretty(detail));
  }
  return data;
}

function applyHealth(data) {
  state.health = data;
  $('healthSignal').classList.add('ok');
  $('healthSignal').classList.remove('bad');
  $('healthText').textContent = '接口在线';
  $('healthDetail').textContent = '模型信息已同步';
  const models = data.models || {};
  $('asrModelLabel').textContent = models.asr || '-';
  $('visionModelLabel').textContent = 'Qwen2.5-VL-7B';
  $('llmModelLabel').textContent = models.llm_tools || models.llm || '-';
  $('sparkModelLabel').textContent = models.spark_llm || '-';
  if (!$('asrModel').value) $('asrModel').value = models.asr || '';
  $('llmModel').value = models.llm_tools || 'qwen3-32b';
  updateAsrPrompt();
  updateLlmEndpoint();
}

async function loadHealth() {
  try {
    applyHealth(await requestJson('/api/health'));
    await loadAudioModelSelection();
  } catch (error) {
    $('healthSignal').classList.add('bad');
    $('healthText').textContent = '健康检查失败';
    $('healthDetail').textContent = error.message;
  }
}

function defaultAudioModel(kind, provider) {
  const models = (state.health && state.health.models) || {};
  if (kind === 'llm') {
    if (provider === 'spark') return models.spark_llm || 'qwen3.6-35b-a3b-fp8';
    if (provider === 'lv') return models.llm || 'Qwen3.5-35B-A3B-Q4_K_M.gguf';
    return models.llm_tools || 'qwen3-32b';
  }
  if (provider === 'lv') return models.lv_vl || 'qwen25vl7b-q4km.gguf';
  if (provider === 'dashscope') return models.vision || 'qwen-vl-plus';
  return models.spark_llm || 'qwen3.6-35b-a3b-fp8';
}

function updateAudioModelDefaults(kind) {
  if (kind === 'llm') $('audioLlmModel').value = defaultAudioModel('llm', $('audioLlmProvider').value);
  if (kind === 'vision') $('audioVisionModel').value = defaultAudioModel('vision', $('audioVisionProvider').value);
  renderAudioModelSelection();
}

function renderAudioModelSelection() {
  const preview = {
    llm: {
      provider: $('audioLlmProvider').value,
      model: $('audioLlmModel').value.trim(),
    },
    vision: {
      provider: $('audioVisionProvider').value,
      model: $('audioVisionModel').value.trim(),
    },
    saved: state.selection || null,
  };
  $('audioModelSelection').textContent = pretty(preview);
}

function applyAudioModelSelection(data) {
  const selection = data.selection || data;
  state.selection = selection;
  if (selection.llm) {
    $('audioLlmProvider').value = selection.llm.provider || 'dashscope';
    $('audioLlmModel').value = selection.llm.model || defaultAudioModel('llm', $('audioLlmProvider').value);
  }
  if (selection.vision) {
    $('audioVisionProvider').value = selection.vision.provider || 'spark';
    $('audioVisionModel').value = selection.vision.model || defaultAudioModel('vision', $('audioVisionProvider').value);
  }
  renderAudioModelSelection();
}

async function loadAudioModelSelection() {
  try {
    applyAudioModelSelection(await requestJson('/api/model-studio/selection'));
  } catch (error) {
    $('audioModelSelection').textContent = error.message;
  }
}

async function saveAudioModelSelection() {
  const payload = {
    llm: {
      provider: $('audioLlmProvider').value,
      model: $('audioLlmModel').value.trim(),
    },
    vision: {
      provider: $('audioVisionProvider').value,
      model: $('audioVisionModel').value.trim(),
    },
  };
  setBusy($('applyAudioModels'), true, '应用中...');
  try {
    const data = await requestJson('/api/model-studio/selection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    applyAudioModelSelection(data);
    toast('audio_recognition 模型选择已更新');
  } catch (error) {
    $('audioModelSelection').textContent = error.message;
    toast(error.message, true);
  } finally {
    setBusy($('applyAudioModels'), false);
  }
}

function updateAsrPrompt() {
  $('asrPrompt').textContent = `language=zh\nmodel=${$('asrModel').value || '(默认)'}`;
}

function messagesPayload() {
  return [
    { role: 'system', content: $('systemPrompt').value.trim() },
    { role: 'user', content: $('userPrompt').value.trim() },
  ].filter((item) => item.content);
}

function llmRoute() {
  const endpoint = $('llmEndpoint').value;
  if (endpoint === 'spark') return '/api/llm/spark-qwen/chat';
  if (endpoint === 'lv') return '/api/llm/chat';
  return '/api/llm/qwen3-32b/chat';
}

function visionRoute() {
  const p = $('visionProvider').value;
  if (p === 'spark') return '/api/vision/spark/analyze-json';
  if (p === 'dashscope') return '/api/vision/dashscope/analyze-json';
  return '/api/vision/lv/analyze-json';
}

function updateVisionEndpoint() {
  $('visionEndpointLabel').textContent = `POST ${visionRoute()}`;
}

function updateLlmEndpoint() {
  const models = (state.health && state.health.models) || {};
  if ($('llmEndpoint').value === 'spark') $('llmModel').value = models.spark_llm || 'qwen3.6-35b-a3b-fp8';
  if ($('llmEndpoint').value === 'lv') $('llmModel').value = models.llm || 'qwen3.5-35b';
  if ($('llmEndpoint').value === 'dashscope') $('llmModel').value = models.llm_tools || 'qwen3-32b';
  $('llmEndpointLabel').textContent = `POST ${llmRoute()}`;
  updateFullPrompt();
}

function updateFullPrompt() {
  const payload = {
    model: $('llmModel').value.trim(),
    messages: messagesPayload(),
    temperature: 0.2,
    max_tokens: 512,
  };
  if ($('llmEndpoint').value === 'spark') {
    payload.chat_template_kwargs = { enable_thinking: false };
  }
  $('llmFullPrompt').textContent = pretty(payload);
}

async function runAsr() {
  const file = $('audioFile').files[0];
  if (!file) return toast('请先选择音频文件', true);
  const body = new FormData();
  body.append('language', 'zh');
  if ($('asrModel').value.trim()) body.append('model', $('asrModel').value.trim());
  body.append('file', file);
  setBusy($('runAsr'), true, '识别中...');
  try {
    updateAsrPrompt();
    const data = await requestJson('/api/asr/transcribe', { method: 'POST', body });
    $('asrResult').value = data.text || data.transcript || pretty(data);
    $('asrRaw').textContent = pretty(data);
    toast('ASR 完成');
  } catch (error) {
    $('asrRaw').textContent = error.message;
    toast(error.message, true);
  } finally {
    setBusy($('runAsr'), false);
  }
}

async function runVision() {
  const file = $('imageFile').files[0];
  if (!file) return toast('请先选择图片', true);
  const question = $('visionQuestion').value.trim() || '请描述这张图片。';
  setBusy($('runVision'), true, '理解中...');
  try {
    const arrayBuffer = await file.arrayBuffer();
    const bytes = new Uint8Array(arrayBuffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
    const image_base64 = btoa(binary);
    const data = await requestJson(visionRoute(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64, question }),
    });
    $('visionResult').value = data.text || pretty(data);
    $('visionRaw').textContent = pretty(data);
    toast('图像理解完成');
  } catch (error) {
    $('visionRaw').textContent = error.message;
    toast(error.message, true);
  } finally {
    setBusy($('runVision'), false);
  }
}

async function runSparkVision() {
  const file = $('sparkImageFile').files[0];
  if (!file) return toast('请先选择图片', true);
  const question = $('sparkVisionQuestion').value.trim() || '请描述这张图片。';
  const model = $('sparkVisionModel').value.trim();
  setBusy($('runSparkVision'), true, '理解中...');
  try {
    const arrayBuffer = await file.arrayBuffer();
    const bytes = new Uint8Array(arrayBuffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
    const image_base64 = btoa(binary);
    const payload = { image_base64, question };
    if (model) payload.model = model;
    const data = await requestJson('/api/vision/spark/analyze-json', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    $('sparkVisionResult').value = data.text || pretty(data);
    $('sparkVisionRaw').textContent = pretty(data);
    toast('Spark 视觉理解完成');
  } catch (error) {
    $('sparkVisionResult').value = '';
    $('sparkVisionRaw').textContent = error.message;
    toast(error.message, true);
  } finally {
    setBusy($('runSparkVision'), false);
  }
}

async function runLlm() {
  const payload = {
    model: $('llmModel').value.trim(),
    messages: messagesPayload(),
    temperature: 0.2,
    max_tokens: 512,
  };
  if ($('llmEndpoint').value === 'spark') payload.chat_template_kwargs = { enable_thinking: false };
  if (!payload.messages.length) return toast('请输入提示词', true);
  setBusy($('runLlm'), true, '生成中...');
  try {
    $('llmFullPrompt').textContent = pretty(payload);
    const data = await requestJson(llmRoute(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    $('llmResult').value = data.text || pretty(data);
    $('llmRaw').textContent = pretty(data);
    toast('LLM 完成');
  } catch (error) {
    $('llmRaw').textContent = error.message;
    toast(error.message, true);
  } finally {
    setBusy($('runLlm'), false);
  }
}

$('loginForm').addEventListener('submit', (event) => {
  event.preventDefault();
  if ($('captchaInput').value.trim() !== '12') {
    $('loginError').textContent = '验证码不正确。提示：12。';
    return;
  }
  sessionStorage.setItem('common_api_model_studio_unlocked', '1');
  $('gate').classList.add('hidden');
  $('app').classList.remove('hidden');
  loadHealth();
});

$('audioFile').addEventListener('change', () => {
  const file = $('audioFile').files[0];
  $('audioName').textContent = file ? `${file.name} · ${Math.round(file.size / 1024)} KB` : 'WAV / MP3 / M4A / OGG';
});
$('imageFile').addEventListener('change', () => {
  const file = $('imageFile').files[0];
  $('imageName').textContent = file ? `${file.name} · ${Math.round(file.size / 1024)} KB` : 'JPG / PNG / WebP';
  const zone = document.querySelector('.image-zone');
  if (!file) return zone.classList.remove('has-image');
  $('imagePreview').src = URL.createObjectURL(file);
  zone.classList.add('has-image');
});
$('sparkImageFile').addEventListener('change', () => {
  const file = $('sparkImageFile').files[0];
  $('sparkImageName').textContent = file ? `${file.name} · ${Math.round(file.size / 1024)} KB` : 'JPG / PNG / WebP';
  const zone = document.querySelector('.panel-spark-vision .image-zone');
  if (!file) return zone.classList.remove('has-image');
  $('sparkImagePreview').src = URL.createObjectURL(file);
  zone.classList.add('has-image');
});
$('runSparkVision').addEventListener('click', runSparkVision);
$('applyAudioModels').addEventListener('click', saveAudioModelSelection);
$('audioLlmProvider').addEventListener('change', () => updateAudioModelDefaults('llm'));
$('audioVisionProvider').addEventListener('change', () => updateAudioModelDefaults('vision'));
$('audioLlmModel').addEventListener('input', renderAudioModelSelection);
$('audioVisionModel').addEventListener('input', renderAudioModelSelection);
$('asrModel').addEventListener('input', updateAsrPrompt);
$('visionProvider').addEventListener('change', updateVisionEndpoint);
$('llmEndpoint').addEventListener('change', updateLlmEndpoint);
$('llmModel').addEventListener('input', updateFullPrompt);
$('systemPrompt').addEventListener('input', updateFullPrompt);
$('userPrompt').addEventListener('input', updateFullPrompt);
$('runAsr').addEventListener('click', runAsr);
$('runVision').addEventListener('click', runVision);
$('runLlm').addEventListener('click', runLlm);

if (sessionStorage.getItem('common_api_model_studio_unlocked') === '1') {
  $('gate').classList.add('hidden');
  $('app').classList.remove('hidden');
  loadHealth();
}
updateAsrPrompt();
updateVisionEndpoint();
updateFullPrompt();
