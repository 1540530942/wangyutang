# lv_server 模型部署手册

> 用途：记录 lv_server 上所有 AI 模型的部署方式、配置和排障方法，便于复现和问题定位。
>
> 最后更新：2026-07-16

---

## 基本信息

| 项目 | 值 |
|------|----|
| 公网 IP | `39.156.151.204` |
| SSH 端口 | `58889`（非标准） |
| SSH 用户 | `centos` |
| SSH 别名 | `lv_server`（本地 ~/.ssh/config） |
| 操作系统 | CentOS（中国移动北京机房） |
| GPU | 2× **NVIDIA GeForce RTX 4090**（各 24GB GDDR6X，共 48GB） |

**注意**：lv_server 不在 Tailscale 网络，22 端口不通，必须走腾讯云跳板或本地 SSH 别名。Tencent（tang）无法直连 `39.156.151.204:8012`，必须走 SSH 隧道。

---

## SSH 访问方式

```bash
# 本地直连（通过 ~/.ssh/config lv_server 别名）
ssh lv_server

# 从腾讯云（ssh tang）作为跳板执行单条命令
ssh tang "ssh -i /etc/common_api/lv_server_tunnel_key -p 58889 centos@39.156.151.204 'your-command'"
```

隧道密钥位于腾讯云 `/etc/common_api/lv_server_tunnel_key`，由 systemd 服务 `common-api-lv-qwen-chat-tunnel.service` 使用（端口转发：`tang:18002 → lv_server:8013`）。

---

## GPU 资源分配（当前状态，2026-07-16）

```
GPU 0（24GB）  ← ~9.7GB（~40%）
  ├─ qwen3_tts_api  ：Qwen3-TTS-12Hz-1.7B-CustomVoice（Docker）
  └─ qwen3_asr_api  ：Qwen3-ASR-1.7B（Docker）

GPU 1（24GB）  ← ~23GB（~94%）
  └─ qwen36-mtp     ：Qwen3.6-35B-A3B-UD-Q4_K_M.gguf + mmproj-F16（Docker）
                       同时支持文本/工具调用 和 图像理解
```

---

## 服务总览

| 容器/服务 | 端口（宿主） | 模型 | 运行方式 | 状态 |
|-----------|------------|------|---------|------|
| `qwen36-mtp` | `8012` | Qwen3.6-35B-A3B-UD-Q4_K_M.gguf + mmproj-F16 | Docker，GPU 1 | 运行中 |
| `qwen3_tts_api` | `8001`→容器`8000` | Qwen3-TTS-12Hz-1.7B-CustomVoice | Docker，GPU 0 | 运行中 |
| `qwen3_asr_api` | `8000` | Qwen3-ASR-1.7B | Docker，GPU 0 | 运行中 |
| `qwen35-proxy`（systemd） | `8013` | 统一代理（文本和图像均路由到 8012） | uvicorn user service | 运行中 |

---

## 各服务详情

### 1. Qwen3.6-35B 统一模型（qwen36-mtp，port 8012）

**能力**：文本对话、工具调用（Function Calling）、图像理解（via mmproj）——单一端点全覆盖。

**部署命令**：

```bash
docker run -d --name qwen36-mtp --gpus "device=1" \
  --entrypoint bash \
  -v /data/llama_server_new:/llama \
  -v /data/models:/models:ro \
  -p 8012:8012 --restart unless-stopped \
  qwen35-vllm:latest \
  -c "export LD_LIBRARY_PATH=/llama; ldconfig /llama; \
      exec /llama/llama-server \
      -m /models/Qwen3.6-35B-A3B-MTP-GGUF/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
      --mmproj /models/Qwen3.6-35B-A3B-MTP-GGUF/mmproj-F16.gguf \
      --jinja -ngl 99 --host 0.0.0.0 --port 8012 -c 4096 -np 2"
```

