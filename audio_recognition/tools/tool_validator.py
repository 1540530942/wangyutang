from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from audio_recognition.core.envelope import DecisionEnvelope, TaskStep, ToolCall
from audio_recognition.skills.registry import OBSERVATION_TOOLS, SYSTEM_TOOLS, SkillRegistry, load_skill_registry


def _reject(call: ToolCall, reason: str) -> ToolCall:
    updated = call.model_copy(deep=True)
    updated.status = "rejected"
    updated.error = reason
    return updated


def _registry(registry_path: str | Path | None = None, catalog_path: str | Path | None = None) -> SkillRegistry:
    candidate = Path(registry_path) if registry_path else None
    if candidate and candidate.suffix.lower() == ".json" and catalog_path is None:
        return load_skill_registry(None, candidate)
    return load_skill_registry(candidate, catalog_path)


def _append_validator_error(envelope: DecisionEnvelope, rejected: ToolCall) -> None:
    envelope.validated_tool_calls.append(rejected)
    envelope.errors.append({"stage": "validator", "message": rejected.error, "tool_call": rejected.model_dump(), "t": time.time()})


def _normalize_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    if tool == "camera_snapshot" and "purpose" not in normalized and normalized.get("reason"):
        normalized["purpose"] = normalized["reason"]
    if tool == "ask_confirmation":
        if "timeout_ms" not in normalized and normalized.get("timeout_s") is not None:
            try:
                normalized["timeout_ms"] = int(normalized["timeout_s"]) * 1000
            except (TypeError, ValueError):
                pass
        if "timeout_s" not in normalized and normalized.get("timeout_ms") is not None:
            try:
                normalized["timeout_s"] = max(1, int(normalized["timeout_ms"]) // 1000)
            except (TypeError, ValueError):
                pass
    if tool == "finish" and "message" not in normalized and normalized.get("final") is not None:
        normalized["message"] = str(normalized.get("final") or "")
    return normalized


def _validate_duration(skill_id: str, args: dict[str, Any], registry: SkillRegistry) -> tuple[int | None, str]:
    spec = registry.get(skill_id)
    duration = args.get("duration_ms")
    if duration is None:
        return None, ""
    try:
        value = int(duration)
    except (TypeError, ValueError):
        return None, "invalid_duration_ms"
    if value < (spec.min_duration_ms if spec else 0):
        return None, "invalid_duration_ms"
    if spec and spec.max_duration_ms is not None and value > spec.max_duration_ms:
        return spec.max_duration_ms, "duration_clipped"
    return value, ""


def validate_tool_call(
    envelope: DecisionEnvelope,
    call: ToolCall,
    registry_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
) -> TaskStep | None:
    registry = _registry(registry_path, catalog_path)
    envelope.t_validate = time.time()
    allowed_tools = {spec.tool for spec in registry.skills.values() if spec.enabled} | OBSERVATION_TOOLS | SYSTEM_TOOLS
    if call.tool not in allowed_tools:
        _append_validator_error(envelope, _reject(call, "unsupported_tool"))
        return None

    args: dict[str, Any] = _normalize_args(call.tool, dict(call.args))
    wait_until = str(args.get("wait_until") or "completed")
    if wait_until not in {"accepted", "completed"}:
        _append_validator_error(envelope, _reject(call, "invalid_wait_until"))
        return None

    if call.tool == "finish":
        accepted = call.model_copy(deep=True)
        accepted.args = args
        accepted.status = "validated"
        envelope.validated_tool_calls.append(accepted)
        return None

    if call.tool in OBSERVATION_TOOLS:
        accepted = call.model_copy(deep=True)
        accepted.args = args
        accepted.status = "validated"
        envelope.validated_tool_calls.append(accepted)
        return None

    skill_id = str(args.get("skill_id") or "")
    if call.tool == "emergency_stop":
        skill_id = "emergency_stop"
        args["skill_id"] = skill_id

    spec = registry.get(skill_id)
    if spec is None or spec.tool != call.tool:
        reason = {
            "dispatch_action": "unsupported_action_skill",
            "dispatch_face": "unsupported_face_skill",
            "emergency_stop": "unsupported_emergency_skill",
        }.get(call.tool, "unsupported_skill")
        _append_validator_error(envelope, _reject(call, reason))
        return None

    duration, duration_note = _validate_duration(skill_id, args, registry)
    if duration_note == "invalid_duration_ms":
        _append_validator_error(envelope, _reject(call, duration_note))
        return None
    if duration is not None:
        args["duration_ms"] = duration

    accepted = call.model_copy(deep=True)
    accepted.args = args
    accepted.status = "validated"
    if duration_note:
        accepted.result["validator_note"] = duration_note
    envelope.validated_tool_calls.append(accepted)
    task = TaskStep(
        task_id=accepted.call_id.replace("call_", "task_"),
        skill_id=skill_id,
        route=spec.route,
        order=int(args.get("order") or len(envelope.tasks) + 1),
        duration_ms=args.get("duration_ms"),
        wait_until=wait_until,
    )
    envelope.tasks.append(task)
    return task


def validate_tool_calls(
    envelope: DecisionEnvelope,
    registry_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
) -> DecisionEnvelope:
    original_calls = list(envelope.tool_calls)
    envelope.validated_tool_calls = []
    envelope.tasks = []
    for call in original_calls:
        validate_tool_call(envelope, call, registry_path=registry_path, catalog_path=catalog_path)
    return envelope
