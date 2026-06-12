from __future__ import annotations

from typing import Any

from audio_recognition.core.contracts import PlannedTask
from audio_recognition.skills.catalog_loader import create_action_task
from audio_recognition.skills.face_router import create_face_task


def execute_planned_task(
    plan: PlannedTask | None,
    text: str,
    cloud_config: dict[str, Any] | None,
    source: str,
) -> dict[str, Any]:
    cloud_config = cloud_config or {}
    action_task: dict[str, Any] | None = None
    face_task: dict[str, Any] | None = None
    action_error = ""
    face_error = ""
    if not plan or not plan.active:
        return {
            "action_task": action_task,
            "face_task": face_task,
            "action_error": action_error,
            "face_error": face_error,
        }

    if plan.route == "face":
        if not bool(cloud_config.get("face_enabled", True)):
            face_error = "face routing disabled"
        else:
            try:
                face_task = create_face_task(
                    str(cloud_config.get("face_server") or ""),
                    plan.skill_id,
                    text,
                    source=source,
                )
            except Exception as exc:  # noqa: BLE001
                face_error = str(exc)
    elif plan.route == "action":
        if not bool(cloud_config.get("action_enabled", True)):
            action_error = "action routing disabled"
        else:
            try:
                action_task = create_action_task(
                    str(cloud_config.get("action_server") or ""),
                    plan.skill_id,
                    source=source,
                )
            except Exception as exc:  # noqa: BLE001
                action_error = str(exc)
    return {
        "action_task": action_task,
        "face_task": face_task,
        "action_error": action_error,
        "face_error": face_error,
    }
