# spark-c9a7 模型部署手册

> 用途：记录 spark-c9a7 上所有 AI 模型的部署方式、配置和调用方法。
>
> 最后更新：2026-09-05 —— 核对时发现容器已在约两周前静默切换：`vllm-qwen38-27b-fp8`（纯 FP8）
> 已停止，现在实际跑的是 `vllm-qwen38-27b-nvfp4`，用的是**混合精度**（多数 MLP 层 NVFP4 4bit +
> attention/lm_head/末8层 MLP 用 FP8 8bit），但 `--served-model-name` 仍沿用旧的
> `qwen3.8-27b-fp8`，接口层面看不出来。实测生成速度 ~11 tok/s，比旧纯 FP8 版本的 ~7 tok/s 快，
> 但仍远慢于更早的 `qwen3.6-35b-a3b`（MoE, 65-75 tok/s）。本次更新只核对了本页内容，2026-08-18
> 的历史决策记录（[cutover 日志](logs/2026-08-18-qwen38-fp8-cutover.md)）未改，仅供追溯当时的决策过程。

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
| `vllm-qwen38-27b-nvfp4` | `8000` | served-name `qwen3.8-27b-fp8`（实为混合精度 FP8+NVFP4，见下） | `nvcr.io/nvidia/vllm:26.05.post1-py3` |
| `vllm-qwen3-asr` | `8001` | `qwen3-asr-1.7b` | 同上 |
| `qwen3-tts-server` | `8002` | Qwen3-TTS（custom server.py） | 同上 |

所有容器使用 `--restart unless-stopped`，随 Docker daemon 自动启动。

**已停用但保留、可回滚的旧容器/权重**（2026-09-05 `docker ps -a` 核实）：
- `vllm-qwen38-27b-fp8`（Exited，2 weeks ago）——纯 FP8 版本，被上面的 nvfp4 混合精度版本取代。
- `vllm-qwen36-nvfp4`（Exited，2 weeks ago）——`qwen3.6-35b-a3b`，NVFP4/MoE，65-75 tok/s，速度明显
  更快。权重和启动脚本保留在 `~/models/NVIDIA/Qwen3.6-35B-A3B-NVFP4/` 和
  `~/models/Scripts/start-qwen36-nvfp4.sh`，未删除，可随时回滚——回滚时**必须显式传
  `SERVED_MODEL_NAME=qwen3.6-35b-a3b`**，脚本自身默认值带 `-fp8` 后缀是错的。

---

## 各服务详情

### 1. LLM + Vision（vllm-qwen38-27b-nvfp4，port 8000，served-name `qwen3.8-27b-fp8`）

**模型**：`~/models/Qwen/Qwen3.8-27B-FP8` 目录下的权重，但实际量化格式已经不是纯 FP8（见下）。
**Served name**：`qwen3.8-27b-fp8`——沿用旧版本命名，`--served-model-name` 没有跟着这次切换改，
调用方看到的 `model` 字段跟实际量化方式对不上，容易误判，排障时以 `docker ps` 里的容器名
（`vllm-qwen38-27b-nvfp4`）为准。
**架构**：稠密 27B，Gated DeltaNet + Gated Attention 混合注意力（每4层3层线性1层全注意力），
自带 VL 视觉塔，原生上下文 262144（当前部署按 `max-model-len=131072` 启动）。

**量化方式（2026-09-05 从运行中容器的 `config.json` 核实，混合精度，非纯 FP8）**：
- `quant_method: compressed-tensors`，`format: mixed-precision`。
- 大部分层的 MLP（`gate/up/down_proj`）—— 一个 transformer 里参数量占比最大的部分 —— 用
  **NVFP4**（`nvfp4-pack-quantized`，4bit）。
- attention 投影层（`q/k/v/o_proj`）、`lm_head`、以及最后 8 层（56-63）的 MLP 用 **FP8**（`e4m3`，8bit）。
- 按参数量算，NVFP4 覆盖的部分比 FP8 部分更多，所以严格说这是"以 NVFP4 为主的混合精度"，
  不是"原生 FP8"。
