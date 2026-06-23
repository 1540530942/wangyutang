from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from common.settings import settings
from .service import vision_with_spark_qwen


router = APIRouter(tags=["spark_qwen_vision"])


class VisionAnalyzeRequest(BaseModel):
    image_base64: str = Field(..., min_length=1)
    question: str = Field(default=settings.vision_default_question)
    model: str | None = Field(default=None)
    filename: str = Field(default="image.jpg")


@router.get("/api/vision/spark/health")
@router.get("/api/vision/spark-qwen/health")
def spark_qwen_vision_health() -> dict[str, object]:
    return vision_with_spark_qwen.health()


@router.get("/api/vision/spark/models")
@router.get("/api/vision/spark-qwen/models")
def spark_qwen_vision_models() -> dict[str, object]:
    return vision_with_spark_qwen.models()


@router.post("/api/vision/spark/analyze")
@router.post("/api/vision/spark-qwen/analyze")
async def spark_qwen_vision_analyze(
    file: UploadFile = File(...),
    question: str = Form(default=settings.vision_default_question),
    model: str | None = Form(default=None),
) -> dict[str, Any]:
    return await vision_with_spark_qwen.analyze_upload(file, question, model=model)


@router.post("/api/vision/spark/analyze-json")
@router.post("/api/vision/spark-qwen/analyze-json")
def spark_qwen_vision_analyze_json(payload: VisionAnalyzeRequest) -> dict[str, Any]:
    try:
        image_data = base64.b64decode(payload.image_base64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=400, detail="image_base64 must be valid base64") from exc
    return vision_with_spark_qwen.analyze_image_bytes(
        image_data,
        payload.question,
        model=payload.model,
        filename=payload.filename,
    )
