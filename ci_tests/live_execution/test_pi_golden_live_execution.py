"""Live execution verification for the Pi ALSA golden session.

This test intentionally creates real action_move tasks. It is not part of the
normal CI gate and must be enabled with explicit environment variables.
"""
from __future__ import annotations

import asyncio
import os
import time
import urllib.request
import json
from typing import Any

import pytest

from ci_tests.golden_sessions import GoldenSession, iter_golden_sessions, strip_text
from ci_tests.session_replay import replay_session_audio


CASE_ID = "pi_alsa_wake_distance_001"
TARGET_DEVICE_ID = "turbopi-01"
TERMINAL_STATUSES = {"complete", "failed", "rejected", "expired"}


def _live_enabled() -> bool:
    return os.getenv("RUN_LIVE_ROBOT_EXECUTION", "").lower() in {"1", "true", "yes"}


def _confirmation_ok() -> bool:
    return os.getenv("LIVE_ROBOT_CONFIRM_DEVICE", "") == TARGET_DEVICE_ID


def _action_server() -> str:
    return os.getenv("LIVE_ACTION_SERVER", "https://www.wangyutang.cn/action").rstrip("/")


def _get_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_task(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    task = payload.get("task")
    return task if isinstance(task, dict) else payload


def _task_id(action_task: dict[str, Any] | None) -> str:
    task = _extract_task(action_task)
    return str(task.get("id") or task.get("task_id") or "")


def _wait_action_task(task_id: str, *, timeout_seconds: float = 90.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _get_json(f"{_action_server()}/api/tasks/{task_id}", timeout=10)
        task = _extract_task(last)
        if str(task.get("status") or "") in TERMINAL_STATUSES:
            return task
        time.sleep(0.5)
    raise TimeoutError(f"action task {task_id} did not finish; last={last}")


def _pi_session() -> GoldenSession:
    matches = [s for s in iter_golden_sessions(tier="smoke") if s.manifest.get("case_id") == CASE_ID]
    if not matches:
        pytest.skip(f"golden session case not found: {CASE_ID}")
    return matches[0]


@pytest.mark.live_execution
def test_pi_golden_session_executes_on_real_robot():
    if not _live_enabled():
        pytest.skip("set RUN_LIVE_ROBOT_EXECUTION=1 to create real action_move tasks")
    if not _confirmation_ok():
        pytest.skip(f"set LIVE_ROBOT_CONFIRM_DEVICE={TARGET_DEVICE_ID} to confirm the target robot")

    try:
        import websockets  # noqa: F401
    except ImportError:
        pytest.skip("websockets library not installed")

    from ci_tests.conftest import WS_URL

    session = _pi_session()
    actual_turns = asyncio.run(
        replay_session_audio(
            WS_URL,
            session,
            route=True,
            device_id=f"live-{TARGET_DEVICE_ID}-{int(time.time())}",
            session_id_prefix="live",
        )
    )

    assert len(actual_turns) >= len(session.turns), (
        f"{session.session_id}: expected at least {len(session.turns)} turns, got {len(actual_turns)}"
    )

    completed_tasks: list[dict[str, Any]] = []
    for expected in session.turns:
        idx = int(expected["index"])
        actual = actual_turns[idx]
        assert strip_text(actual.get("text", "")) == strip_text(expected["expected_text"])
        assert actual.get("wake_status") == expected["expected_wake_status"]

        if expected.get("expected_route") != "action":
            assert not actual.get("action_task"), f"{expected['turn_id']} unexpectedly created an action task"
            continue

        assert actual.get("skill_id") == expected["expected_skill_id"]
        assert not actual.get("action_error"), f"{expected['turn_id']} action_error={actual.get('action_error')}"
        task_id = _task_id(actual.get("action_task"))
        assert task_id, f"{expected['turn_id']} did not return an action_task id"

        final_task = _wait_action_task(task_id)
        assert final_task.get("status") == "complete", f"{expected['turn_id']} final_task={final_task}"
        assert final_task.get("skill_id") == expected["expected_skill_id"]
        assert final_task.get("device_id") == TARGET_DEVICE_ID
        if "expected_settings" in expected:
            assert float(final_task.get("unit_distance_cm") or 0) == float(expected["expected_settings"]["unit_distance_cm"])
        completed_tasks.append(final_task)

    expected_action_count = len([t for t in session.turns if t.get("expected_route") == "action"])
    assert len(completed_tasks) == expected_action_count
