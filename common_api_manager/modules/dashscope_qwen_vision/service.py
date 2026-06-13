from __future__ import annotations

import base64
import io
import time
from typing import Any

import dashscope
from dashscope import MultiModalConversation
from fastapi import HTTPException, UploadFile
from PIL import Image

from common.settings import settings
from common.usage import report_model_usage


def _obj_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _extract_vision_text(response: Any) -> str:
    output = _obj_get(response, "output", {}) or {}
    choices = _obj_get(output, "choices", []) or []
    if not choices:
        return ""
    message = _obj_get(choices[0], "message", {}) or {}
    content = _obj_get(message, "content", "")
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    texts.append(str(text))
            elif item is not None:
                texts.append(str(item))
        return "\n".join(texts).strip()
    return str(content).strip()


def _dashscope_response_to_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    output = _obj_get(response, "output", {}) or {}
    usage = _obj_get(response, "usage") or _obj_get(output, "usage")
    return {
        "request_id": _obj_get(response, "request_id") or _obj_get(output, "request_id"),
        "output": output,
        "usage": usage,
    }


def _image_mime(data: bytes, filename: str = "") -> str:
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = (image.format or "").lower()
    except Exception:
        fmt = ""
    if fmt in {"jpeg", "jpg"}:
        return "image/jpeg"
    if fmt == "png":
        return "image/png"
    if fmt == "webp":
        return "image/webp"
    lower = filename.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    raise HTTPException(status_code=400, detail="Unsupported image type. Use JPEG, PNG, or WebP.")


def _data_uri(data: bytes, filename: str = "") -> str:
    mime = _image_mime(data, filename)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _resize_image(data: bytes) -> tuple[bytes, dict[str, object]]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            original_size = image.size
            image = image.convert("RGB")
            max_side = max(1, settings.vision_resize_max_side)
            if max(image.size) > max_side:
                image.thumbnail((max_side, max_side), Image.LANCZOS)
            output = io.BytesIO()
            quality = max(1, min(95, settings.vision_jpeg_quality))
            image.save(output, format="JPEG", quality=quality, optimize=True)
            resized = output.getvalue()
            return resized, {
                "original_width": original_size[0],
                "original_height": original_size[1],
                "width": image.size[0],
                "height": image.size[1],
                "bytes": len(resized),
                "format": "jpeg",
            }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image file: {exc}") from exc


class DashScopeQwenVisionClient:
    def health(self) -> dict[str, object]:
        return {
            "provider": "dashscope_qwen_vision",
            "base_url": settings.dashscope_base_http_api_url,
            "model": settings.dashscope_vision_model,
            "api_key_configured": bool(settings.dashscope_llm_api_key),
            "max_image_bytes": settings.vision_max_image_bytes,
            "resize_max_side": settings.vision_resize_max_side,
            "jpeg_quality": settings.vision_jpeg_quality,
        }

    def models(self) -> dict[str, object]:
        return {
            "object": "list",
            "provider": "dashscope_qwen_vision",
            "data": [
                {
                    "id": settings.dashscope_vision_model,
                    "object": "model",
                    "capability": "vision_understanding",
                }
            ],
        }

    async def analyze_upload(self, file: UploadFile, question: str, model: str | None = None) -> dict[str, Any]:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="image file is required")
        if len(data) > settings.vision_max_image_bytes:
            raise HTTPException(status_code=413, detail=f"image exceeds {settings.vision_max_image_bytes} bytes")
        return self.analyze_image_bytes(data, question, model=model, filename=file.filename or "")

    def analyze_image_bytes(self, data: bytes, question: str, model: str | None = None, filename: str = "") -> dict[str, Any]:
        if not settings.dashscope_llm_api_key:
            raise HTTPException(status_code=500, detail="DASHSCOPE_LLM_API_KEY is not configured")
        prompt = question.strip() or settings.vision_default_question
        selected_model = model or settings.dashscope_vision_model
        resized_data, image_info = _resize_image(data)
        dashscope.api_key = settings.dashscope_llm_api_key
        dashscope.base_http_api_url = settings.dashscope_base_http_api_url
        messages = [
            {
                "role": "system",
                "content": [{"text": "你是一个专业的图像理解助手。请准确识别图片内容、文字、图表和关键信息。"}],
            },
            {
                "role": "user",
                "content": [
                    {"image": _data_uri(resized_data, "image.jpg")},
                    {"text": prompt},
                ],
            },
        ]
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = MultiModalConversation.call(
                    api_key=settings.dashscope_llm_api_key,
                    model=selected_model,
                    messages=messages,
                )
                status_code = getattr(response, "status_code", None)
                if status_code is not None and status_code != 200:
                    code = getattr(response, "code", "")
                    message = getattr(response, "message", "")
                    raise HTTPException(status_code=502, detail=f"DashScope vision request failed: {status_code} {code} {message}")
                raw = _dashscope_response_to_dict(response)
                text = _extract_vision_text(response)
                report_model_usage(
                    model=selected_model,
                    capability="vision_understanding",
                    input_text=prompt,
                    output_text=text,
                    raw_usage=raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
                )
                return {
                    "text": text,
                    "model": selected_model,
                    "provider": "dashscope_qwen_vision",
                    "image": image_info,
                    "raw": raw,
                }
            except HTTPException:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        raise HTTPException(status_code=502, detail=f"DashScope vision request failed: {last_exc}")


vision_with_dashscope_qwen = DashScopeQwenVisionClient()
