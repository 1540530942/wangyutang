# 2026-05-12 Audio Recognition Mock Cloud

## Goal

Show a mocked multimodal ASR result on Tencent Cloud before the real model API is provided.

Public page:

```text
https://www.wangyutang.cn/audio/
```

## Source Changes

Added and wired the `audio_recognition` module:

```text
audio_recognition/server.py
audio_recognition/edge_audio_listener.py
audio_recognition/model_provider.py
audio_recognition/recorder.py
audio_recognition/skill_router.py
audio_recognition/static/
audio_recognition/Dockerfile
audio_recognition/config.example.json
docker-compose.yml
control_platform/infra/caddy/Caddyfile
control_platform/modules/registry.json
scripts/deploy_tencent.ps1
scripts/tencent_apply_release.sh
```

## Cloud Deployment

Uploaded module files to:

```text
/root/control_platform/audio_recognition
```

Uploaded gateway config:

```text
/root/control_platform/infra/caddy/Caddyfile
```

Uploaded module registry:

```text
/root/control_platform/registry.json
```

Started the temporary container:

```bash
docker run -d --no-healthcheck \
  --name audio-recognition \
  --network infra_default \
  --restart unless-stopped \
  -p 8095:8095 \
  -v /root/control_platform/audio_recognition:/app \
  -v /root/control_platform/audio_recognition_data:/app/data \
  camera-snapshot:local \
  uvicorn server:app --host 0.0.0.0 --port 8095
```

Reason for `--no-healthcheck`:

- The temporary runtime reuses `camera-snapshot:local`.
- That image inherits a healthcheck for port `8099`.
- `audio-recognition` listens on `8095`, so the inherited healthcheck would incorrectly mark the container unhealthy.

Reloaded Caddy:

```bash
docker exec control-platform-caddy caddy reload --config /etc/caddy/Caddyfile
```

## Initial Mock Result

Posted one mocked ASR result:

```json
{
  "device_id": "mock-multimodal-model",
  "text": "这是一个模拟的大模型语音识别结果：向左看，然后向前走一点。",
  "wav_path": "/tmp/audio_recognition/mock_command.wav",
  "skill_id": "look_left",
  "raw": {
    "mock": true,
    "model": "mock-multimodal-asr",
    "note": "用于验证腾讯云 /audio/ 文本呈现"
  }
}
```

An earlier Windows-console submission produced question marks due to local console encoding. That bad row was removed from:

```text
/root/control_platform/audio_recognition_data/results.json
```

## Hardware-Only Self-Developed Pipeline Mock

The user clarified that WonderEchoPro should be used only as hardware. The software stack should be self-developed.

Updated implementation:

```text
WonderEchoPro hardware frame
  -> self-developed serial reader
  -> self-developed arecord wrapper
  -> mocked audio conversion/model provider
  -> cloud text result
  -> online visualization
```

No Hiwonder `speech`, `awake.WonderEchoPro`, or `vocal_detect` runtime is required for the self-developed path.

Added cloud event visualization:

```text
/audio/api/events
```

The `/audio/` page now shows pipeline stages:

```text
hardware_wakeup
recording
audio_conversion
model_asr
text_display
```

Posted a complete hardware-only mock chain:

```json
{
  "device_id": "wonderecho-hardware-only-mock",
  "text": "硬件链路 mock 完成：我说话后，系统识别为“向左看，然后向前走一点”。",
  "skill_id": "look_left",
  "raw": {
    "mock": true,
    "hardware_only": true,
    "software_stack": "self-developed",
    "provider": "mock-multimodal-asr"
  }
}
```

Pipeline event examples:

```json
[
  {
    "stage": "hardware_wakeup",
    "status": "mock",
    "message": "WonderEchoPro 硬件唤醒帧已模拟收到"
  },
  {
    "stage": "recording",
    "status": "ok",
    "message": "树莓派本地录音阶段已模拟完成"
  },
  {
    "stage": "audio_conversion",
    "status": "ok",
    "message": "音频数据转换为模型输入 payload"
  },
  {
    "stage": "model_asr",
    "status": "mock",
    "message": "多模态大模型返回 mock ASR 文本"
  },
  {
    "stage": "text_display",
    "status": "ok",
    "message": "识别文本已上传并进入在线可视化页面"
  }
]
```

## Verification

Verified HTTP 200:

```text
https://www.wangyutang.cn/audio/
https://www.wangyutang.cn/audio/api/health
https://www.wangyutang.cn/audio/api/results
https://www.wangyutang.cn/audio/api/events
https://www.wangyutang.cn/audio/static/app.js
```

Verified latest result:

```text
硬件链路 mock 完成：我说话后，系统识别为“向左看，然后向前走一点”。
```

Verified latest event:

```text
text_display: 识别文本已上传并进入在线可视化页面
```

## Current Status

The `/audio/` page is live on Tencent Cloud and displays both:

- The latest mocked ASR text.
- The full mocked processing pipeline.

This is an emergency-style direct deployment. The source has been updated so the next standard Tencent release can build a proper `audio-recognition:local` image instead of reusing `camera-snapshot:local`.
