# Wangyutang Common API Manager

这是王语棠平台的公共接口管理模块。当前默认 ASR 使用 LV 服务器上的 `qwen3-asr-1.7b`，默认 TTS 使用 LV 服务器上的 `qwen3-tts-12hz-1.7b-customvoice`，默认 LLM 使用 LV 服务器上的 `qwen3.5-9b`。

## 原则和宗旨

`common_api_manager` 的核心目标，是作为平台统一适配层，对外部工具、模型和供应商接口做收口，对业务模块提供长期稳定的统一接口。

原则：

- 不管上游实际使用的是哪一种工具、模型、云服务或第三方接口，业务侧都尽量只对接 `common_api_manager`。
- 上游差异，包括鉴权方式、请求格式、返回结构、模型切换、参数变化，都优先在 `common_api_manager` 内部消化。
- 业务模块只对齐平台统一接口，不直接依赖外部供应商接口细节。
- 新增能力时，优先继续扩展 `common_api_manager`，而不是让各业务模块分别接入上游。

宗旨：

- 让业务永远优先对接我们自己的标准接口，而不是直接对接外部接口。
- 避免因为上游模型、平台、供应商或协议变化，导致多个业务模块反复适配。
- 通过统一入口、统一参数风格、统一返回结构，降低长期维护成本。
- 让平台后续扩展 ASR、TTS、视觉、多模态等能力时，保持一致的接入方式。

可以把这个模块理解为：

```text
上游接口经常变化
-> common_api_manager 做统一适配和收口
-> 下游业务只调用平台标准接口
```

目标不是把所有业务逻辑都放进来，而是把“通用能力的外部接口适配”集中管理，避免业务层直接暴露在上游变化面前。

## 当前公网地址

在线页面：

```text
https://www.wangyutang.cn/common/
```

健康检查：

```text
GET https://www.wangyutang.cn/common/api/health
```

公网能力接口当前暂不启用 API Key，公网侧统一通过 `/common/api/*` 路径收口。

语音转文本：

```text
POST https://www.wangyutang.cn/common/api/asr/transcribe
```

文本转语音：

```text
POST https://www.wangyutang.cn/common/api/tts/speech
POST https://www.wangyutang.cn/common/api/tts/synthesize
```

LLM 聊天：

```text
GET  https://www.wangyutang.cn/common/api/llm/models
POST https://www.wangyutang.cn/common/api/llm/chat
GET  https://www.wangyutang.cn/common/api/llm/qwen3-32b/health
POST https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat
POST https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat/completions
```

当前默认上游：

```text
ASR: http://39.156.151.204:8000/v1/audio/transcriptions
     model=qwen3-asr-1.7b

TTS: http://39.156.151.204:8001/v1/audio/speech
     model=qwen3-tts-12hz-1.7b-customvoice
     voice=vivian

LLM: http://127.0.0.1:8002/v1/chat/completions
     model=qwen3.5-9b
     note=当前 8002 已在 lv_server 本机验证，公网 39.156.151.204:8002 未开放。

LLM Tools: https://dashscope.aliyuncs.com/api/v1
           compatible=https://dashscope.aliyuncs.com/compatible-mode/v1
           model=qwen3-32b
           note=DashScope qwen3-32b tools/tool_calls 专用入口，不覆盖默认 qwen3.5-9b。
```

请求格式：

```text
multipart/form-data
```

字段：

```text
file      WAV 音频文件，推荐 16-bit PCM WAV
language  可选，默认 zh
```

## 如何调用

打开在线接口网页：

```text
https://www.wangyutang.cn/common/
```

健康检查：

```powershell
curl.exe -sS https://www.wangyutang.cn/common/api/health
```

公网测试示例：

```powershell
curl.exe -sS --max-time 120 `
  -F "file=@C:\Users\Administrator\Desktop\Workspace\Project_Codex\Project_ASR\online_api\samples\hello_zh.wav" `
  -F "language=zh" `
  https://www.wangyutang.cn/common/api/asr/transcribe
