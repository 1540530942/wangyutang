# 2026-09-13 · TTS 合成延迟实测 + 两个真实生产bug修复

## 背景

用户问"说一句五秒的话要多久"，本意是了解 ESP32/树莓派播报链路的音频上云/下云速率。
排查过程中顺带发现并修复了两个跟原问题无关、但真实存在的生产 bug。

## 延迟实测结果

### 下云（TTS合成+下载）

| 测试路径 | 音频时长 | 总耗时 | 相对实时速度 |
|---|---|---|---|
| 走公网完整链路（`audio_interact/api/tts`，非流式） | 4.88s | 13.11s | ~0.4x |
| spark 本机直连（qwen3-tts, `localhost:8002`） | 4.32s | 5.51s | ~0.78x |
| lv_server 本机直连（qwen3-tts-12hz-1.7b, `localhost:8001`） | 4.24s | 6.66s | ~0.64x |

**结论**：公网完整链路的延迟里，相当一部分是网络/网关中转开销（本机直连比走公网快约2倍），
不全是模型合成本身慢。两台机器本机测都慢于实时（0.64x~0.78x），spark 略快。

**重要限定**：以上都是通过**非流式**（一次性生成整段音频再返回）接口测的。ESP32
`mic_asr_test` 用的正是这个非流式接口（`audio_interact/api/tts`），所以这组数字对它是
真实代表；但树莓派 WonderEcho Pro / ESP32 `play_audio` 走的是**流式** `_stream_tts`
（按句子分段合成、边合成边推送），首句延迟理论上应该明显更好，本次没有单独测流式路径。

### 上云（麦克风录音上传+ASR）——引用 2026-09-09 ESP32 实测

4秒录音（约192KB PCM）：录音结束到拿到ASR结果共 3.52s，其中约2.01s 是 ESP32 侧 TLS
握手（嵌入式设备加密计算明显慢于服务器），实际上传+ASR处理约1.5s。

## 修复1：spark 的 qwen3-tts-server CUDA 报错

**现象**：`curl localhost:8002/v1/audio/speech` 稳定返回 500，`docker logs` 显示
`torch.AcceleratorError: CUDA error: operation not permitted`，报错发生在
`transformers` 的 `isin_mps_friendly` 里的 `torch.isin()` 调用。GPU显存本身正常
（三个服务共占约102GB，剩19GB），不是OOM。

**根因推测**：该容器（`qwen3-tts-server`）已连续运行32小时，跨过了 2026-09-09/10
Nemotron 部署时那次内存耗尽事故（`docker stop`卡住+`--init`缺失导致僵尸进程，机器一度
121Gi/121Gi 打满近失去响应）。容器本身没崩，但大概率是那次极端内存压力把它的 CUDA
上下文搞坏了，此后一直在静默报500（直到这次才被发现）。

**修复**：`docker restart qwen3-tts-server`，重启后实测正常（4.32s音频/5.51s耗时）。

**遗留**：不确定这个500状态从什么时候开始的（2026-09-09/10事故后到现在这段时间内，所有
经过 spark TTS 的真实播报请求可能都在失败），没有查具体影响范围。

## 修复2：lv_server 的 qwen3-tts-12hz API 环境变量大小写错误

**现象**：`POST /v1/audio/speech` 不显式传 `voice`/`language` 参数（即按 OpenAPI
schema 文档给的默认值调用）必定返回 400：`Unsupported voice: Vivian` /
`Unsupported language: Chinese`。

**根因**：容器启动时设置的环境变量本身就是错的大小写——
`DEFAULT_LANGUAGE=Chinese`、`DEFAULT_SPEAKER=Vivian`，但 `/healthz` 暴露的
`supported_speakers`/`supported_languages` 实际列表全是小写（`vivian`/`chinese`
等）。应用代码里 `DEFAULT_SPEAKER = os.environ.get("DEFAULT_SPEAKER", "Vivian")`
的兜底默认值同样是错的大小写，但因为环境变量本身已经设置，兜底值从未生效——
**两层默认值（环境变量 + 代码兜底）恰好用的是同一套错误大小写，双重掩盖了这个bug，
只有显式传参调用才会绕开它，这也是为什么它能存活这么久没被发现**。

**排查方法**：容器没有挂载 `/app` 源码卷（只读挂载了模型权重），代码是打进镜像
`qwen3_tts:latest` 里的。宿主机上找不到对应的 `docker-compose.yml` 或启动脚本——
这个容器最初就是手动 `docker run` 起来的。

**修复过程**（有一次白费功夫，记录下来避免下次重复）：
1. 第一次尝试：`docker exec` 进容器直接 `sed` 改 `/app/app/main.py` 里的代码兜底默认值
   （`"Chinese"`→`"chinese"`，`"Vivian"`→`"vivian"`），`docker restart` 生效——**这次
   白费了**，因为环境变量本身设置了 `DEFAULT_LANGUAGE=Chinese`/`DEFAULT_SPEAKER=Vivian`，
   环境变量优先级高于代码里的 `os.environ.get(key, fallback)` 兜底值，容器重启后
   `docker restart`（非重建）保留了原环境变量，代码里的修改因此完全不起作用。
2. 真正的修复：`docker inspect` 拿到完整原配置（端口 `8001→8000`、
   `--gpus device=0`、只读挂载 `/data/models/Qwen3-TTS-12Hz-1.7B-CustomVoice`、
   `--restart unless-stopped`、`--ipc private`），`docker stop`+`docker rm` 旧容器，
   用同样配置 + 修正后的 `-e DEFAULT_LANGUAGE=chinese -e DEFAULT_SPEAKER=vivian`
   重新 `docker run` 创建。

**验证**：不传 voice/language 参数的请求现在返回 200，正常生成1.68s的合法WAV。

**教训**：容器环境变量的优先级高于应用代码里 `os.environ.get(key, default)` 的
fallback 值——诊断"为什么改了代码默认值不生效"时，第一件事应该是 `docker exec ... env`
确认有没有被环境变量覆盖，而不是先怀疑代码逻辑或以为改错了地方。

**遗留**：这个容器不是用 compose/脚本管理的，这次修复也只是手动 `docker run`，没有
留下可复用的启动脚本——如果以后有人不知情地用旧的（错误大小写）方式重建这个容器，
这个bug会原样复现。建议后续把这次用的 `docker run` 命令存成脚本。
