# common_api_manager modules

目录规则：

```text
一个 API 能力对应一个子 module 文件夹。
```

当前模块：

```text
health_check       健康检查
asr_transcribe     公共 ASR 入口，默认转发到 LV Qwen3 ASR
lv_qwen_asr        LV 服务器 qwen3-asr-1.7b 专用 ASR 适配
lv_qwen_tts        LV 服务器 qwen3-tts-12hz-1.7B-CustomVoice 专用 TTS 适配
```

当前公共入口：

```text
POST /api/asr/transcribe
POST /api/asr/qwen3/transcribe

POST /api/tts/speech
POST /api/tts/synthesize
POST /api/tts/qwen3/speech
```

后续新增接口时，按同样结构增加：

```text
vision_analyze
llm_chat
...
```

Current LV chat adapter:

```text
lv_qwen_chat
GET  /api/llm/models
POST /api/llm/chat
GET  /api/chat/qwen3/models
POST /api/chat/qwen3/completions
```