```

Python 调用示例：

```python
import requests

url = "https://www.wangyutang.cn/common/api/asr/transcribe"

with open("hello_zh.wav", "rb") as audio:
    response = requests.post(
        url,
        data={"language": "zh"},
        files={"file": ("hello_zh.wav", audio, "audio/wav")},
        timeout=120,
    )

response.raise_for_status()
print(response.json()["text"])
```

浏览器 JavaScript 调用示例：

```javascript
const form = new FormData();
form.append("language", "zh");
form.append("file", fileInput.files[0]);

const response = await fetch("https://www.wangyutang.cn/common/api/asr/transcribe", {
  method: "POST",
  body: form,
});

const data = await response.json();
console.log(data.text);
```

返回 JSON 主要字段：

```json
{
  "text": "识别文本",
  "model": "qwen3-asr-1.7b",
  "provider": "lv_qwen_asr",
  "raw": {
    "text": "识别文本"
  }
}
```

TTS 调用示例：

```powershell
curl.exe -X POST https://www.wangyutang.cn/common/api/tts/speech `
  -H "Content-Type: application/json" `
  -d "{\"input\":\"你好，欢迎使用王语棠语音服务。\",\"voice\":\"vivian\",\"language\":\"chinese\",\"response_format\":\"wav\"}" `
  -o output.wav
```

LLM 调用示例：

```powershell
curl.exe -X POST https://www.wangyutang.cn/common/api/llm/chat `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"qwen3.5-9b\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"temperature\":0.7,\"max_tokens\":512}"
```

## 图像理解（Vision）

图像理解目前提供两条公网接口，统一收口于 `common_api_manager`：

| 接口 | 路径前缀 | 后端模型 | 特点 |
|---|---|---|---|
| **lv**（推荐） | `/common/api/vision/lv/` | Qwen2.5-VL-7B-Q4KM（lv_server RTX 4090） | ~0.4s，速度快 6x |
| **spark** | `/common/api/vision/spark/` | Qwen3.6-35B-A3B-NVFP4（spark-c9a7 GB10） | ~2.5s，回答更详细 |

两条接口**请求格式完全一致**，可直接切换。

### 接口地址

```text
# lv_server Qwen2.5-VL-7B（推荐，速度快）
GET  https://www.wangyutang.cn/common/api/vision/lv/health
GET  https://www.wangyutang.cn/common/api/vision/lv/models
POST https://www.wangyutang.cn/common/api/vision/lv/analyze-json   # base64 JSON（推荐）
POST https://www.wangyutang.cn/common/api/vision/lv/analyze        # multipart 文件上传

# spark-c9a7 Qwen3.6-35B（回答更丰富）
GET  https://www.wangyutang.cn/common/api/vision/spark/health
GET  https://www.wangyutang.cn/common/api/vision/spark/models
POST https://www.wangyutang.cn/common/api/vision/spark/analyze-json
POST https://www.wangyutang.cn/common/api/vision/spark/analyze
```

### base64 JSON 方式（推荐）

请求体（`Content-Type: application/json`）：

```json
{
  "image_base64": "<图片 base64 字符串>",
  "question": "描述这张图片",
  "filename": "image.jpg"
}
```

字段说明：

```text
image_base64  必填，JPEG / PNG / WebP 图片的 base64 编码（超过 10MB 拒绝，超过 1280px 长边自动缩放）
question      可选，默认"请描述这张图片，并提取其中的文字和关键信息。"
filename      可选，用于推断图片格式，默认 image.jpg
```

返回 JSON（lv 与 spark 格式相同，provider 字段不同）：

```json
{
  "text": "模型回答",
  "model": "qwen25vl7b-q4km.gguf",
  "provider": "lv_qwen_vision",
  "image": {
    "original_width": 640,
    "original_height": 480,
    "width": 640,
    "height": 480,
    "bytes": 12345,
    "format": "jpeg"
  },
  "raw": { "...": "后端原始响应，含 usage 统计" }
}
```

### curl 调用示例

```bash
IMAGE_B64=$(base64 -w 0 /path/to/image.jpg)