**关键点**：
- 镜像：`qwen35-vllm:latest`（含 CUDA 12.x libs，与编译环境匹配）
- 二进制：`/data/llama_server_new/llama-server`（llama.cpp master，支持 Qwen3.6 的 SSM/Mamba 架构）
- 模型：`/data/models/Qwen3.6-35B-A3B-MTP-GGUF/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`（22.66GB）
- mmproj：`/data/models/Qwen3.6-35B-A3B-MTP-GGUF/mmproj-F16.gguf`（858MB）
- `--jinja`：开启 Jinja 模板，工具调用必需
- `--entrypoint bash`：覆盖 vllm 默认入口

**为什么用 Docker 而非裸 systemd**：新二进制在 `/data/llama_server_new/` 用 Docker 编译（CUDA libs 匹配），直接在 conda 环境运行会 coredump。

**调用 API（no-think 模式，最快）**：

```python
# 文本/工具调用（~0.3s，~170 tok/s）
requests.post("http://39.156.151.204:8012/v1/chat/completions", json={
    "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
    "messages": [...],
    "chat_template_kwargs": {"enable_thinking": False},  # 关闭 thinking，速度提升 10x
    "max_tokens": 512,
})

# 图像理解（~0.8s）
requests.post("http://39.156.151.204:8012/v1/chat/completions", json={
    "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
    "messages": [{"role":"user","content":[
        {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}},
        {"type":"text","text":"描述这张图片"}
    ]}],
    "chat_template_kwargs": {"enable_thinking": False},
    "max_tokens": 512,
})
```

**thinking on/off 性能对比**：

| 模式 | 文本耗时 | 文本tokens | 图像耗时 | 图像tokens |
|------|---------|-----------|---------|-----------|
| thinking ON | ~6s | ~1000 | ~1.3s | ~190 |
| thinking OFF（推荐） | **~0.3s** | **~20** | **~0.8s** | **~105** |

健康检查：`curl http://39.156.151.204:8012/health`

---

### 2. 统一代理（qwen35-proxy，port 8013）

文本请求和图像请求均转发到 8012（同一 Qwen3.6 端点）。

**文件位置**：
- 代码：`/data/qwen3_5_35b_deploy/qwen35_proxy_server.py`
- 环境变量：`/data/qwen3_5_35b_deploy/proxy.env`
- Systemd：`~/.config/systemd/user/qwen35-proxy.service`

**proxy.env（当前）**：
```ini
LLAMA_35B_BASE=http://127.0.0.1:8012
VL_MODEL_BASE=http://127.0.0.1:8012
```

**Tencent → lv_server 路由**：
```
common_api (tang) → 127.0.0.1:18002 → [SSH tunnel] → lv_server:8013 (proxy) → lv_server:8012 (qwen36-mtp)
```

管理：
```bash
systemctl --user restart qwen35-proxy
journalctl --user -u qwen35-proxy -f
```

---

### 3. TTS（qwen3_tts_api，port 8001）

- 镜像：`qwen3_tts:latest`（PyTorch 2.5.1，CUDA 12.4）
- 模型：`/data/models/Qwen3-TTS-12Hz-1.7B-CustomVoice`，GPU 0
- 端口映射：`8001(宿主) → 8000(容器)`

```bash
curl -X POST http://39.156.151.204:8001/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-tts-12hz-1.7b-customvoice","input":"你好","voice":"vivian","language":"chinese"}' \
  --output out.wav

curl http://39.156.151.204:8001/healthz
```

支持声音（全小写）：`vivian`、`serena`、`uncle_fu`、`dylan`、`eric`、`ryan`、`aiden`、`ono_anna`、`sohee`

---

### 4. ASR（qwen3_asr_api，port 8000）

- 镜像：`qwen3_asr:latest`（PyTorch 2.5.1，CUDA 12.4）
- 模型：`/data/models/Qwen3-ASR-1.7B`，GPU 0
- API：`POST /v1/asr`（非 OpenAI 标准，见 common_api `lv_qwen_asr` 模块）

健康检查：`curl http://39.156.151.204:8000/`（返回 HTTP 200）

---

## common_api_manager 集成

Tencent 上的 `common_api_manager`（port 8101）通过 SSH 隧道（18002）调用 lv_server：

```
LV_CHAT_BASE_URL=http://127.0.0.1:18002   # → lv_server:8013 proxy → 8012
LV_CHAT_MODEL=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
LV_VL_BASE_URL=http://127.0.0.1:18002     # 同一端点
LV_VL_MODEL=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
```