- 启动命令关键参数：`--quantization compressed-tensors --kv-cache-dtype auto
  --attention-backend flashinfer --tensor-parallel-size 1 --enforce-eager
  --enable-auto-tool-choice --tool-call-parser qwen3_coder`（`--enforce-eager` 关闭了
  CUDA Graph，是已知的速度取舍点，还没试过关掉它能提速多少）。
- 支持 `chat_template_kwargs: {"enable_thinking": False}` 关闭 thinking，用法与旧模型一致。

**已知限制：生成速度仍明显慢于旧 MoE 模型（已知晓，接受这个代价）**
- 稠密架构，每 token 全部 27B 参数参与计算；旧模型 `qwen3.6-35b-a3b` 是 MoE，名字里的
  **A3B = Active 3B**，每 token 仅激活约 3B 参数，吞吐大致与激活参数量成反比。
- **2026-08-18 记录的 ~7 tok/s 是纯 FP8 版本（`vllm-qwen38-27b-fp8`）的数据，那个容器现在已经
  停止运行**（现场测得 GPU 利用率 96% 但功耗仅 33W，确认是带宽瓶颈不是算力瓶颈，不是卡死）。
- **2026-09-05 对现在实际在跑的混合精度版本重新实测**：500 completion tokens 耗时 45s，
  即 **~11 tok/s**——比旧纯 FP8 版本快约 1.5x（大部分 MLP 权重从 8bit 降到 4bit，带宽压力变小），
  但仍是旧 MoE 模型（65-75 tok/s）的约 1/6-1/7，量级上没有本质改善。
- **多轮工具调用场景会被放大**：短问答在任何速度下都是秒级完成，感觉不出来；但 hermes-gateway
  这类会连续多轮调用工具（session_search / skill_view / terminal 等）的场景，每一轮都要重新生成
  一段文本，速度劣势线性叠加——2026-08-18 实测一次触发 9 轮工具调用的对话，单条微信消息卡了
  12 分钟以上才有回复（当时测的是纯 FP8 版本；混合精度版本理论上会快一些，但没有专门重测这个
  多轮工具调用场景，不确定实际改善幅度）。
- 2026-08-18 用户已知晓此限制并明确选择继续使用（当时的）`qwen3.8-27b-fp8`，等后续有速度更好的
  模型再替换；两周前切到现在这个混合精度版本，是同一个决策方向下的延续（速度稍有改善但没有
  根本解决）。完整过程记录：
  [docs/logs/2026-08-18-qwen38-fp8-cutover.md](logs/2026-08-18-qwen38-fp8-cutover.md)、
  [docs/logs/2026-08-18-spark-hermes-dependency-discovery.md](logs/2026-08-18-spark-hermes-dependency-discovery.md)
  ——这两篇记录的是纯 FP8 版本切换时的过程，未覆盖两周前到混合精度版本的这次切换（没找到对应的
  决策记录）。

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

**启动脚本**：现在实际在跑的容器由 `~/models/Scripts/start-qwen38-nvfp4.sh` 启动（2026-09-05 从
`~/models/Scripts/` 目录核实存在）；旧的纯 FP8 版本对应 `~/models/Scripts/start-qwen38-27b-fp8.sh`
（`GPU_MEMORY_UTILIZATION=0.70`，经验值，0.85 会因为没算上 TTS/ASR/hermes/系统开销而 OOM），
两个脚本都还在，可以对照差异。冷启动（权重加载 + torch.compile）约 6-7 分钟。
**切换编排脚本**：`~/models/Scripts/cutover_qwen38.sh`（停旧起新+就绪轮询+失败自动回滚+冒烟测试，
下游配置同步是手动的，见脚本输出提示）——不确定这次切到 nvfp4 版本是否也经过这个脚本，未找到
对应的切换记录。

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

**注意**：这张表测的是 2026-07-16 时 Spark 上跑的 `qwen3.6-35b-a3b`（MoE，NVFP4），跟现在 Spark
上实际跑的 `qwen3.8-27b-fp8`（稠密，混合 FP8+NVFP4，~11 tok/s，见上文）不是同一个模型，别搞混——
现在 Spark 的实际速度比这张表里的数字差不少。

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
