# Wangyutang Common API Manager

## 可调用模型总览 · 真实性校验

> 以下状态均经过实际接口调用验证（2026-08-18；Spark 一行为 2026-08-18 切换 qwen3.8-27b-fp8 后复验）

| 能力 | 公网接口路径 | 后端模型 | 验证状态 |
|---|---|---|---|
| **健康检查** | `GET /common/api/health` | — | ✅ |
| **语音识别 ASR** | `POST /common/api/asr/transcribe` | Qwen3-ASR-1.7B (lv RTX 4090) | ✅ |
| **语音合成 TTS** | `POST /common/api/tts/speech` | Qwen3-TTS-12Hz-1.7B (lv RTX 4090) | ✅ |
| **LLM 聊天（默认）** | `POST /common/api/llm/chat` | Qwen3.6-35B-A3B-UD-Q4_K_M.gguf (lv RTX 4090) | ✅ |
| **LLM 聊天（DashScope）** | `POST /common/api/llm/qwen3-32b/chat` | Qwen3-32B (阿里云 DashScope) | ✅ |
| **LLM 聊天（Spark）** | `POST /common/api/llm/qwen3.6-35b/chat`（路由名沿用历史，实际模型见下） | qwen3.8-27b-fp8 (spark vLLM) | ✅ |
| **图像理解 lv（推荐）** | `POST /common/api/vision/lv/analyze-json` | Qwen3.6-35B-A3B-UD-Q4_K_M.gguf + mmproj (lv RTX 4090) | ✅ |
| **图像理解 Spark** | `POST /common/api/vision/spark/analyze-json` | qwen3.8-27b-fp8 (spark vLLM) | ✅ |
| **图像理解 DashScope** | `POST /common/api/vision/dashscope/analyze-json` | Qwen-VL-Plus (阿里云) | ✅ |
| **音频转换（ASR 预处理）** | `POST /common/api/audio/convert` | ffmpeg 7.0.2（宿主机本地转码，不走模型） | ✅ |

接口基础 URL：`https://www.wangyutang.cn`

当前默认路由：

```text
ASR    → lv_server:8000   model=qwen3-asr-1.7b
TTS    → lv_server:8001   model=qwen3-tts-12hz-1.7b-customvoice
LLM    → 127.0.0.1:18002 → lv_server:8013 proxy → 8012   model=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
Vision → 127.0.0.1:18002 → lv_server:8013 proxy → 8012   model=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf + mmproj
Spark  → 100.97.66.46:8000 / tunnel :18000               model=qwen3.8-27b-fp8（2026-08-18 前为 qwen3.6-35b-a3b，切换记录见 docs/spark-server-deployment.md）
```

**注意**：`/api/llm/qwen3.6-35b/*` 这组路由名字是历史沿用，不代表当前实际模型——路由名不随模型切换改，
避免破坏已有调用方的 URL；要看当前实际服务的模型名，以 `SPARK_QWEN_MODEL` 环境变量或
`GET /common/api/health` 返回的 `models.spark_llm` 字段为准。

---

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

---

## 当前公网接口

基础地址：`https://www.wangyutang.cn/common`

```text
GET  /api/health                         健康检查

# 语音识别
POST /api/asr/transcribe                 默认 ASR（lv qwen3-asr-1.7b）
GET  /api/asr/qwen3/health
GET  /api/asr/qwen3/models
POST /api/asr/qwen3/transcribe

# 语音合成
POST /api/tts/speech                     默认 TTS（lv qwen3-tts）
POST /api/tts/synthesize
GET  /api/tts/qwen3/health
GET  /api/tts/qwen3/models
POST /api/tts/qwen3/speech

# LLM 聊天
GET  /api/llm/models                     可用模型列表
POST /api/llm/chat                       默认 LLM（lv Qwen3.6-35B-A3B-UD-Q4_K_M.gguf）

POST /api/chat/qwen3/completions         lv Qwen3.6-35B-A3B-UD-Q4_K_M.gguf，OpenAI 格式返回
GET  /api/chat/qwen3/health

POST /api/llm/qwen3-32b/chat            DashScope Qwen3-32B（含 tools）
POST /api/llm/qwen3-32b/chat/completions
GET  /api/llm/qwen3-32b/health

POST /api/llm/qwen3.6-35b/chat         Spark qwen3.8-27b-fp8（路由名沿用历史，见上方说明）
POST /api/llm/qwen3.6-35b/chat/completions
GET  /api/llm/qwen3.6-35b/health

# 图像理解
POST /api/vision/lv/analyze-json        lv Qwen3.6-35B-A3B-UD-Q4_K_M.gguf + mmproj（推荐）
POST /api/vision/lv/analyze
GET  /api/vision/lv/health

POST /api/vision/spark/analyze-json     Spark qwen3.8-27b-fp8（文本/工具/图像共用）
POST /api/vision/spark/analyze
GET  /api/vision/spark/health

POST /api/vision/dashscope/analyze-json DashScope Qwen-VL-Plus
POST /api/vision/dashscope/analyze
GET  /api/vision/dashscope/health
```

