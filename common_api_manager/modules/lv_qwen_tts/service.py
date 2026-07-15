from __future__ import annotations

from collections.abc import Iterator

import requests
from fastapi import HTTPException

from common.settings import settings
from common.usage import report_model_usage


class LvQwenTtsClient:
    def _url(self, path: str) -> str:
        return f"{settings.lv_tts_base_url}{path}"

    def health(self) -> dict[str, object]:
        try:
            response = requests.get(self._url("/healthz"), timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"LV Qwen TTS health check failed: {exc}") from exc
        return {"provider": "lv_qwen_tts", "base_url": settings.lv_tts_base_url, **data}

    def models(self) -> dict[str, object]:
        try:
            response = requests.get(self._url("/v1/models"), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"LV Qwen TTS models request failed: {exc}") from exc

    def __call__(self, payload: dict[str, object]) -> tuple[bytes, str]:
        request_payload = {
            "model": payload.get("model") or settings.lv_tts_model,
            "input": payload.get("input") or "",
            "voice": payload.get("voice") or settings.lv_tts_voice,
            "language": payload.get("language") or settings.lv_tts_language,
            "instructions": payload.get("instructions") or settings.lv_tts_instructions,
            "response_format": payload.get("response_format") or "wav",
        }
        if not str(request_payload["input"]).strip():
            raise HTTPException(status_code=400, detail="input is required")
        try:
            response = requests.post(
                self._url("/v1/audio/speech"),
                json=request_payload,
                timeout=settings.lv_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"LV Qwen TTS request failed: {exc}") from exc
        content_type = response.headers.get("content-type") or "audio/wav"
        report_model_usage(
            model=str(request_payload["model"]),
            capability="tts",
            input_text=request_payload["input"],
        )
        return response.content, content_type

    def stream(self, payload: dict[str, object]) -> Iterator[bytes]:
        """分句流式：透传 lv_server /v1/audio/speech/stream 的 [4B 长度][WAV] 字节流。

        格式：每句 = [大端 4 字节长度][WAV 数据]，结束哨兵 = [4 字节零]。
        调用方按此协议解析，收到零长度包即结束。
        """
        request_payload = {
            "model": payload.get("model") or settings.lv_tts_model,
            "input": payload.get("input") or "",
            "voice": payload.get("voice") or settings.lv_tts_voice,
            "language": payload.get("language") or settings.lv_tts_language,
            "instructions": payload.get("instructions") or settings.lv_tts_instructions,
            "response_format": "wav",
        }
        if not str(request_payload["input"]).strip():
            raise HTTPException(status_code=400, detail="input is required")
        try:
            with requests.post(
                self._url("/v1/audio/speech/stream"),
                json=request_payload,
                timeout=settings.lv_timeout_seconds,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"LV Qwen TTS stream failed: {exc}") from exc


synthesize_with_lv_qwen = LvQwenTtsClient()
