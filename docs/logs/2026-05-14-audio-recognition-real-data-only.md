# Audio Recognition Real Data Only

## Change

The audio recognition chain now rejects synthetic ASR paths. Runtime support for fabricated transcripts, dry-run transcripts, and synthetic wakeup results was removed from `audio_recognition`.

## Current Rule

Every visible transcript or routed servo command must come from one of these real inputs:

- browser microphone audio submitted to `Project_ASR/online_api`;
- Raspberry Pi WAV recording captured from the WonderEchoPro audio device.

If recording, ASR, cloud upload, skill routing, or robot execution fails, the system must show the error instead of producing a successful placeholder result.

## Updated Files

```text
audio_recognition/server.py
audio_recognition/model_provider.py
audio_recognition/edge_audio_listener.py
audio_recognition/config.example.json
audio_recognition/README.md
audio_recognition/IMPLEMENTATION.md
audio_recognition/static/style.css
```

## Deployment Verification

Tencent Cloud was updated and `audio-recognition` was restarted. The online data stores were cleared so no historical synthetic records remain visible:

```text
GET https://www.wangyutang.cn/audio/api/results -> []
GET https://www.wangyutang.cn/audio/api/events  -> []
```

The local ASR API health check is real-backend healthy:

```text
http://127.0.0.1:8097/health -> backend_ok=true, model=qwen3-asr
```