两个模块均默认开启 `chat_template_kwargs: {"enable_thinking": False}`：
- `lv_qwen_chat/service.py`：line 85-86
- `lv_qwen_vision/service.py`：`analyze_image_bytes` payload 中

---

## 完整重启流程

`--restart unless-stopped` 会在宿主重启后自动拉起所有 Docker 容器。Proxy 由 systemd user service 管理，含 15s 延迟等待 llama-server 就绪。

手动重启顺序（不常用）：
```bash
# 1. Qwen3.6（加载约 2-3 分钟）
docker restart qwen36-mtp

# 2. TTS / ASR
docker restart qwen3_tts_api
docker restart qwen3_asr_api

# 3. Proxy（等 8012 健康后）
systemctl --user restart qwen35-proxy
```

---

## 常见故障排查

### Qwen3.6 无响应
```bash
docker logs qwen36-mtp --tail 30
curl http://39.156.151.204:8012/health
docker restart qwen36-mtp  # 重新加载约 2-3 分钟
```

### TTS/ASR CUDA 错误
```bash
docker restart qwen3_tts_api   # 或 qwen3_asr_api，等待 ~20s
curl http://39.156.151.204:8001/healthz
```

### Proxy 502（连不到后端）
```bash
curl http://39.156.151.204:8012/health
systemctl --user restart qwen35-proxy
```

### Tencent 无法连接 lv_server
不能直连 `39.156.151.204:8012`（无路由）。必须走隧道：
```bash
# 确认隧道服务在 Tencent 上运行
systemctl status common-api-lv-qwen-chat-tunnel
# 测试隧道
curl http://127.0.0.1:18002/health
```

---

## 模型文件路径

```
/data/models/
├── Qwen3.6-35B-A3B-MTP-GGUF/
│   ├── Qwen3.6-35B-A3B-UD-Q4_K_M.gguf    # 主模型（22.66GB）
│   └── mmproj-F16.gguf                    # 视觉投影（858MB）
├── Qwen3-TTS-12Hz-1.7B-CustomVoice/
└── Qwen3-ASR-1.7B/

/data/llama_server_new/                    # llama.cpp master 编译产物
├── llama-server                           # 主二进制（支持 SSM/Mamba 架构）
├── libggml-cuda.so(.0)                    # CUDA 后端（187MB）
└── lib*.so(.0)                            # 其他依赖库
```

---

## llama.cpp 二进制重新编译

Qwen3.6 使用 SSM/Mamba 混合架构，需要 llama.cpp master（非 b8994 等旧版）。

```bash
# 1. 本地克隆（lv_server 不能访问 GitHub）
git clone https://github.com/ggml-org/llama.cpp /tmp/llama_src

# 2. rsync 到 lv_server
rsync -av --exclude='.git' /tmp/llama_src/ lv_server:/data/llama_cpp_src/

# 3. 在 Docker 内编译（匹配运行时 CUDA 库）
ssh lv_server "docker run --rm --gpus all \
  -v /data/llama_cpp_src:/src:ro \
  -v /data/llama_server_new:/out \
  qwen35-vllm:latest bash -c '
    cmake -S /src -B /tmp/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89
    cmake --build /tmp/build --config Release -j$(nproc) -t llama-server
    cp /tmp/build/bin/llama-server /out/
    cp /tmp/build/src/lib*.so /out/
    cp /tmp/build/ggml/src/lib*.so /out/
    for f in /out/*.so; do ln -sf \$(basename \$f) \${f}.0; done
  '"
```

---

## 遗留服务（已停用）

| 服务 | 说明 |
|------|------|
| `llama35b-gpu` | 原 Qwen3.5-35B，已被 `qwen36-mtp` 替换，systemd unit 已 disable |
| `qwen25-vl` | 原 Qwen2.5-VL-7B（port 8015），已停止，功能并入 Qwen3.6 mmproj |
| `helios-stt-*` | 旧 FunaASR + TensorRT，监听 127.0.0.1:19000/19001，已不使用 |
| `qwen3_5_9b_tool_proxy` | 9B 工具代理（port 8003），后端已无模型 |