---

## 快速调用示例

### 健康检查

```bash
curl https://www.wangyutang.cn/common/api/health
# → {"status":"ok","models":{"asr":"qwen3-asr-1.7b","llm":"Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",...}}
```

### LLM 聊天

```bash
curl -s -X POST https://www.wangyutang.cn/common/api/llm/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"你好"}],"max_tokens":512}'
```

返回：

```json
{
  "text": "你好！有什么我可以帮助你的？",
  "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
  "provider": "lv_qwen_chat",
  "raw": { "choices": [...], "usage": {...} }
}
```

DashScope qwen3-32b（含 tool_calls 支持）：

```bash
curl -s -X POST https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"介绍一下北京"}],"max_tokens":256}'
```

Spark qwen3.8-27b-fp8（路由名仍是 `/qwen3.6-35b/`，沿用历史，实际模型已切换）：

```bash
curl -s -X POST https://www.wangyutang.cn/common/api/llm/qwen3.6-35b/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"介绍一下北京"}],"max_tokens":256}'
```

### 语音识别 ASR

```bash
curl -sS -F "file=@audio.wav" -F "language=zh" \
  https://www.wangyutang.cn/common/api/asr/transcribe
# → {"text":"识别结果","model":"qwen3-asr-1.7b","provider":"lv_qwen_asr"}
```

```python
import requests

with open("audio.wav", "rb") as f:
    resp = requests.post(
        "https://www.wangyutang.cn/common/api/asr/transcribe",
        data={"language": "zh"},
        files={"file": ("audio.wav", f, "audio/wav")},
        timeout=120,
    )
print(resp.json()["text"])
```

### 语音合成 TTS

```bash
curl -X POST https://www.wangyutang.cn/common/api/tts/speech \
  -H "Content-Type: application/json" \
  -d '{"input":"你好，欢迎使用王语棠语音服务。","voice":"vivian","language":"chinese","response_format":"wav"}' \
  -o output.wav
```

---

## 图像理解（Vision）

两条公网接口，格式完全一致，可直接切换：

| 接口 | 路径前缀 | 模型 | 速度 |
|---|---|---|---|
| **lv**（推荐） | `/common/api/vision/lv/` | Qwen3.6-35B-A3B-UD-Q4_K_M.gguf + mmproj（lv RTX 4090） | 实测可用 |
| **spark** | `/common/api/vision/spark/` | qwen3.8-27b-fp8（spark vLLM） | 实测可用 |
| **dashscope** | `/common/api/vision/dashscope/` | Qwen-VL-Plus（阿里云） | ~2–5s |

### 接口地址

```text
# lv（推荐，速度最快）
GET  https://www.wangyutang.cn/common/api/vision/lv/health
GET  https://www.wangyutang.cn/common/api/vision/lv/models
POST https://www.wangyutang.cn/common/api/vision/lv/analyze-json   # base64 JSON
POST https://www.wangyutang.cn/common/api/vision/lv/analyze        # multipart 文件

# spark（回答最详细）
GET  https://www.wangyutang.cn/common/api/vision/spark/health
POST https://www.wangyutang.cn/common/api/vision/spark/analyze-json
POST https://www.wangyutang.cn/common/api/vision/spark/analyze

# dashscope（云端兜底）
GET  https://www.wangyutang.cn/common/api/vision/dashscope/health
POST https://www.wangyutang.cn/common/api/vision/dashscope/analyze-json
POST https://www.wangyutang.cn/common/api/vision/dashscope/analyze
```

### 请求格式（JSON）

```json
{
  "image_base64": "<图片 base64 字符串>",
  "question": "描述这张图片",
  "filename": "image.jpg"
}
```

```text
image_base64  必填，JPEG / PNG / WebP 的 base64（超过 10MB 拒绝，超过 1280px 长边自动缩放）
question      可选，默认"请描述这张图片，并提取其中的文字和关键信息。"
filename      可选，推断格式用，默认 image.jpg
```

### 返回格式

