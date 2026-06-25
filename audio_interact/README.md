# audio_interact

Pi 端语音交互服务，负责采集麦克风音频、VAD 检测、上传 ASR、再将识别结果路由到 audio_recognition。

## 组件

| 文件 | 运行位置 | 职责 |
|---|---|---|
| `server.py` | 腾讯云容器 (:8095 或内部端口) | FastAPI + WebSocket 服务端，接收音频流、调用 ASR、把结果转发到 audio_recognition 路由 |
| `edge_streamer.py` | Pi 宿主机 | PyAudio 48kHz 采集 → 能量 VAD → 降采样 16kHz → WAV → WebSocket 上传 |

## 数据流

```
Pi 麦克风 (USB PnP, 48kHz)
  → edge_streamer.py VAD 分段
  → WebSocket /ws/audio
  → server.py (cloud)
  → POST COMMON_ASR_URL /api/asr/transcribe
  → POST AUDIO_RECOGNITION_URL 路由
  → 返回技能指令
```

## 配置

`config.example.json` 中的关键字段：

| 字段 | 说明 |
|---|---|
| `ws_url` | cloud server WebSocket 地址 |
| `vad_energy_threshold` | 能量 VAD 阈值（默认 500） |
| `vad_silence_frames` | 静音帧数触发切割 |
| `sample_rate` | 采集采样率，必须与设备匹配（48000） |

## 启动

```bash
# Pi 端（采集上传）
python3 edge_streamer.py --config config.json --loop

# cloud 端（Docker）
docker compose up audio-interact
```
