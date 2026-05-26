# health_check

对应接口：

```text
GET /health
GET /api/health
```

用途：

```text
检查 common_api_manager 是否在线，并返回当前统一接口默认使用的 ASR、TTS、LLM provider、模型、上游地址和路由。
```

当前默认能力：

```text
ASR: lv_qwen_asr  -> qwen3-asr-1.7b
TTS: lv_qwen_tts  -> qwen3-tts-12hz-1.7b-customvoice
LLM: lv_qwen_chat -> qwen3.5-9b
LLM Tools: dashscope_qwen_chat -> qwen3-32b
```