```json
{
  "text": "模型回答",
  "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
  "provider": "lv_qwen_vision",
  "image": {
    "original_width": 640, "original_height": 480,
    "width": 640, "height": 480, "bytes": 12345, "format": "jpeg"
  },
  "raw": { "choices": [...], "usage": {...} }
}
```

### curl 示例

```bash
IMAGE_B64=$(base64 -w 0 /path/to/image.jpg)

# lv（快）
curl -s -X POST https://www.wangyutang.cn/common/api/vision/lv/analyze-json \
  -H "Content-Type: application/json" \
  -d "{\"image_base64\": \"${IMAGE_B64}\", \"question\": \"这张图片里有什么？\"}"

# spark（详细）
curl -s -X POST https://www.wangyutang.cn/common/api/vision/spark/analyze-json \
  -H "Content-Type: application/json" \
  -d "{\"image_base64\": \"${IMAGE_B64}\", \"question\": \"这张图片里有什么？\"}"
```

### Python 示例

```python
import base64, requests

def analyze_image(image_path: str, question: str = "描述这张图片", provider: str = "lv") -> str:
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()
    url = f"https://www.wangyutang.cn/common/api/vision/{provider}/analyze-json"
    resp = requests.post(url, json={"image_base64": image_b64, "question": question}, timeout=60)
    resp.raise_for_status()
    return resp.json()["text"]

print(analyze_image("photo.jpg"))                              # lv（默认，快）
print(analyze_image("photo.jpg", provider="spark"))            # spark（详细）
print(analyze_image("photo.jpg", provider="dashscope"))        # dashscope（云端）
```

### multipart 文件上传

```bash
curl -s -X POST https://www.wangyutang.cn/common/api/vision/lv/analyze \
  -F "file=@/path/to/image.jpg" \
  -F "question=图中有什么内容？"
```

### 健康检查

```bash
curl https://www.wangyutang.cn/common/api/vision/lv/health
# → {"provider":"lv_qwen_vision","model":"Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",...}

curl https://www.wangyutang.cn/common/api/vision/spark/health
# → {"provider":"spark_qwen_vision","model":"qwen3.8-27b-fp8",...}
```

### 实测状态（2026-07-19 首次验证；Spark 两行 2026-08-18 切换 qwen3.8-27b-fp8 后复验，公网外部调用验证）

| 接口 | 模型 | 验证结果 |
|---|---|---|
| `chat/qwen3/completions` | Qwen3.6-35B-A3B-UD-Q4_K_M.gguf（lv RTX 4090） | 文本返回 `OK`，工具调用返回 `get_weather({"city":"北京"})` |
| `vision/lv/analyze-json` | Qwen3.6-35B-A3B-UD-Q4_K_M.gguf + mmproj（lv RTX 4090） | 红色测试图返回 `红色` |
| `llm/spark-qwen/chat/completions` | qwen3.8-27b-fp8（spark vLLM） | 文本对话、工具调用（`get_weather({"city":"北京"})`）均通过 |
| `vision/spark/analyze-json` | qwen3.8-27b-fp8（spark vLLM） | 纯色测试图正确识别颜色 |
| `vision/dashscope/analyze-json` | Qwen-VL-Plus（阿里云） | 云端兜底接口 |

历史性能基准（18 张图）见 `tests/vision_benchmark/`；Spark 切换记录见
[docs/spark-server-deployment.md](../docs/spark-server-deployment.md)。

---

## 部署方式

### CI/CD（推荐）

提交 `common_api_manager/**` 下的代码到 `feature/**` 或 `main` 分支，GitHub Actions 自动触发：

1. 语法检查（`python -m compileall`）
2. rsync 推送到腾讯云 `/root/control_platform/common_api/app_src/`
3. 重启 `common-api` systemd 服务
4. 运行 smoke tests（`tests/test_smoke.py`）
5. 测试失败 → 自动回滚到上一个备份

Workflow 文件：`.github/workflows/deploy-common-api.yml`

### 本地启动

在 `common_api_manager/` 下新建 `.env`：

```env
LV_ASR_BASE_URL=http://39.156.151.204:8000
LV_ASR_MODEL=qwen3-asr-1.7b
LV_TTS_BASE_URL=http://39.156.151.204:8001
LV_TTS_MODEL=qwen3-tts-12hz-1.7b-customvoice
LV_TTS_VOICE=vivian
LV_CHAT_BASE_URL=http://127.0.0.1:18002
LV_CHAT_MODEL=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
LV_VL_BASE_URL=http://127.0.0.1:18002
LV_VL_MODEL=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
DASHSCOPE_LLM_API_KEY=你的 DashScope API Key
DASHSCOPE_COMPATIBLE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
TEXT_MODEL=qwen3-32b
SPARK_QWEN_BASE_URL=http://127.0.0.1:18000
SPARK_QWEN_MODEL=qwen3.8-27b-fp8
MODEL_USAGE_COLLECTOR_URL=http://127.0.0.1:18080/usage
```

