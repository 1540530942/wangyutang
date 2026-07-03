from __future__ import annotations

from fastapi import APIRouter

from common.settings import settings


router = APIRouter(tags=["health"])


def health_payload() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "common_api_manager",
        "default_provider": {
            "asr": "lv_qwen_asr",
            "tts": "lv_qwen_tts",
            "llm": "lv_qwen_chat",
            "llm_tools": "dashscope_qwen_chat",
            "vision": "dashscope_qwen_vision",
            "spark_llm": "spark_qwen_chat",
        },
        "models": {
            "asr": settings.lv_asr_model,
            "tts": settings.lv_tts_model,
            "llm": settings.lv_chat_model,
            "llm_tools": settings.dashscope_text_model,
            "vision": settings.dashscope_vision_model,
            "lv_vl": settings.lv_vl_model,
            "spark_llm": settings.spark_qwen_model,
        },
        "upstreams": {
            "asr": settings.lv_asr_base_url,
            "tts": settings.lv_tts_base_url,
            "llm": settings.lv_chat_base_url,
            "llm_tools": settings.dashscope_compatible_base_url,
            "vision": settings.dashscope_base_http_api_url,
            "lv_vl": settings.lv_vl_base_url,
            "spark_llm": settings.spark_qwen_base_url,
        },
        "routes": {
            "asr": "/api/asr/transcribe",
            "tts": ["/api/tts/speech", "/api/tts/synthesize"],
            "llm": ["/api/llm/chat", "/v1/chat/completions"],
            "llm_tools": [
                "/api/llm/qwen3-32b/chat",
                "/api/llm/qwen3-32b/chat/completions",
            ],
            "vision": [
                "/api/vision/qwen/analyze",
                "/api/vision/qwen/analyze-json",
            ],
            "spark_llm": [
                "/api/llm/spark-qwen/chat",
                "/api/llm/spark-qwen/chat/completions",
            ],
        },
        "max_audio_bytes": settings.max_audio_bytes,
        "usage_collector_configured": bool(settings.model_usage_collector_url),
    }


@router.get("/health")
def health() -> dict[str, object]:
    return health_payload()


@router.get("/api/health")
def api_health() -> dict[str, object]:
    return health_payload()
