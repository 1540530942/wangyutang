from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from common.settings import settings
from modules.lv_qwen_asr.service import transcribe_with_lv_qwen
from .service import transcribe_audio_bytes


router = APIRouter(tags=["asr_transcribe"])


@router.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = Form(settings.default_language)) -> dict[str, object]:
    content = await file.read()
    return transcribe_with_lv_qwen(
        content,
        filename=file.filename or "audio.wav",
        model=settings.lv_asr_model,
        language=language,
    )


@router.post("/api/asr/transcribe")
async def common_asr_transcribe(
    file: UploadFile = File(...),
    language: str = Form(settings.default_language),
    model: str = Form(""),
) -> dict[str, object]:
    content = await file.read()
    selected_model = model or settings.lv_asr_model
    if selected_model == settings.asr_model:
        return transcribe_audio_bytes(content, language)
    return transcribe_with_lv_qwen(content, filename=file.filename or "audio.wav", model=selected_model, language=language)