```bash
cd common_api_manager
uvicorn app:app --host 0.0.0.0 --port 8101 --reload
```

### Smoke Test

```bash
pip install requests pytest Pillow
API_BASE=https://www.wangyutang.cn/common pytest tests/test_smoke.py -v
```

---

## 服务器链路

```text
公网调用方
→ https://www.wangyutang.cn/common/api/*
→ Nginx（腾讯云）
→ common-api.service（port 8101, /root/control_platform/common_api/app_src/）
→ 按能力分发：
    ASR/TTS  → lv_server 39.156.151.204:8000/8001
    LLM      → tunnel :18002 → lv_server:8013（proxy → Qwen3.6 llama-server:8012）
    Vision   → tunnel :18002 → lv_server:8013（同一 Qwen3.6 + mmproj）
             → tunnel :18000 → spark-c9a7:8000 (35B vLLM)
             → DashScope API
```

SSH tunnel（systemd 管理，腾讯云上）：

```text
common-api-lv-qwen-chat-tunnel.service
  127.0.0.1:18002 → lv_server:8013  (LLM chat)
  127.0.0.1:18080 → lv_server:18080 (usage collector)

common-api-spark-c9a7-tunnel.service（或等效）
  127.0.0.1:18000 → spark-c9a7:8000 (vision/LLM)
```

---

## 目录结构

```text
common_api_manager/
  app.py                      FastAPI 入口，注册所有路由
  common/
    settings.py               环境变量与配置
  modules/
    health_check/             GET /api/health
    asr_transcribe/           POST /api/asr/transcribe
    lv_qwen_asr/              POST /api/asr/qwen3/transcribe
    lv_qwen_tts/              POST /api/tts/speech
    lv_qwen_chat/             POST /api/llm/chat, /api/chat/qwen3/completions
    lv_qwen_vision/           POST /api/vision/lv/analyze-json
    dashscope_qwen_chat/      POST /api/llm/qwen3-32b/chat
    dashscope_qwen_vision/    POST /api/vision/dashscope/analyze-json
    spark_qwen_chat/          POST /api/llm/qwen3.6-35b/chat
    spark_qwen_vision/        POST /api/vision/spark/analyze-json
    model_studio_catalog/     GET /api/model-studio/catalog
  tests/
    test_smoke.py             部署后 smoke tests（15 个用例）
    vision_benchmark/         18 张图评测集，results.json 基准数据
  systemd/
    common-api.env.example    环境变量模板
```

---

## 模型工作台（Model Studio）

`https://www.wangyutang.cn/common/model-studio`

一页式 UI，可直接在浏览器体验所有接口：

| 面板 | 说明 |
|---|---|
| **01 ASR** | 上传音频 → Qwen3-ASR-1.7B 识别 |
| **02 图像理解 VL** | 上传图片 + 选择接口（LV / Spark / DashScope）→ 视觉问答 |
| **03 LLM** | 选择接口（DashScope / Spark / LV）+ 输入 Prompt → 聊天 |
| **04 Spark 视觉** | Spark Qwen3.6-35B 专用视觉面板 |
| **★ 模型总览** | 所有模型健康状态 + 一键真实推理校验 |

验证码提示：`12`（入口轻量保护，防爬虫）。

---

## 注意事项

- 公网接口暂不启用 API Key，统一通过 `/common/api/*` 收口。
- LLM chat 默认使用 lv_server 上的 Qwen3.6-35B-A3B-UD-Q4_K_M.gguf（llama-server 思考模式默认关闭）。
- 图像超过 10MB 拒绝，超过 1280px 长边自动缩放至 JPEG 85%。
- lv vision `/api/vision/lv/` 后端是 llama-server，返回标准 OpenAI chat.completions 格式。
- Spark chat/vision 经腾讯云 SSH tunnel 路由，模型为 `qwen3.8-27b-fp8`（文本、工具调用、图像理解均已验证；
  2026-08-18 前为 `qwen3.6-35b-a3b`，切换记录见 [docs/spark-server-deployment.md](../docs/spark-server-deployment.md)）。
