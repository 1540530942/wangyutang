from __future__ import annotations

from typing import Any

import requests
from fastapi import HTTPException

from common.settings import settings
from common.usage import report_model_usage


class LvQwenAsrClient:
    def _url(self, path: str) -> str:
        return f"{settings.lv_asr_base_url}{path}"

    def health(self) -> dict[str, object]:
        try:
            response = requests.get(self._url("/healthz"), timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"LV Qwen ASR health check failed: {exc}") from exc
        return {"provider": "lv_qwen_asr", "base_url": settings.lv_asr_base_url, **data}

    def models(self) -> dict[str, object]:
        try:
            response = requests.get(self._url("/v1/models"), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"LV Qwen ASR models request failed: {exc}") from exc

    def __call__(self, audio: bytes, filename: str, model: str, language: str = "") -> dict[str, Any]:
        if not audio:
            raise HTTPException(status_code=400, detail="empty audio file")
        selected_model = model or settings.lv_asr_model
        try:
            response = requests.post(
                self._url("/v1/audio/transcriptions"),
                data={"model": selected_model},
                files={"file": (filename or "audio.wav", audio, "audio/wav")},
                timeout=settings.lv_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"LV Qwen ASR request failed: {exc}") from exc
        text = str(payload.get("text") or "").strip()
        report_model_usage(
            model=selected_model,
            capability="asr",
            output_text=text,
            raw_usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
        )
        return {
            "text": text,
            "model": selected_model,
            "provider": "lv_qwen_asr",
            "raw": payload,
        }


transcribe_with_lv_qwen = LvQwenAsrClient()
