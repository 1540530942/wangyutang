from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from common.settings import settings
from .service import synthesize_with_lv_qwen


router = APIRouter(tags=["lv_qwen_tts"])


class SpeechRequest(BaseModel):
    model: str = Field(default=settings.lv_tts_model)
    input: str = Field(..., min_length=1)
    voice: str = Field(default=settings.lv_tts_voice)
    language: str = Field(default=settings.lv_tts_language)
    instructions: str = Field(default=settings.lv_tts_instructions)
    response_format: str = Field(default="wav")


@router.get("/api/tts/qwen3/health")
def qwen3_tts_health() -> dict[str, object]:
    return synthesize_with_lv_qwen.health()


@router.get("/api/tts/qwen3/models")
def qwen3_tts_models() -> dict[str, object]:
    return synthesize_with_lv_qwen.models()


@router.post("/api/tts/speech")
@router.post("/api/tts/synthesize")
@router.post("/api/tts/qwen3/speech")
def qwen3_tts_speech(payload: SpeechRequest) -> Response:
    audio, content_type = synthesize_with_lv_qwen(payload.model_dump())
    suffix = payload.response_format.lower() or "wav"
    return Response(
        content=audio,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="speech.{suffix}"'},
    )


@router.post("/api/tts/speech/stream")
@router.post("/api/tts/qwen3/speech/stream")
def qwen3_tts_speech_stream(payload: SpeechRequest) -> StreamingResponse:
    """分句流式 TTS：每句生成完立即以 [4B 长度][WAV] 格式推送给调用方。"""
    return StreamingResponse(
        synthesize_with_lv_qwen.stream(payload.model_dump()),
        media_type="application/octet-stream",
    )

