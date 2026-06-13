from __future__ import annotations

from typing import Any

import requests
from fastapi import HTTPException

from common.settings import settings
from common.usage import messages_text, report_model_usage


def _extract_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    text = str(content).strip()
    marker = "</think>"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text


class SparkQwenChatClient:
    def _url(self, path: str) -> str:
        return f"{settings.spark_qwen_base_url}{path}"

    def health(self) -> dict[str, object]:
        try:
            response = requests.get(self._url("/v1/models"), timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Spark Qwen health check failed: {exc}") from exc
        return {
            "provider": "spark_qwen_chat",
            "base_url": settings.spark_qwen_base_url,
            "model": settings.spark_qwen_model,
            "models": data,
        }

    def models(self) -> dict[str, object]:
        try:
            response = requests.get(self._url("/v1/models"), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Spark Qwen models request failed: {exc}") from exc

    def chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = dict(payload)
        request_payload["model"] = request_payload.get("model") or settings.spark_qwen_model
        request_payload.setdefault("stream", False)
        request_payload.setdefault("chat_template_kwargs", {"enable_thinking": False})
        if not request_payload.get("messages"):
            raise HTTPException(status_code=400, detail="messages is required")
        try:
            response = requests.post(
                self._url("/v1/chat/completions"),
                json=request_payload,
                timeout=settings.spark_qwen_timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            detail = str(exc)
            if exc.response is not None:
                detail = exc.response.text[:1000]
            raise HTTPException(status_code=502, detail=f"Spark Qwen Chat request failed: {detail}") from exc

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected_model = payload.get("model") or settings.spark_qwen_model
        raw = self.chat_completions(payload)
        text = _extract_message_text(raw)
        report_model_usage(
            model=str(selected_model),
            capability="llm_chat",
            input_text=messages_text(payload.get("messages") or []),
            output_text=text,
            raw_usage=raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
        )
        return {
            "text": text,
            "model": raw.get("model") or selected_model,
            "provider": "spark_qwen_chat",
            "raw": raw,
        }


chat_with_spark_qwen = SparkQwenChatClient()
