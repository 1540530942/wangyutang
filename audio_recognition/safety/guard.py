from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from audio_recognition.core.envelope import DecisionEnvelope, TaskStep
from audio_recognition.skills.registry import SkillRegistry, SkillSpec, load_skill_registry


NEGATIVE_WORDS = ("\u4e0d\u8981", "\u522b", "\u4e0d\u8bb8", "\u4e0d\u7528", "\u4e0d\u8981\u53bb", "\u522b\u53bb")
EMERGENCY_WORDS = ("\u6025\u505c", "\u505c\u6b62", "\u505c\u4e0b", "\u505c\u8f66", "\u5239\u8f66", "\u522b\u52a8", "\u4e0d\u8981\u52a8", "stop", "e-stop")
MOVEMENT_PREFIXES = ("move_", "turn_")
MAX_SEQUENCE_ACTIONS = 3
MAX_TOTAL_DURATION_MS = 10000


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def has_emergency_intent(text: str) -> bool:
    return _contains_any(text or "", EMERGENCY_WORDS)


def has_negative_intent(text: str) -> bool:
    return _contains_any(text or "", NEGATIVE_WORDS)


def _reject_task(task: TaskStep, reason: str) -> TaskStep:
    updated = task.model_copy(deep=True)
    updated.status = "rejected"
    updated.error = reason
    return updated


def _registry(registry_path: str | Path | None = None, catalog_path: str | Path | None = None) -> SkillRegistry:
    return load_skill_registry(registry_path, catalog_path)


def _latest_completed_observation(envelope: DecisionEnvelope, tool: str) -> dict[str, Any] | None:
    for item in reversed(envelope.observations):
        if item.get("tool") == tool and item.get("status") == "completed" and isinstance(item.get("data"), dict):
            return item
    return None


def _observation_timestamp(observation: dict[str, Any]) -> float | None:
    for key in ("t_start", "t"):
        try:
            value = observation.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _is_recent_observation(observation: dict[str, Any] | None, max_age_ms: int, now: float) -> bool:
    if observation is None:
        return False
    ts = _observation_timestamp(observation)
    if ts is None:
        return False
    return (now - ts) * 1000 <= max_age_ms


def _latest_front_distance_observation(envelope: DecisionEnvelope) -> tuple[dict[str, Any] | None, str]:
    observation = _latest_completed_observation(envelope, "front_distance")
    if observation is not None:
        return observation, "front_distance"
    return _latest_completed_observation(envelope, "camera_snapshot"), "camera_snapshot"


def _front_distance_estimate(observation: dict[str, Any] | None) -> float | None:
    if observation is None:
        return None
    data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
    try:
        value = data.get("front_distance_estimate_cm")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _reject_with_safety_result(
    envelope: DecisionEnvelope,
    task: TaskStep,
    result: dict[str, Any],
    reason: str,
    *,
    spec: SkillSpec | None = None,
    detail: dict[str, Any] | None = None,
) -> TaskStep:
    updated = _reject_task(task, reason)
    result["checks"].append(reason)
    result.update({"allowed": False, "reason": reason, "skill_id": task.skill_id})
    if spec is not None:
        result["risk"] = spec.risk
    if detail:
        result.update(detail)
    envelope.safety_result = result
    return updated


