from __future__ import annotations

import secrets
import time
from typing import Any, Literal

from pydantic import BaseModel, Field


ToolStatus = Literal["pending", "validated", "rejected", "executed", "failed"]
TaskStatus = Literal["pending", "accepted", "running", "completed", "failed", "cancelled", "rejected", "expired"]
RouteKind = Literal["action", "face", "observation", "system", "none"]


def build_envelope_id(prefix: str = "env") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"


def build_call_id(prefix: str = "call") -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class ToolCall(BaseModel):
    call_id: str = Field(default_factory=build_call_id)
    tool: str = Field(..., max_length=80)
    args: dict[str, Any] = Field(default_factory=dict)
    status: ToolStatus = "pending"
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class TaskStep(BaseModel):
    task_id: str = Field(default_factory=lambda: build_call_id("task"))
    skill_id: str = Field("", max_length=80)
    route: RouteKind = "none"
    order: int = 0
    duration_ms: int | None = None
    depends_on: list[str] = Field(default_factory=list)
    wait_until: Literal["accepted", "completed"] = "completed"
    status: TaskStatus = "pending"
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    settings_override: dict[str, Any] = Field(default_factory=dict)


class DecisionEnvelope(BaseModel):
    protocol_version: str = "react_v1_single_tool"
    envelope_id: str = Field(default_factory=build_envelope_id)
    device_id: str = Field("turbopi-01", max_length=80)
    source: str = Field("audio", max_length=80)
    t_created: float = Field(default_factory=time.time)
    t_capture: float | None = None
    t_transcribe: float | None = None
    t_agent_start: float | None = None
    t_agent_end: float | None = None
    t_validate: float | None = None
    t_safety: float | None = None
    t_dispatch_start: float | None = None
    t_dispatch_end: float | None = None
    audio_ref: str | None = None
    audio_meta: dict[str, Any] = Field(default_factory=dict)
    transcript: str = ""
    asr_meta: dict[str, Any] = Field(default_factory=dict)
    agent_mode: Literal["react"] = "react"
    reasoning_summary: str = ""
    agent_steps: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    validated_tool_calls: list[ToolCall] = Field(default_factory=list)
    tasks: list[TaskStep] = Field(default_factory=list)
    safety_result: dict[str, Any] = Field(default_factory=dict)
    needs_confirmation: bool = False
    dispatch_mode: Literal["dry_run", "cloud_queue", "local_first"] = "dry_run"
    dispatch_results: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    react_messages: list[dict[str, Any]] = Field(default_factory=list)
    react_turns: list[dict[str, Any]] = Field(default_factory=list)
    source_chain: list[dict[str, Any]] = Field(default_factory=list)
    final_response: str = ""
    errors: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: dict[str, float] = Field(default_factory=dict)
    vehicle_execution: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    def add_error(self, stage: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.errors.append({"stage": stage, "message": message, "detail": detail or {}, "t": time.time()})

    def compute_latency(self) -> dict[str, float]:
        """Derive a per-stage latency breakdown (milliseconds) from recorded timestamps.

        Populated once routing finishes so the persisted envelope is directly
        analyzable for problem localization, replay, and RL reward shaping.
        Stages whose endpoints are missing are simply omitted rather than guessed.
        """

        def delta_ms(start: float | None, end: float | None) -> float | None:
            if start is None or end is None:
                return None
            return round((end - start) * 1000.0, 1)

        # t_transcribe is the absolute ASR-done time from the upstream audio
        # service, so created-minus-transcribe captures the inbound hop+queue.
        stages: dict[str, float | None] = {
            "asr": delta_ms(self.t_capture, self.t_transcribe),
            "ingest": delta_ms(self.t_transcribe, self.t_created),
            "agent": delta_ms(self.t_agent_start, self.t_agent_end),
            "dispatch": delta_ms(self.t_dispatch_start, self.t_dispatch_end),
            "total": delta_ms(self.t_capture or self.t_transcribe or self.t_created, self.t_dispatch_end or self.t_agent_end),
        }
        # Car-side execution latency, reported by action_move once the edge
        # device claims and completes the task (see vehicle_execution). Folding
        # it in makes the envelope the single source of truth for the full
        # web -> server -> car chain.
        ve = self.vehicle_execution or {}
        for key in ("vehicle_claim", "vehicle_exec", "vehicle_ros"):
            value = ve.get(key)
            if isinstance(value, (int, float)):
                stages[key] = round(float(value), 1)
        self.latency_ms = {key: value for key, value in stages.items() if value is not None}
        return self.latency_ms
