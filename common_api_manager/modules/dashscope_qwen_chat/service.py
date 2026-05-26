from __future__ import annotations

import time
from typing import Any

import dashscope
import requests
from dashscope import Generation
from fastapi import HTTPException

from common.settings import settings
from common.usage import messages_text, report_model_usage


def _extract_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    return str(content).strip()


def _obj_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _message_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)

    data: dict[str, Any] = {}
    for key in ("role", "content", "tool_calls"):
        value = getattr(message, key, None)
        if value is not None:
            data[key] = value
    return data


def _dashscope_response_to_openai(payload: dict[str, Any], response: Any) -> dict[str, Any]:
    output = _obj_get(response, "output", {}) or {}
    choices = _obj_get(output, "choices", []) or []
    normalized_choices: list[dict[str, Any]] = []

    for index, choice in enumerate(choices):
        message = _message_to_dict(_obj_get(choice, "message", {}) or {})
        finish_reason = _obj_get(choice, "finish_reason")
        if message.get("tool_calls"):
            finish_reason = "tool_calls"
        normalized_choices.append(
            {
                "index": _obj_get(choice, "index", index),
                "message": message,
                "finish_reason": finish_reason,
            }
        )

    usage = _obj_get(response, "usage") or _obj_get(output, "usage")
    request_id = _obj_get(response, "request_id") or _obj_get(output, "request_id")
    return {
        "id": request_id,
        "object": "chat.completion",
        "model": payload.get("model"),
        "choices": normalized_choices,
        "usage": usage,
    }


class DashScopeQwenChatClient:
    def _headers(self) -> dict[str, str]:
        if not settings.dashscope_llm_api_key:
            raise HTTPException(status_code=500, detail="DASHSCOPE_LLM_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {settings.dashscope_llm_api_key}",
            "Content-Type": "application/json",
        }

    def health(self) -> dict[str, object]:
        return {
            "provider": "dashscope_qwen_chat",
            "base_url": settings.dashscope_base_http_api_url,
            "compatible_base_url": settings.dashscope_compatible_base_url,
            "model": settings.dashscope_text_model,
            "api_key_configured": bool(settings.dashscope_llm_api_key),
            "supports_tools": True,
        }

    def chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = dict(payload)
        request_payload["model"] = request_payload.get("model") or settings.dashscope_text_model
        request_payload.setdefault("stream", False)

        if request_payload["model"].startswith("qwen3-") and not request_payload.get("stream"):
            request_payload.setdefault("enable_thinking", False)
        if not request_payload.get("messages"):
            raise HTTPException(status_code=400, detail="messages is required")

        try:
            return self._chat_completions_with_dashscope_sdk(request_payload)
        except HTTPException:
            raise
        except Exception:
            if not request_payload.get("tools"):
                raise
            return self._chat_completions_with_compatible_http(request_payload)

    def _chat_completions_with_dashscope_sdk(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        if not settings.dashscope_llm_api_key:
            raise HTTPException(status_code=500, detail="DASHSCOPE_LLM_API_KEY is not configured")

        dashscope.api_key = settings.dashscope_llm_api_key
        dashscope.base_http_api_url = settings.dashscope_base_http_api_url

        call_kwargs: dict[str, Any] = {
            "api_key": settings.dashscope_llm_api_key,
            "model": request_payload["model"],
            "messages": request_payload["messages"],
            "result_format": "message",
        }
        for key in (
            "tools",
            "tool_choice",
            "temperature",
            "top_p",
            "max_tokens",
            "stream",
            "enable_thinking",
        ):
            if key in request_payload:
                call_kwargs[key] = request_payload[key]

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = Generation.call(**call_kwargs)
                status_code = getattr(response, "status_code", None)
                if status_code is not None and status_code != 200:
                    code = getattr(response, "code", "")
                    message = getattr(response, "message", "")
                    raise HTTPException(
                        status_code=502,
                        detail=f"DashScope Qwen SDK request failed: {status_code} {code} {message}",
                    )
                return _dashscope_response_to_openai(request_payload, response)
            except HTTPException:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))

        raise RuntimeError(f"DashScope Qwen SDK request failed: {last_exc}")

    def _chat_completions_with_compatible_http(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        last_exc: requests.RequestException | None = None
        for attempt in range(3):
            try:
                response = requests.post(
                    f"{settings.dashscope_compatible_base_url}/chat/completions",
                    headers=self._headers(),
                    json=request_payload,
                    timeout=settings.dashscope_timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_exc = exc
                if exc.response is not None and exc.response.status_code < 500:
                    break
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))

        detail = str(last_exc)
        if last_exc is not None and last_exc.response is not None:
            detail = last_exc.response.text[:1000]
        raise HTTPException(status_code=502, detail=f"DashScope Qwen Chat request failed: {detail}")

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected_model = payload.get("model") or settings.dashscope_text_model
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
            "provider": "dashscope_qwen_chat",
            "raw": raw,
        }


chat_with_dashscope_qwen = DashScopeQwenChatClient()
