# spark-c9a7 模型部署手册

> 用途：记录 spark-c9a7 上所有 AI 模型的部署方式、配置和调用方法。
>
> 最后更新：2026-08-18 — 主 LLM/Vision 服务切换为 `qwen3.8-27b-fp8`（原生 FP8）。生成速度明显
> 慢于旧的 `qwen3.6-35b-a3b`（MoE，约 1/9），用户已知晓并明确选择接受这个代价，等后续有更优化
> 的模型再替换。详见下方"模型选型记录"。

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
| `vllm-qwen38-27b-fp8` | `8000` | `qwen3.8-27b-fp8`（原生 FP8） | `nvcr.io/nvidia/vllm:26.05.post1-py3` |
| `vllm-qwen3-asr` | `8001` | `qwen3-asr-1.7b` | 同上 |
| `qwen3-tts-server` | `8002` | Qwen3-TTS（custom server.py） | 同上 |

所有容器使用 `--restart unless-stopped`，随 Docker daemon 自动启动。

`vllm-qwen36-nvfp4`（`qwen3.6-35b-a3b`，NVFP4/MoE）已停用，权重和启动脚本保留在
`~/models/NVIDIA/Qwen3.6-35B-A3B-NVFP4/` 和 `~/models/Scripts/start-qwen36-nvfp4.sh`，未删除，
可随时回滚——回滚时**必须显式传 `SERVED_MODEL_NAME=qwen3.6-35b-a3b`**，脚本自身默认值带
`-fp8` 后缀是错的。

---

## 各服务详情

### 1. LLM + Vision（vllm-qwen38-27b-fp8，port 8000）

**模型**：`~/models/Qwen/Qwen3.8-27B-FP8`
**Served name**：`qwen3.8-27b-fp8`
**架构**：稠密 27B，Gated DeltaNet + Gated Attention 混合注意力（每4层3层线性1层全注意力），
自带 VL 视觉塔，原生上下文 262144（当前部署按 `max-model-len=131072` 启动）。

**特点**：
- 原生 FP8 量化（`quant_method=fp8, fmt=e4m3, activation_scheme=dynamic`），vLLM 0.21.0 原生支持
  该架构类 `Qwen3_5ForConditionalGeneration`（Qwen3.8 复用了 Qwen3.5 的建模代码）
- 支持 `chat_template_kwargs: {"enable_thinking": False}` 关闭 thinking，用法与旧模型一致

**已知限制：生成速度明显慢于旧模型（已知晓，接受这个代价）**
- 稠密架构，每 token 全部 27B 参数参与计算；旧模型 `qwen3.6-35b-a3b` 是 MoE，名字里的
  **A3B = Active 3B**，每 token 仅激活约 3B 参数。单序列（batch=1）解码在这台机器上是显存带宽
  瓶颈场景，吞吐大致与激活参数量成反比：实测 `qwen3.8-27b-fp8` 只有 **~7 tok/s**，是旧模型
  （65-75 tok/s）的约 1/9（现场测得 GPU 利用率 96% 但功耗仅 33W，确认是带宽瓶颈不是算力瓶颈，
  不是卡死）。
- **多轮工具调用场景会被放大**：短问答在任何速度下都是秒级完成，感觉不出来；但 hermes-gateway
  这类会连续多轮调用工具（session_search / skill_view / terminal 等）的场景，每一轮都要重新生成
  一段文本，速度劣势线性叠加——2026-08-18 实测一次触发 9 轮工具调用的对话，单条微信消息卡了
  12 分钟以上才有回复。
- 2026-08-18 用户已知晓此限制并明确选择继续使用 `qwen3.8-27b-fp8`，等后续有速度更好的模型再替换。
  完整过程记录：[docs/logs/2026-08-18-qwen38-fp8-cutover.md](logs/2026-08-18-qwen38-fp8-cutover.md)、
  [docs/logs/2026-08-18-spark-hermes-dependency-discovery.md](logs/2026-08-18-spark-hermes-dependency-discovery.md)。

**调用示例（文本，关 thinking）**：

```python
requests.post("http://100.97.66.46:8000/v1/chat/completions", json={
    "model": "qwen3.8-27b-fp8",
    "messages": [{"role": "user", "content": "用一句话介绍北京。"}],
    "max_tokens": 200,
    "chat_template_kwargs": {"enable_thinking": False},
})
```

**调用示例（图像理解，关 thinking）**：

```python
requests.post("http://100.97.66.46:8000/v1/chat/completions", json={
    "model": "qwen3.8-27b-fp8",
    "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
        {"type": "text", "text": "这张图是什么颜色？"}
    ]}],
    "max_tokens": 300,
    "chat_template_kwargs": {"enable_thinking": False},
})
```

**注意**：不传 `chat_template_kwargs` 时模型会进入 thinking 模式，所有 token 用于推理，`content` 为空。务必传 `enable_thinking: False` 或确保 `max_tokens` 足够大（≥2000）。

**健康检查**：
```bash
curl http://100.97.66.46:8000/v1/models
```

**启动脚本**：`~/models/Scripts/start-qwen38-27b-fp8.sh`（`GPU_MEMORY_UTILIZATION=0.70`，经验值，
0.85 会因为没算上 TTS/ASR/hermes/系统开销而 OOM）。冷启动（权重加载 + torch.compile）约 6-7 分钟。
**切换编排脚本**：`~/models/Scripts/cutover_qwen38.sh`（停旧起新+就绪轮询+失败自动回滚+冒烟测试，
下游配置同步是手动的，见脚本输出提示）。

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
SPARK_QWEN_MODEL=qwen3.8-27b-fp8
```

调用路径：
- `POST /common/api/llm/spark-qwen/chat/completions`
- `POST /common/api/vision/spark/analyze-json`
