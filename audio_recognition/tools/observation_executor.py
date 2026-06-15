from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from audio_recognition.core.envelope import DecisionEnvelope, ToolCall


OBSERVATION_TOOLS = {"camera_snapshot", "front_distance", "get_robot_state", "ask_confirmation"}


class _ObservationToolError(RuntimeError):
    def __init__(self, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.data = data or {}


def _get_json(url: str, timeout: float = 5) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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
            sensor_server = str(cloud_config.get("sensor_server") or cloud_config.get("camera_server") or "").rstrip("/")
            if not sensor_server:
                raise RuntimeError("sensor_server or camera_server is required")
            observation["data"] = _get_json(f"{sensor_server}/api/sonar")
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
        else:
            raise RuntimeError(f"unsupported observation tool: {call.tool}")
    except _ObservationToolError as exc:
        observation.update({"status": "failed", "error": str(exc), "data": exc.data})
    except Exception as exc:  # noqa: BLE001
        observation.update({"status": "failed", "error": str(exc)})
    observation["latency_ms"] = int((time.time() - started) * 1000)
    envelope.observations.append(observation)
    return observation
