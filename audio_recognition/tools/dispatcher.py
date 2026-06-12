from __future__ import annotations

import time
import json
import urllib.request
from typing import Any

from audio_recognition.core.contracts import PlannedTask
from audio_recognition.core.envelope import DecisionEnvelope, TaskStep
from audio_recognition.tools.executors import execute_planned_task


def _planned_from_task(task: TaskStep) -> PlannedTask:
    return PlannedTask(skill_id=task.skill_id, route="face" if task.route == "face" else "action", transcript="", metadata={"task_id": task.task_id})


def _post_json(url: str, payload: dict[str, Any], timeout: float = 12) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_json(url: str, timeout: float = 5) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _execute_local_action(task: TaskStep, cloud_config: dict[str, Any] | None) -> dict[str, Any]:
    cloud_config = cloud_config or {}
    action_server = str(cloud_config.get("local_action_server") or cloud_config.get("action_server") or "").rstrip("/")
    if not action_server:
        raise RuntimeError("local action_server is required")
    settings = {"unit_distance_cm": 1.0, "sensitivity": 2.0, "stop_publish_times": 5}
    settings.update(dict(cloud_config.get("local_settings") or {}))
    if task.duration_ms is not None:
        settings["requested_duration_ms"] = task.duration_ms
    result = _post_json(f"{action_server}/execute", {"action": task.skill_id, "settings": settings})
    try:
        result["health"] = _fetch_json(f"{action_server}/health")
    except Exception as exc:  # noqa: BLE001
        result["health_error"] = str(exc)
    return result


def dispatch_task(
    envelope: DecisionEnvelope,
    task: TaskStep,
    *,
    cloud_config: dict[str, Any] | None,
    source: str,
    dispatch_mode: str = "dry_run",
) -> dict[str, Any]:
    envelope.dispatch_mode = dispatch_mode if dispatch_mode in {"dry_run", "cloud_queue", "local_first"} else "dry_run"
    if task.status == "rejected":
        result = {"task_id": task.task_id, "skill_id": task.skill_id, "status": "rejected", "error": task.error}
        envelope.dispatch_results.append(result)
        return result
    if envelope.dispatch_mode == "dry_run":
        task.status = "completed"
        task.result = {"dry_run": True, "skill_id": task.skill_id, "route": task.route}
        result = {"task_id": task.task_id, "skill_id": task.skill_id, "status": "dry_run", "result": task.result}
        envelope.dispatch_results.append(result)
        return result
    task.status = "running"
    if envelope.dispatch_mode == "local_first" and task.route == "action":
        try:
            local_result = _execute_local_action(task, cloud_config)
            execution = {"action_task": local_result, "face_task": None, "action_error": "" if local_result.get("ok", True) else str(local_result.get("error", "")), "face_error": ""}
        except Exception as exc:  # noqa: BLE001
            execution = {"action_task": None, "face_task": None, "action_error": str(exc), "face_error": ""}
    else:
        execution = execute_planned_task(_planned_from_task(task), envelope.transcript, cloud_config, source)
    if execution.get("action_error") or execution.get("face_error"):
        task.status = "failed"
        task.error = str(execution.get("action_error") or execution.get("face_error") or "")
    else:
        task.status = "completed"
    task.result = execution
    result = {"task_id": task.task_id, "skill_id": task.skill_id, "status": task.status, "result": execution, "error": task.error}
    envelope.dispatch_results.append(result)
    envelope.observations.append({"task_id": task.task_id, "skill_id": task.skill_id, "status": task.status, "result": execution, "t": time.time()})
    return result


def dispatch_envelope(
    envelope: DecisionEnvelope,
    *,
    cloud_config: dict[str, Any] | None,
    source: str,
    dispatch_mode: str = "dry_run",
) -> DecisionEnvelope:
    envelope.dispatch_mode = dispatch_mode if dispatch_mode in {"dry_run", "cloud_queue", "local_first"} else "dry_run"
    envelope.t_dispatch_start = time.time()
    results: list[dict[str, Any]] = []
    for task in sorted(envelope.tasks, key=lambda item: item.order):
        if task.status == "rejected":
            results.append({"task_id": task.task_id, "skill_id": task.skill_id, "status": "rejected", "error": task.error})
            continue
        if envelope.dispatch_mode == "dry_run":
            task.status = "completed"
            task.result = {"dry_run": True, "skill_id": task.skill_id, "route": task.route}
            results.append({"task_id": task.task_id, "skill_id": task.skill_id, "status": "dry_run", "result": task.result})
            continue
        result = dispatch_task(envelope, task, cloud_config=cloud_config, source=source, dispatch_mode=envelope.dispatch_mode)
        results.append(result)
        if task.status != "completed" or task.skill_id == "emergency_stop":
            break
    envelope.dispatch_results = results
    envelope.t_dispatch_end = time.time()
    return envelope
