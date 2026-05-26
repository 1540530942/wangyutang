from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from common.settings import settings
from .service import transcribe_with_lv_qwen


router = APIRouter(tags=["lv_qwen_asr"])


@router.get("/api/asr/qwen3/health")
def qwen3_asr_health() -> dict[str, object]:
    return transcribe_with_lv_qwen.health()


@router.get("/api/asr/qwen3/models")
def qwen3_asr_models() -> dict[str, object]:
    return transcribe_with_lv_qwen.models()


@router.post("/api/asr/qwen3/transcribe")
async def qwen3_asr_transcribe(
    file: UploadFile = File(...),
    model: str = Form(settings.lv_asr_model),
    language: str = Form(settings.default_language),
) -> dict[str, object]:
    content = await file.read()
    return transcribe_with_lv_qwen(content, filename=file.filename or "audio.wav", model=model, language=language)

