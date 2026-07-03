from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from common.settings import settings
from .service import chat_with_lv_qwen


router = APIRouter(tags=["lv_qwen_chat"])


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: Any = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(default=settings.lv_chat_model)
    messages: list[ChatMessage] = Field(..., min_length=1)
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False


@router.get("/api/chat/qwen3/health")
def qwen3_chat_health() -> dict[str, object]:
    return chat_with_lv_qwen.health()


@router.get("/api/chat/qwen3/models")
@router.get("/api/llm/models")
def qwen3_chat_models() -> dict[str, object]:
    return chat_with_lv_qwen.models()


@router.post("/api/chat/qwen3/completions")
def qwen3_chat_completions(payload: ChatCompletionRequest) -> dict[str, Any]:
    return chat_with_lv_qwen.chat_completions(payload.model_dump(exclude_none=True))


@router.post("/api/llm/chat")
def common_llm_chat(payload: ChatCompletionRequest) -> dict[str, Any]:
    return chat_with_lv_qwen.chat(payload.model_dump(exclude_none=True))
