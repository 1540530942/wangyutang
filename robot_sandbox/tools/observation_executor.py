from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from robot_sandbox.core.envelope import DecisionEnvelope, ToolCall


OBSERVATION_TOOLS = {"camera_snapshot", "front_distance", "get_robot_state", "ask_confirmation", "inspect_scene"}


class _ObservationToolError(RuntimeError):
    def __init__(self, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.data = data or {}


def _get_json(url: str, timeout: float = 5) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_bytes(url: str, timeout: float = 10) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read(), response.headers.get("content-type") or "image/jpeg"


def _post_json(url: str, payload: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_front_distance(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    now = time.time()
    data = raw.get("sonar") if isinstance(raw.get("sonar"), dict) else raw

    if "front_distance_estimate_cm" in data:
        distance = data.get("front_distance_estimate_cm")
        available = bool(data.get("available")) and distance is not None
        confidence = float(data.get("confidence") or (0.9 if available else 0.0))
    else:
        distance = data.get("distance_cm")
        try:
            distance = float(distance)
        except (TypeError, ValueError):
            distance = None
        available = bool(data.get("ok")) and distance is not None and distance > 0
        confidence = 0.9 if available else 0.0

    explicit_age = data.get("age_seconds")
    reported_at = data.get("reported_at") or data.get("sampled_at")
    if reported_at is None and explicit_age is not None:
        try:
            reported_at = now - max(0.0, float(explicit_age))
        except (TypeError, ValueError):
            reported_at = now
    reported_at = reported_at or now
    try:
        age_seconds = max(0.0, now - float(reported_at))
    except (TypeError, ValueError):
        age_seconds = float(explicit_age or 0.0)

    return {
        "available": available,
        "front_distance_estimate_cm": float(distance) if available and distance is not None else None,
        "confidence": confidence,
        "source": str(data.get("source") or source),
        "sampled_at": float(data.get("sampled_at") or reported_at or now),
        "reported_at": float(reported_at or now),
        "age_seconds": age_seconds,
        "raw": data.get("raw", raw),
    }


def _extract_action_task(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    task = payload.get("task")
    return task if isinstance(task, dict) else payload


def _parse_front_distance_task(task: dict[str, Any]) -> dict[str, Any]:
    output = str(task.get("output") or "")
    match = re.search(r"front_distance_estimate_cm=([0-9.]+)", output)
    if not match:
        raise RuntimeError(str(task.get("error") or "front_distance task did not report a distance"))
    raw_match = re.search(r"raw_mm_samples=([0-9,]+)", output)
    confidence_match = re.search(r"confidence=([0-9.]+)", output)
    reported_at = float(task.get("updated_at") or task.get("completed_at") or time.time())
    raw: dict[str, Any] = {
        "available": True,
        "front_distance_estimate_cm": float(match.group(1)),
        "confidence": float(confidence_match.group(1)) if confidence_match else 0.9,
        "source": "action-move-front-distance-task",
        "sampled_at": reported_at,
        "reported_at": reported_at,
        "raw": raw_match.group(1) if raw_match else output[-600:],
    }
    return _normalize_front_distance(raw, source="action-move-front-distance-task")


def _read_front_distance_task(action_server: str, cloud_config: dict[str, Any]) -> dict[str, Any]:
    created = _post_json(
        f"{action_server}/api/tasks",
        {"action": "front_distance", "source": "robot_sandbox", "ttl_seconds": 15},
        timeout=8,
    )
    task = _extract_action_task(created)
    task_id = str(task.get("id") or task.get("task_id") or "")
    if not task_id:
        raise RuntimeError("front_distance task was not created")

    timeout_seconds = float(cloud_config.get("front_distance_wait_timeout_seconds") or 12.0)
    poll_interval = float(cloud_config.get("front_distance_poll_interval_seconds") or 0.2)
    deadline = time.monotonic() + max(timeout_seconds, 0.5)
    terminal = {"complete", "failed", "rejected", "expired"}
    last_task = task
    while True:
        payload = _get_json(f"{action_server}/api/tasks/{task_id}", timeout=5)
        last_task = _extract_action_task(payload)
        status = str(last_task.get("status") or "")
        if status in terminal:
            if status == "complete":
                return _parse_front_distance_task(last_task)
            raise RuntimeError(str(last_task.get("error") or f"front_distance task ended with {status}"))
        if time.monotonic() >= deadline:
            raise TimeoutError(f"front_distance task {task_id} did not complete within {timeout_seconds:.1f}s")
        time.sleep(max(poll_interval, 0.05))


def _read_front_distance(cloud_config: dict[str, Any], *, allow_action_task: bool) -> dict[str, Any]:
    candidates: list[tuple[str, str]] = []
    action_server = str(cloud_config.get("action_server") or "").rstrip("/")
    sensor_server = str(cloud_config.get("sensor_server") or cloud_config.get("camera_server") or "").rstrip("/")

    if action_server:
        candidates.append((f"{action_server}/api/sonar", "action-server-on-demand"))
        if allow_action_task:
            candidates.append(("action-task://front_distance", "action-move-front-distance-task"))
    if sensor_server and sensor_server != action_server:
        candidates.append((f"{sensor_server}/api/sonar", "camera-snapshot-cache"))

    if not candidates:
        raise RuntimeError("action_server, sensor_server, or camera_server is required for front_distance")

    errors: list[str] = []
    for url, source in candidates:
        try:
            if url == "action-task://front_distance":
                return _read_front_distance_task(action_server, cloud_config)
            return _normalize_front_distance(_get_json(url, timeout=8), source=source)
        except Exception as exc:  # noqa: BLE001 - try the next configured sensor source.
            errors.append(f"{url}: {exc}")
    raise RuntimeError("; ".join(errors))


def _call_vision_analyze(
    *,
    analyze_url: str,
    image_b64: str,
    question: str,
    model: str = "",
    timeout: float = 60,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"image_base64": image_b64, "question": question}
    if model:
        payload["model"] = model
    raw = _post_json(analyze_url, payload, timeout=timeout)
    return {"answer": str(raw.get("text") or "").strip(), "raw": raw}


def _capture_kind(task: dict[str, Any], args: dict[str, Any]) -> str:
    kind = str(task.get("kind") or args.get("kind") or "camera").strip().lower()
    mode = str(args.get("mode") or "").strip().lower()
    if mode == "face":
        return "face"
    if mode in {"screenshot", "inspect"}:
        return "screen"
    return kind if kind in {"camera", "screen", "face"} else "camera"


def _wait_for_latest_frame(camera_server: str, task: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    requested_at = float(task.get("requested_at") or time.time())
    kind = _capture_kind(task, args)
    timeout_seconds = max(0.2, float(args.get("timeout_ms") or 5000) / 1000.0)
    deadline = time.monotonic() + timeout_seconds
    last_latest: dict[str, Any] = {}
    last_control: dict[str, Any] = {}
    while True:
        last_latest = _get_json(f"{camera_server}/api/latest?kind={kind}")
        control_task: dict[str, Any] = {}
        try:
            last_control = _get_json(f"{camera_server}/api/control?kind={kind}")
            control_task = last_control.get("task") if isinstance(last_control.get("task"), dict) else {}
        except urllib.error.HTTPError:
            raise
        except Exception:
            last_control = {}
        latest_task_id = str(last_latest.get("task_id") or "")
        latest_updated_at = float(last_latest.get("updated_at") or 0.0)
        if latest_task_id == task_id or latest_updated_at > requested_at:
            result = {
                "capture_task": task,
                "latest": last_latest,
                "control": last_control,
                **last_latest,
            }
            capture_error = str(last_latest.get("capture_error") or control_task.get("capture_error") or "")
            control_status = str(control_task.get("status") or "")
            if capture_error or control_status in {"failed", "expired", "stopped"}:
                detail = capture_error or f"capture task ended with {control_status}"
                raise _ObservationToolError(detail, data=result)
            return result
        control_status = str(control_task.get("status") or "")
        if control_status in {"failed", "expired", "stopped"}:
            raise _ObservationToolError(
                f"capture task ended with {control_status}",
                data={"capture_task": task, "latest": last_latest, "control": last_control},
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"camera snapshot {task_id} did not produce a fresh latest frame within {timeout_seconds:.1f}s")
        time.sleep(0.2)


def execute_observation_tool(envelope: DecisionEnvelope, call: ToolCall, cloud_config: dict[str, Any] | None) -> dict[str, Any]:
    cloud_config = cloud_config or {}
    started = time.time()
    observation: dict[str, Any] = {
        "tool": call.tool,
        "call_id": call.call_id,
        "status": "completed",
        "data": {},
        "error": "",
        "t_start": started,
    }
    try:
        if call.tool == "get_robot_state":
            action_server = str(cloud_config.get("local_action_server") or cloud_config.get("action_server") or "").rstrip("/")
            if action_server:
                observation["data"] = _get_json(f"{action_server}/health")
            else:
                observation["data"] = {"status": "unknown", "reason": "action_server_not_configured"}
        elif call.tool == "camera_snapshot":
            camera_server = str(cloud_config.get("camera_server") or "").rstrip("/")
            if not camera_server:
                raise RuntimeError("camera_server is required")
            capture_payload = _post_json(f"{camera_server}/api/capture", dict(call.args or {}))
            task = capture_payload.get("task") if isinstance(capture_payload.get("task"), dict) else {}
            observation["data"] = _wait_for_latest_frame(camera_server, task, dict(call.args or {})) if task.get("id") else capture_payload
        elif call.tool == "front_distance":
            observation["data"] = _read_front_distance(cloud_config, allow_action_task=envelope.dispatch_mode != "dry_run")
        elif call.tool == "ask_confirmation":
            observation["status"] = "pending"
            timeout_ms = int(call.args.get("timeout_ms") or int(call.args.get("timeout_s") or 10) * 1000)
            observation["data"] = {
                "question": str(call.args.get("question") or ""),
                "timeout_ms": timeout_ms,
                "timeout_s": max(1, timeout_ms // 1000),
                "confirmed": False,
                "answer": None,
                "timeout": False,
            }
        elif call.tool == "inspect_scene":
            camera_server = str(cloud_config.get("camera_server") or "").rstrip("/")
            if not camera_server:
                raise RuntimeError("camera_server is required for inspect_scene")
            analyze_url = str(
                cloud_config.get("vision_analyze_url")
                or os.environ.get("AUDIO_VISION_ANALYZE_URL")
                or ""
            )
            if not analyze_url:
                raise RuntimeError("vision_analyze_url is required; set AUDIO_VISION_ANALYZE_URL")
            vision_model = str(
                cloud_config.get("vision_llm_model")
                or os.environ.get("AUDIO_VISION_LLM_MODEL")
                or ""
            )
            frame_meta: dict[str, Any] = {}
            try:
                capture_payload = _post_json(f"{camera_server}/api/capture", {"mode": "single"})
                task = capture_payload.get("task") if isinstance(capture_payload.get("task"), dict) else {}
                if task.get("id"):
                    frame_meta = _wait_for_latest_frame(camera_server, task, {"timeout_ms": 8000})
                else:
                    frame_meta = capture_payload
            except TimeoutError:
                frame_meta = {"warning": "fresh_frame_timeout_using_cached"}
            img_bytes, _ = _get_bytes(f"{camera_server}/api/latest.jpg")
            img_b64 = base64.b64encode(img_bytes).decode()
            question = str(call.args.get("question") or envelope.transcript or "图片里有什么？")
            vision_result = _call_vision_analyze(
                analyze_url=analyze_url,
                image_b64=img_b64,
                question=question,
                model=vision_model,
            )
            observation["data"] = {
                "question": question,
                "answer": vision_result.get("answer", ""),
                "vision_model": vision_model,
                "frame": {k: v for k, v in frame_meta.items() if k not in {"latest", "capture_task", "control"}},
            }
        else:
            raise RuntimeError(f"unsupported observation tool: {call.tool}")
    except _ObservationToolError as exc:
        observation.update({"status": "failed", "error": str(exc), "data": exc.data})
    except Exception as exc:  # noqa: BLE001
        observation.update({"status": "failed", "error": str(exc)})
    observation["latency_ms"] = int((time.time() - started) * 1000)
    envelope.observations.append(observation)
    return observation