def _int_default(registry: SkillRegistry, group: str, key: str, fallback: int) -> int:
    values = registry.defaults.get(group) if isinstance(registry.defaults.get(group), dict) else {}
    try:
        return int(values.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def _check_skill_preconditions(
    envelope: DecisionEnvelope,
    task: TaskStep,
    spec: SkillSpec | None,
    registry: SkillRegistry,
    result: dict[str, Any],
    now: float,
) -> TaskStep | None:
    if spec is None:
        return None
    front_observation = None
    front_observation_tool = "camera_snapshot"
    camera_ttl = _int_default(registry, "observation_ttl_ms", "camera_snapshot", 2000)
    front_ttl = _int_default(registry, "observation_ttl_ms", "front_distance", camera_ttl)
    threshold_cm = _int_default(registry, "safety_thresholds", "min_front_distance_estimate_cm", 15)
    for condition in spec.pre_conditions:
        if condition == "recent_camera_snapshot":
            front_observation, front_observation_tool = _latest_front_distance_observation(envelope)
            ttl = front_ttl if front_observation_tool == "front_distance" else camera_ttl
            if not _is_recent_observation(front_observation, ttl, now):
                return _reject_with_safety_result(
                    envelope,
                    task,
                    result,
                    "recent_camera_snapshot_required",
                    spec=spec,
                    detail={"required_observation": "front_distance_or_camera_snapshot"},
                )
        elif condition == "recent_front_distance":
            front_observation, front_observation_tool = _latest_front_distance_observation(envelope)
            ttl = front_ttl if front_observation_tool == "front_distance" else camera_ttl
            if not _is_recent_observation(front_observation, ttl, now):
                return _reject_with_safety_result(
                    envelope,
                    task,
                    result,
                    "recent_front_distance_required",
                    spec=spec,
                    detail={"required_observation": "front_distance"},
                )
        elif condition == "front_distance_clear":
            front_observation, front_observation_tool = _latest_front_distance_observation(envelope)
            ttl = front_ttl if front_observation_tool == "front_distance" else camera_ttl
            if not _is_recent_observation(front_observation, ttl, now):
                return _reject_with_safety_result(
                    envelope,
                    task,
                    result,
                    "recent_camera_snapshot_required",
                    spec=spec,
                    detail={"required_observation": "front_distance_or_camera_snapshot"},
                )
            distance_cm = _front_distance_estimate(front_observation)
            if distance_cm is None:
                return _reject_with_safety_result(
                    envelope,
                    task,
                    result,
                    "front_distance_observation_missing",
                    spec=spec,
                    detail={"required_observation": front_observation_tool, "threshold_cm": threshold_cm},
                )
            if distance_cm <= threshold_cm:
                return _reject_with_safety_result(
                    envelope,
                    task,
                    result,
                    "front_distance_too_close",
                    spec=spec,
                    detail={"required_observation": front_observation_tool, "observed_value": distance_cm, "threshold_cm": threshold_cm},
                )
    return None


def run_safety_guard_for_task(
    envelope: DecisionEnvelope,
    task: TaskStep,
    registry_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
) -> TaskStep:
    envelope.t_safety = time.time()
    registry = _registry(registry_path, catalog_path)
    spec = registry.get(task.skill_id)
    result = dict(envelope.safety_result or {"allowed": True, "reason": "", "checks": []})
    result.setdefault("checks", [])
    matching_call = next((call for call in envelope.validated_tool_calls if call.call_id.replace("call_", "task_") == task.task_id), None)
    fragment = str((matching_call.args if matching_call else {}).get("text") or "")
    if task.skill_id == "emergency_stop":
        result.update({"allowed": True, "reason": "emergency_stop_detected", "priority": "highest"})
        envelope.safety_result = result
        return task
    if _contains_any(fragment, NEGATIVE_WORDS):
        updated = _reject_task(task, "negative_instruction_detected")
        result["checks"].append("negative_instruction_detected")
        result.update({"allowed": False, "reason": "negative_instruction_detected"})
        envelope.safety_result = result
        return updated
    movement_tasks = [item for item in envelope.tasks if item.status != "rejected" and item.skill_id.startswith(MOVEMENT_PREFIXES)]
    if len(movement_tasks) > MAX_SEQUENCE_ACTIONS:
        updated = _reject_task(task, "too_many_movement_tasks")
        result["checks"].append("movement_sequence_clipped")
        result.update({"allowed": False, "reason": "too_many_movement_tasks"})
        envelope.safety_result = result
        return updated
    total_duration = sum(int(item.duration_ms or 0) for item in envelope.tasks if item.status != "rejected")
    if total_duration > MAX_TOTAL_DURATION_MS:
        updated = _reject_task(task, "total_duration_exceeded")
        result.update({"allowed": False, "reason": "total_duration_exceeded"})
        envelope.safety_result = result
        return updated
    if matching_call and float(matching_call.args.get("confidence") or 1.0) < 0.65:
        updated = _reject_task(task, "low_confidence_confirmation_required")
        envelope.needs_confirmation = True
        result["checks"].append("low_confidence_confirmation_required")
        result.update({"allowed": False, "reason": "low_confidence_confirmation_required"})
        envelope.safety_result = result
        return updated
    precondition_rejection = _check_skill_preconditions(envelope, task, spec, registry, result, time.time())
    if precondition_rejection is not None:
        return precondition_rejection
    result.update({"allowed": True, "reason": result.get("reason", "")})
    envelope.safety_result = result
    return task


def run_safety_guard(
    envelope: DecisionEnvelope,
    registry_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
) -> DecisionEnvelope:
    envelope.t_safety = time.time()
    if _contains_any(envelope.transcript or "", EMERGENCY_WORDS) and not envelope.tasks:
        envelope.tasks = [TaskStep(skill_id="emergency_stop", route="action", order=0, wait_until="accepted", status="pending")]
    envelope.tasks = [run_safety_guard_for_task(envelope, task, registry_path=registry_path, catalog_path=catalog_path) for task in envelope.tasks]
    return envelope
