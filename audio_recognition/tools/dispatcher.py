from __future__ import annotations

import re
import time
import json
import urllib.request
import urllib.error
from typing import Any

from audio_recognition.core.contracts import PlannedTask
from audio_recognition.core.envelope import DecisionEnvelope, TaskStep
from audio_recognition.tools.executors import execute_planned_task


def _vehicle_execution_from_task(final_task: dict[str, Any]) -> dict[str, Any]:
    """Extract car-side execution latency/log from a completed action task.

    action_move reports claim/completion latency (queue->claim, claim->done)
    and the edge ROS controller emits ``elapsed_seconds`` in its output. This
    folds the car layer into the envelope so the full web->server->car chain
    is traceable in one record.
    """
    claim = final_task.get("claim_latency_seconds")
    completion = final_task.get("completion_latency_seconds")
    output = str(final_task.get("output") or "")
    ve: dict[str, Any] = {
        "task_id": final_task.get("id") or final_task.get("task_id") or "",
        "status": final_task.get("status") or "",
    }
    if isinstance(claim, (int, float)):
        ve["vehicle_claim"] = round(float(claim) * 1000.0, 1)
    if isinstance(claim, (int, float)) and isinstance(completion, (int, float)):
        ve["vehicle_exec"] = round((float(completion) - float(claim)) * 1000.0, 1)
    match = re.search(r"elapsed_seconds=([0-9.]+)", output)
    if match:
        try:
            ve["vehicle_ros"] = round(float(match.group(1)) * 1000.0, 1)
        except ValueError:
            pass
    if output:
        ve["output_tail"] = output[-600:]
    return ve


def _planned_from_task(task: TaskStep) -> PlannedTask:
    return PlannedTask(skill_id=task.skill_id, route="face" if task.route == "face" else "action", transcript="", metadata={"task_id": task.task_id, "settings_override": task.settings_override})


def _post_json(url: str, payload: dict[str, Any], timeout: float = 12) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_json(url: str, timeout: float = 5) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_action_task(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    task = payload.get("task")
    return task if isinstance(task, dict) else payload


def _action_task_result(task: TaskStep, action_payload: dict[str, Any], status: str) -> dict[str, Any]:
    action_task = _extract_action_task(action_payload)
    action_task_id = str(action_task.get("id") or action_task.get("task_id") or "")
    action_status = str(action_task.get("status") or "")
    return {
        "task_id": task.task_id,
        "skill_id": task.skill_id,
        "tool": "dispatch_action" if task.route == "action" else f"dispatch_{task.route}",
        "status": status,
        "action_task_id": action_task_id,
        "action_status": action_status,
        "wait_until": task.wait_until,
        "result": {
            "action_task": action_payload,
            "action_task_id": action_task_id,
            "action_status": action_status,
        },
        "error": task.error,
    }


def _wait_for_action_task(action_server: str, action_task_id: str, timeout_seconds: float, poll_interval_seconds: float) -> dict[str, Any]:
    if not action_server:
        raise RuntimeError("action_server is required to wait for task completion")
    if not action_task_id:
        raise RuntimeError("action_task_id is required to wait for task completion")
    deadline = time.monotonic() + max(timeout_seconds, 0.1)
    last_payload: dict[str, Any] = {}
    terminal_statuses = {"complete", "failed", "rejected", "expired"}
    while True:
        last_payload = _fetch_json(f"{action_server.rstrip('/')}/api/tasks/{action_task_id}")
        action_task = _extract_action_task(last_payload)
        if str(action_task.get("status") or "") in terminal_statuses:
            return last_payload
        if time.monotonic() >= deadline:
            raise TimeoutError(f"action task {action_task_id} did not complete within {timeout_seconds:.1f}s")
        time.sleep(max(poll_interval_seconds, 0.05))


def _execute_local_action(task: TaskStep, cloud_config: dict[str, Any] | None) -> dict[str, Any]:
    cloud_config = cloud_config or {}
    action_server = str(cloud_config.get("local_action_server") or cloud_config.get("action_server") or "").rstrip("/")
    if not action_server:
        raise RuntimeError("local action_server is required")
    settings = {"unit_distance_cm": 1.0, "sensitivity": 2.0, "stop_publish_times": 5}
    settings.update(dict(cloud_config.get("local_settings") or {}))
    settings.update(dict(task.settings_override or {}))
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
        task.result = {"dry_run": True, "skill_id": task.skill_id, "route": task.route, "settings_override": task.settings_override}
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
    elif task.route == "action" and envelope.dispatch_mode == "cloud_queue":
        action_payload = execution.get("action_task") if isinstance(execution.get("action_task"), dict) else {}
        action_task = _extract_action_task(action_payload)
        action_task_id = str(action_task.get("id") or action_task.get("task_id") or "")
        task.status = "accepted"
        task.result = execution
        accepted_result = _action_task_result(task, action_payload, "accepted")
        if task.wait_until == "accepted":
            envelope.dispatch_results.append(accepted_result)
            envelope.observations.append({"task_id": task.task_id, "skill_id": task.skill_id, "status": task.status, "result": execution, "t": time.time()})
            return accepted_result
        try:
            final_payload = _wait_for_action_task(
                str((cloud_config or {}).get("action_server") or ""),
                action_task_id,
                float((cloud_config or {}).get("action_wait_timeout_seconds") or 60.0),
                float((cloud_config or {}).get("action_poll_interval_seconds") or 0.5),
            )
            final_task = _extract_action_task(final_payload)
            action_status = str(final_task.get("status") or "")
            if action_status == "complete":
                task.status = "completed"
            elif action_status == "expired":
                task.status = "expired"
                task.error = str(final_task.get("error") or "action task expired")
            elif action_status == "rejected":
                task.status = "rejected"
                task.error = str(final_task.get("error") or "action task rejected")
            else:
                task.status = "failed"
                task.error = str(final_task.get("error") or f"action task ended with {action_status}")
            envelope.vehicle_execution = _vehicle_execution_from_task(final_task)
            execution["action_task"] = final_payload
        except (TimeoutError, RuntimeError, urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            task.status = "failed"
            task.error = str(exc)
    else:
        task.status = "completed"
    task.result = execution
    if task.route == "action" and envelope.dispatch_mode == "cloud_queue":
        result = _action_task_result(task, execution.get("action_task") if isinstance(execution.get("action_task"), dict) else {}, task.status)
    else:
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
            task.result = {"dry_run": True, "skill_id": task.skill_id, "route": task.route, "settings_override": task.settings_override}
            results.append({"task_id": task.task_id, "skill_id": task.skill_id, "status": "dry_run", "result": task.result})
            continue
        result = dispatch_task(envelope, task, cloud_config=cloud_config, source=source, dispatch_mode=envelope.dispatch_mode)
        results.append(result)
        if task.skill_id == "emergency_stop":
            break
        if task.status != "completed" and task.route != "face":
            break
    envelope.dispatch_results = results
    envelope.t_dispatch_end = time.time()
    return envelope