# lv（快）
curl -s -X POST https://www.wangyutang.cn/common/api/vision/lv/analyze-json \
  -H "Content-Type: application/json" \
  -d "{\"image_base64\": \"${IMAGE_B64}\", \"question\": \"这张图片里有什么？\"}"

# spark（更详细）
curl -s -X POST https://www.wangyutang.cn/common/api/vision/spark/analyze-json \
  -H "Content-Type: application/json" \
  -d "{\"image_base64\": \"${IMAGE_B64}\", \"question\": \"这张图片里有什么？\"}"
```

Windows PowerShell：

```powershell
$bytes = [System.IO.File]::ReadAllBytes("C:\path\to\image.jpg")
$b64 = [Convert]::ToBase64String($bytes)
$body = @{ image_base64 = $b64; question = "这张图片里有什么？" } | ConvertTo-Json

curl.exe -s -X POST https://www.wangyutang.cn/common/api/vision/lv/analyze-json `
  -H "Content-Type: application/json" `
  -d $body
```

### Python 调用示例

```python
import base64
import requests

def analyze_image(image_path: str, question: str = "描述这张图片", provider: str = "lv") -> str:
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    url = f"https://www.wangyutang.cn/common/api/vision/{provider}/analyze-json"
    resp = requests.post(url, json={"image_base64": image_b64, "question": question}, timeout=60)
    resp.raise_for_status()
    return resp.json()["text"]

print(analyze_image("photo.jpg", "图中有哪些物体？"))            # lv（默认，快）
print(analyze_image("photo.jpg", "详细描述图片", provider="spark"))  # spark（详细）
```

用 PIL 生成测试图并发送：

```python
from PIL import Image
import io, base64, requests

img = Image.new("RGB", (640, 480), (50, 150, 220))
buf = io.BytesIO()
img.save(buf, format="JPEG", quality=85)
b64 = base64.b64encode(buf.getvalue()).decode()

resp = requests.post(
    "https://www.wangyutang.cn/common/api/vision/lv/analyze-json",
    json={"image_base64": b64, "question": "这是什么颜色的图片？"},
    timeout=30,
)
print(resp.json()["text"])  # → "红色"
```

### multipart 文件上传方式

```bash
curl -s -X POST https://www.wangyutang.cn/common/api/vision/lv/analyze \
  -F "file=@/path/to/image.jpg" \
  -F "question=图中有什么内容？"
```

```python
import requests

with open("image.jpg", "rb") as f:
    resp = requests.post(
        "https://www.wangyutang.cn/common/api/vision/lv/analyze",
        files={"file": ("image.jpg", f, "image/jpeg")},
        data={"question": "图中有什么内容？"},
        timeout=30,
    )
print(resp.json()["text"])
```

### 健康检查

```bash
curl https://www.wangyutang.cn/common/api/vision/lv/health
# → {"provider":"lv_qwen_vision","model":"qwen25vl7b-q4km.gguf",...}

curl https://www.wangyutang.cn/common/api/vision/spark/health
# → {"provider":"spark_qwen_vision","model":"qwen3.6-35b-a3b-fp8",...}
```

### 实测性能（2026-07-01，18 张 640×480 JPEG，公网外部调用验证）

| 接口 | 模型 | 均值响应时间 | 说明 |
|---|---|---|---|
| `vision/lv/analyze-json` | Qwen2.5-VL-7B-Q4KM（RTX 4090） | **~0.4s** | 已验证 ✅ |
| `vision/spark/analyze-json` | Qwen3.6-35B-A3B-NVFP4（GB10） | **~2.5s** | 已验证 ✅ |

lv 比 spark 快约 **6x**。详细基准测试（18 张图）见 `tests/vision_benchmark/`。

### 当前上游配置

