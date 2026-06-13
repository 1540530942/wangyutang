from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from common.settings import settings
from .service import chat_with_spark_qwen


router = APIRouter(tags=["spark_qwen_chat"])


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: Any = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(default=settings.spark_qwen_model)
    messages: list[ChatMessage] = Field(..., min_length=1)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    chat_template_kwargs: dict[str, Any] | None = None


@router.get("/api/llm/spark-qwen/health")
@router.get("/api/llm/qwen3.6-35b/health")
def spark_qwen_health() -> dict[str, object]:
    return chat_with_spark_qwen.health()


@router.get("/api/llm/spark-qwen/models")
@router.get("/api/llm/qwen3.6-35b/models")
def spark_qwen_models() -> dict[str, object]:
    return chat_with_spark_qwen.models()


@router.post("/api/llm/spark-qwen/chat")
@router.post("/api/llm/qwen3.6-35b/chat")
def spark_qwen_chat(payload: ChatCompletionRequest) -> dict[str, Any]:
    return chat_with_spark_qwen.chat(payload.model_dump(exclude_none=True))


@router.post("/api/llm/spark-qwen/chat/completions")
@router.post("/api/llm/qwen3.6-35b/chat/completions")
def spark_qwen_chat_completions(payload: ChatCompletionRequest) -> dict[str, Any]:
    return chat_with_spark_qwen.chat_completions(payload.model_dump(exclude_none=True))
