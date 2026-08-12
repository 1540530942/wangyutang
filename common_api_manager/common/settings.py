from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(BASE_DIR), ".env"))


@dataclass(frozen=True)
class Settings:
    base_dir: str = BASE_DIR
    static_dir: str = STATIC_DIR
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "").strip()
    asr_model: str = os.getenv("ASR_MODEL", "qwen3-asr-flash-realtime")
    realtime_url: str = os.getenv("DASHSCOPE_REALTIME_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
    max_audio_bytes: int = int(os.getenv("ASR_MAX_AUDIO_BYTES", "3000000"))
    default_language: str = os.getenv("ASR_LANGUAGE", "zh")
    target_sample_rate: int = int(os.getenv("ASR_SAMPLE_RATE", "16000"))
    lv_asr_base_url: str = os.getenv("LV_ASR_BASE_URL", "http://39.156.151.204:8000").rstrip("/")
    lv_asr_model: str = os.getenv("LV_ASR_MODEL", "qwen3-asr-1.7b")
    lv_tts_base_url: str = os.getenv("LV_TTS_BASE_URL", "http://39.156.151.204:8001").rstrip("/")
    lv_tts_model: str = os.getenv("LV_TTS_MODEL", "qwen3-tts-12hz-1.7b-customvoice")
    lv_tts_voice: str = os.getenv("LV_TTS_VOICE", "vivian")
    lv_tts_language: str = os.getenv("LV_TTS_LANGUAGE", "chinese")
    lv_tts_instructions: str = os.getenv("LV_TTS_INSTRUCTIONS", "用清新自然、甜美温柔的语气说，声音明亮亲切，语调轻快柔和")
    lv_chat_base_url: str = os.getenv("LV_CHAT_BASE_URL", "http://39.156.151.204:8012").rstrip("/")
    lv_chat_model: str = os.getenv("LV_CHAT_MODEL", "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
    lv_vl_base_url: str = os.getenv("LV_VL_BASE_URL", "http://39.156.151.204:8012").rstrip("/")
    lv_vl_model: str = os.getenv("LV_VL_MODEL", "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
    dashscope_llm_api_key: str = (
        os.getenv("DASHSCOPE_LLM_API_KEY", "").strip()
        or os.getenv("DASHSCOPE_API_KEY", "").strip()
    )
    dashscope_base_http_api_url: str = os.getenv(
        "DASHSCOPE_BASE_HTTP_API_URL",
        "https://dashscope.aliyuncs.com/api/v1",
    ).rstrip("/")
    dashscope_realtime_url: str = os.getenv(
        "DASHSCOPE_REALTIME_URL",
        "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    )
    dashscope_compatible_base_url: str = os.getenv(
        "DASHSCOPE_COMPATIBLE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).rstrip("/")
    dashscope_text_model: str = os.getenv("TEXT_MODEL", "qwen3-32b")
    dashscope_vision_model: str = os.getenv("VISION_MODEL", "qwen-vl-plus")
    vision_default_question: str = os.getenv("VISION_DEFAULT_QUESTION", "请描述这张图片，并提取其中的文字和关键信息。")
    vision_max_image_bytes: int = int(os.getenv("VISION_MAX_IMAGE_BYTES", "10000000"))
    vision_resize_max_side: int = int(os.getenv("VISION_RESIZE_MAX_SIDE", "1280"))
    vision_jpeg_quality: int = int(os.getenv("VISION_JPEG_QUALITY", "85"))
    dashscope_timeout_seconds: float = float(os.getenv("DASHSCOPE_TIMEOUT_SECONDS", "180"))
    lv_timeout_seconds: float = float(os.getenv("LV_QWEN_TIMEOUT_SECONDS", "180"))
    spark_qwen_base_url: str = os.getenv("SPARK_QWEN_BASE_URL", "http://127.0.0.1:18000").rstrip("/")
    spark_qwen_model: str = os.getenv("SPARK_QWEN_MODEL", "qwen3.6-35b-a3b")
    spark_qwen_timeout_seconds: float = float(os.getenv("SPARK_QWEN_TIMEOUT_SECONDS", "180"))
    model_usage_collector_url: str = os.getenv("MODEL_USAGE_COLLECTOR_URL", "").strip()
    audio_convert_dir: str = os.getenv("AUDIO_CONVERT_DIR", os.path.join(BASE_DIR, "data", "audio_convert"))
    audio_convert_max_upload_bytes: int = int(os.getenv("AUDIO_CONVERT_MAX_UPLOAD_BYTES", "209715200"))
    audio_convert_max_jobs: int = int(os.getenv("AUDIO_CONVERT_MAX_JOBS", "40"))
    audio_convert_max_store_bytes: int = int(os.getenv("AUDIO_CONVERT_MAX_STORE_BYTES", "2147483648"))
    audio_convert_max_age_hours: float = float(os.getenv("AUDIO_CONVERT_MAX_AGE_HOURS", "168"))
    audio_convert_concurrency: int = int(os.getenv("AUDIO_CONVERT_CONCURRENCY", "2"))
    audio_convert_timeout_seconds: float = float(os.getenv("AUDIO_CONVERT_TIMEOUT_SECONDS", "600"))


settings = Settings()