```text
lv_server VL:  http://39.156.151.204:8015/v1/chat/completions
               model=qwen25vl7b-q4km.gguf（llama-server，GPU 0，RTX 4090）
               common_api_manager 直接 HTTP 调用，~0.4s/张

Spark Vision:  spark-c9a7:8000/v1/chat/completions
               model=qwen3.6-35b-a3b-fp8（vLLM，GB10）
               经腾讯云 SSH tunnel :18000 → spark-c9a7:8000 路由，~2.5s/张
               图片超过 1280px 自动缩放，超过 10MB 拒绝
```

---

qwen3-32b tools 调用示例：

```powershell
curl.exe -X POST https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat/completions `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"qwen3-32b\",\"messages\":[{\"role\":\"user\",\"content\":\"What is the weather in Beijing? Use the get_weather tool.\"}],\"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"description\":\"Get weather for a city\",\"parameters\":{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\",\"description\":\"city name, e.g. Beijing\"}},\"required\":[\"city\"]}}}],\"tool_choice\":{\"type\":\"function\",\"function\":{\"name\":\"get_weather\"}},\"max_tokens\":256,\"temperature\":0.1}"
```

兼容入口：

```text
POST https://www.wangyutang.cn/audio/api/asr/transcribe
```

`audio_recognition` 的兼容入口会转发到 common API。新业务优先直接调用 `/common/api/asr/transcribe`。

## 目录结构

当前目录按“一个 API 能力一个子 module 文件夹”组织：

```text
common_api_manager/
  app.py
  common/
    settings.py
  modules/
    health_check/
      router.py
      README.md
    asr_transcribe/
      router.py
      service.py
    lv_qwen_asr/
      router.py
      service.py
    lv_qwen_tts/
      router.py
      service.py
      README.md
  static/
    index.html
    app.js
    style.css
