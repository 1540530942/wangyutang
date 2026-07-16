from __future__ import annotations

import base64
import io
import time
from typing import Any

import requests
from fastapi import HTTPException, UploadFile
from PIL import Image

from common.settings import settings
from common.usage import report_model_usage


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


class LvQwenVisionClient:
    def _url(self, path: str) -> str:
        return f"{settings.lv_vl_base_url}{path}"

    def health(self) -> dict[str, object]:
        try:
            response = requests.get(self._url("/v1/models"), timeout=8)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"LV VL health check failed: {exc}") from exc
        return {
            "provider": "lv_qwen_vision",
            "base_url": settings.lv_vl_base_url,
            "model": settings.lv_vl_model,
            "max_image_bytes": settings.vision_max_image_bytes,
            "resize_max_side": settings.vision_resize_max_side,
            "jpeg_quality": settings.vision_jpeg_quality,
            "models": data,
        }

    def models(self) -> dict[str, object]:
        return {
            "object": "list",
            "provider": "lv_qwen_vision",
            "data": [{"id": settings.lv_vl_model, "object": "model", "capability": "vision_understanding"}],
        }

    async def analyze_upload(self, file: UploadFile, question: str, model: str | None = None) -> dict[str, Any]:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="image file is required")
        if len(data) > settings.vision_max_image_bytes:
            raise HTTPException(status_code=413, detail=f"image exceeds {settings.vision_max_image_bytes} bytes")
        return self.analyze_image_bytes(data, question, model=model, filename=file.filename or "")

    def analyze_image_bytes(self, data: bytes, question: str, model: str | None = None, filename: str = "") -> dict[str, Any]:
        prompt = question.strip() or settings.vision_default_question
        selected_model = model or settings.lv_vl_model
        resized_data, image_info = _resize_image(data)
        img_b64 = base64.b64encode(resized_data).decode("ascii")
        data_url = f"data:image/jpeg;base64,{img_b64}"
        payload = {
            "model": selected_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": 512,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.post(
                    self._url("/v1/chat/completions"),
                    json=payload,
                    timeout=settings.lv_timeout_seconds,
                )
                response.raise_for_status()
                raw = response.json()
                choices = raw.get("choices") or []
                text = ""
                if choices:
                    msg = (choices[0] or {}).get("message") or {}
                    content = msg.get("content") or ""
                    marker = "</think>"
                    text = content.split(marker, 1)[1].strip() if marker in content else content.strip()
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
                    "provider": "lv_qwen_vision",
                    "image": image_info,
                    "raw": raw,
                }
            except HTTPException:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        raise HTTPException(status_code=502, detail=f"LV VL request failed: {last_exc}")


vision_with_lv_qwen = LvQwenVisionClient()
