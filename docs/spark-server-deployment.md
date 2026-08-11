# spark-c9a7 模型部署手册

> 用途：记录 spark-c9a7 上所有 AI 模型的部署方式、配置和调用方法。
>
> 最后更新：2026-07-19

---

## 基本信息

| 项目 | 值 |
|------|----|
| Tailscale IP | `100.97.66.46` |
| SSH 主机名 | `spark-c9a7`（Tailscale） |
| SSH 用户 | `archer` |
| SSH 别名 | `ssh archer@spark-c9a7` |
| GPU | NVIDIA GPU（vLLM NVFP4 量化） |

**注意**：spark-c9a7 通过 Tailscale 访问，SSH 登录需要 Tailscale 认证（`https://login.tailscale.com`）。从腾讯云可直接访问 HTTP 端口（`100.97.66.46:8000/8001/8002`）但不能 SSH。

---

## 服务总览

| 容器 | 端口 | 模型 | 镜像 |
|------|------|------|------|
| `vllm-qwen36-nvfp4` | `8000` | `qwen3.6-35b-a3b`（NVFP4） | `nvcr.io/nvidia/vllm:26.05.post1-py3` |
| `vllm-qwen3-asr` | `8001` | `qwen3-asr-1.7b` | 同上 |
| `qwen3-tts-server` | `8002` | Qwen3-TTS（custom server.py） | 同上 |

所有容器使用 `--restart unless-stopped`，随 Docker daemon 自动启动。

---

## 各服务详情

### 1. LLM + Vision（vllm-qwen36-nvfp4，port 8000）

**模型**：`~/models/NVIDIA/Qwen3.6-35B-A3B-NVFP4`
**Served name**：`qwen3.6-35b-a3b`（**注意**：不带 `-fp8` 后缀）

**特点**：
- NVFP4 量化（NVIDIA 混合精度）格式，原生支持图像理解（不需要单独 mmproj）
- vLLM 后端，支持 `chat_template_kwargs: {"enable_thinking": False}` 关闭 thinking

**调用示例（文本，关 thinking）**：

```python
requests.post("http://100.97.66.46:8000/v1/chat/completions", json={
    "model": "qwen3.6-35b-a3b",
    "messages": [{"role": "user", "content": "用一句话介绍北京。"}],
    "max_tokens": 200,
    "chat_template_kwargs": {"enable_thinking": False},
})
# 耗时约 0.4-1s，速度约 65-75 tok/s
```

**调用示例（图像理解，关 thinking）**：

```python
requests.post("http://100.97.66.46:8000/v1/chat/completions", json={
    "model": "qwen3.6-35b-a3b",
    "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
        {"type": "text", "text": "这张图是什么颜色？"}
    ]}],
    "max_tokens": 300,
    "chat_template_kwargs": {"enable_thinking": False},
})
# 耗时约 3-4s，速度约 75 tok/s
```

**注意**：不传 `chat_template_kwargs` 时模型会进入 thinking 模式，所有 token 用于推理，`content` 为空。务必传 `enable_thinking: False` 或确保 `max_tokens` 足够大（≥2000）。

**容器内额外进程**：
- `python3 /server.py`：vision HTTP 辅助服务（随容器启动）

**健康检查**：
```bash
curl http://100.97.66.46:8000/v1/models
```

**启动脚本**：`~/models/Scripts/start-qwen36-nvfp4.sh`

---

### 2. ASR（vllm-qwen3-asr，port 8001）

- 模型：`~/models/Qwen/Qwen3-ASR-1.7B`
- API：`/v1/chat/completions`（标准 OpenAI 格式）

---

### 3. TTS（qwen3-tts-server，port 8002）

- 模型：`~/models/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`
- API：自定义 `server.py`，非标准格式

支持声音：`Vivian`、`Serena`、`Uncle_Fu`、`Dylan`、`Eric`、`Ryan`、`Aiden`、`Ono_Anna`、`Sohee`

---

## Spark vs lv_server 性能对比（2026-07-16）

| 指标 | lv_server Qwen3.6 | Spark Qwen3.6 |
|------|------------------|---------------|
| **格式** | GGUF Q4_K_M + llama.cpp | NVFP4 + vLLM |
| **GPU** | 2× RTX 4090（48GB 总） | 单 GPU |
| **文本耗时**（关 thinking） | ~0.3s | ~0.4-1s |
| **生成速率** | ~165 tok/s | ~65-75 tok/s |
| **图像耗时**（关 thinking） | ~0.8s | ~3-4s |
| **图像生成速率** | ~130 tok/s | ~75 tok/s |
| **工具调用** | ✅ 支持 | ✅ 支持 |
| **关 thinking 方式** | `chat_template_kwargs` | `chat_template_kwargs` |

lv_server 生成速率约为 Spark 的 **2-2.5x**，图像端到端也快约 4x。2026-07-19 线上验证中，Spark 文本返回 `OK`，工具调用返回 `get_weather({"city":"北京"})`，红色测试图返回 `红色`。

---

## Hermes Gateway

```bash
# Systemd service（自动启动）
hermes-gateway.service   # Restart=always, enabled, 用户 archer
python -m hermes_cli.main gateway run --replace
```

---

## common_api_manager 集成

Spark 端点在 `common_api_manager` 中以 `spark_qwen_chat` 和 `spark_qwen_vision` provider 接入：

```
SPARK_QWEN_BASE_URL=http://100.97.66.46:8000   # 或 ssh tunnel 127.0.0.1:18000
SPARK_QWEN_MODEL=qwen3.6-35b-a3b
```

调用路径：
- `POST /common/api/llm/spark-qwen/chat/completions`
- `POST /common/api/vision/spark/analyze-json`