```

说明：

- `app.py` 只负责创建 FastAPI、挂载静态页、注册各子模块路由。
- `modules/health_check` 对应健康检查接口。
- `modules/asr_transcribe` 对应公共语音转文本入口，默认转到 LV Qwen3 ASR。
- `modules/lv_qwen_asr` 对应 LV 服务器 `qwen3-asr-1.7b`。
- `modules/lv_qwen_tts` 对应 LV 服务器 `qwen3-tts-12hz-1.7b-customvoice`。
- `modules/lv_qwen_chat` 对应 LV 服务器 `qwen3.5-9b`。

已验证返回：

```text
你好，这是王语棠语音识别接口测试。
```

## 本地启动

在当前目录新建 `.env`：

```env
ASR_LANGUAGE=zh
LV_ASR_BASE_URL=http://39.156.151.204:8000
LV_ASR_MODEL=qwen3-asr-1.7b
LV_TTS_BASE_URL=http://39.156.151.204:8001
LV_TTS_MODEL=qwen3-tts-12hz-1.7b-customvoice
LV_TTS_VOICE=vivian
LV_CHAT_BASE_URL=http://127.0.0.1:8002
LV_CHAT_MODEL=qwen3.5-9b
DASHSCOPE_LLM_API_KEY=你的 DashScope API Key
DASHSCOPE_BASE_HTTP_API_URL=https://dashscope.aliyuncs.com/api/v1
DASHSCOPE_REALTIME_URL=wss://dashscope.aliyuncs.com/api-ws/v1/realtime
DASHSCOPE_COMPATIBLE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
TEXT_MODEL=qwen3-32b
MODEL_USAGE_COLLECTOR_URL=http://127.0.0.1:18080/usage
```

腾讯云统一转发到 LV 的推荐链路：

```text
公网调用方
-> https://www.wangyutang.cn/common/api/llm/chat
-> 腾讯云 common_api_manager
-> 腾讯云 127.0.0.1:18002
-> SSH tunnel
-> LV 127.0.0.1:8002
-> qwen3.5-9b
```

腾讯云上的 `common_api_manager` 推荐配置：

```env
LV_CHAT_BASE_URL=http://127.0.0.1:18002
LV_CHAT_MODEL=qwen3.5-9b
DASHSCOPE_LLM_API_KEY=你的 DashScope API Key
DASHSCOPE_BASE_HTTP_API_URL=https://dashscope.aliyuncs.com/api/v1
DASHSCOPE_REALTIME_URL=wss://dashscope.aliyuncs.com/api-ws/v1/realtime
DASHSCOPE_COMPATIBLE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
TEXT_MODEL=qwen3-32b
MODEL_USAGE_COLLECTOR_URL=http://127.0.0.1:18080/usage
```

SSH tunnel 可参考：

```text
systemd/common-api-lv-qwen-chat-tunnel.service
systemd/common-api.env.example
```

模型调用量统计：

```text
LV collector: http://127.0.0.1:18080/usage
事件明细: /data/models/logs/model_usage_events.jsonl
汇总结果: /data/models/logs/model_usage_summary.json
脚本位置: /data/models/logs/model_usage_collector.py
启动脚本: /data/models/logs/start_model_usage_collector.sh
```

统计口径：

```text
ASR: 统计识别输出文本的估算 token
TTS: 统计输入待合成文本的估算 token
LLM: 统计 messages 输入和回复输出的估算 token；若上游返回 usage，则优先使用上游 usage
```

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

启动本地服务：

```powershell
cd C:\Users\Administrator\Desktop\Workspace\Project_Codex\wangyutang_platform\common_api_manager
.\start_api.ps1
```

本地页面：

```text
http://127.0.0.1:8100/
```

本地接口：

```text
GET  http://127.0.0.1:8100/health
GET  http://127.0.0.1:8100/api/health
POST http://127.0.0.1:8100/api/transcribe
POST http://127.0.0.1:8100/api/asr/transcribe
```

说明：

- `/api/transcribe` 是原始接口。
- `/api/asr/transcribe` 是为了和腾讯云公共接口路径保持一致新增的接口。
- 本地 `start_api.ps1` 当前监听 `0.0.0.0:8100`，方便同局域网设备访问。

## 数据链路

本地链路：

```text
浏览器 / curl
-> http://127.0.0.1:8100/
-> FastAPI app.py
-> WAV 转 PCM / 16k 重采样
-> LV qwen3-asr-1.7b HTTP 接口
-> 返回识别文本 JSON
```

服务器 ASR/TTS 链路：

```text
浏览器 / API 调用方
-> http://<host>:8101/
-> common-api.service
-> /root/wangyutang_platform/common_api/app_src/app.py
-> LV 39.156.151.204:8000/8001
-> 返回 ASR JSON 或 TTS WAV
```

服务器 LLM 链路：

```text
浏览器 / API 调用方
-> http://<host>:8101/api/llm/chat
-> common-api.service
-> 腾讯云 127.0.0.1:18002
-> SSH tunnel
-> LV 127.0.0.1:8002
-> qwen3.5-9b
-> 返回聊天 JSON
```

## 子模块与接口

`modules/health_check/`

```text
GET /health
GET /api/health
```

`modules/asr_transcribe/`

```text
POST /api/transcribe
POST /api/asr/transcribe
```

## 关键文件

```text
app.py              FastAPI 后端，负责注册统一公共接口
common/settings.py  公共配置与环境变量加载
modules/            API 子模块目录
static/index.html   在线录音/上传页面
static/app.js       浏览器录音、WAV 编码、上传识别
static/style.css    页面样式
start_api.ps1       Windows 本地启动脚本
Dockerfile          备用 Docker 构建文件
TENCENT_COMMON_API_DEPLOYMENT.md  腾讯云部署记录
```

## 注意事项

- 当前默认 ASR 模型是 LV 服务器上的 `qwen3-asr-1.7b`。
- 当前默认 TTS 模型是 LV 服务器上的 `qwen3-tts-12hz-1.7b-customvoice`。
- 当前默认 LLM 模型是 LV 服务器上的 `qwen3.5-9b`，公网由腾讯云通过 SSH tunnel 统一转发。
- 如果网页录音准确率不稳定，优先检查浏览器麦克风、降噪、音量和录音距离；上传固定 WAV 通常更稳定。
- 如果本地页面异常，先重启 8100 服务并强制刷新浏览器缓存。
