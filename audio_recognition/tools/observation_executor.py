from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from audio_recognition.core.envelope import DecisionEnvelope, ToolCall


OBSERVATION_TOOLS = {"camera_snapshot", "front_distance", "get_robot_state", "ask_confirmation"}


def _get_json(url: str, timeout: float = 5) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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
            observation["data"] = _post_json(f"{camera_server}/api/capture", dict(call.args or {}))
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
    except Exception as exc:  # noqa: BLE001
        observation.update({"status": "failed", "error": str(exc)})
    observation["latency_ms"] = int((time.time() - started) * 1000)
    envelope.observations.append(observation)
    return observation
