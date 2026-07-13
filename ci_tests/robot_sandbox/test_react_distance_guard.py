"""LLM ReAct guards for session-labeled movement distance turns."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ci_tests.golden_sessions import iter_golden_sessions
from robot_sandbox.harness.react_loop import decide_transcript


REPO_ROOT = Path(__file__).resolve().parents[2]
ROBOT_SANDBOX_DIR = REPO_ROOT / "robot_sandbox"
ROUTER_CONFIG = {
    "skill_catalog": str((REPO_ROOT / "action_move" / "skill_catalog.json").resolve()),
    "skill_registry": str((ROBOT_SANDBOX_DIR / "skills" / "registry.yaml").resolve()),
    "react_agent": {
        "mode": "llm",
        "max_steps": 8,
        "prompt_path": "agent/prompts/walle_system_prompt.md",
        "llm": {
            "endpoint": os.getenv(
                "ROBOT_SANDBOX_LLM_ENDPOINT",
                "https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat/completions",
            ),
            "model": os.getenv("ROBOT_SANDBOX_LLM_MODEL", "qwen3-32b"),
            "timeout_seconds": float(os.getenv("ROBOT_SANDBOX_LLM_TIMEOUT", "90")),
            "retries": int(os.getenv("ROBOT_SANDBOX_LLM_RETRIES", "1")),
        },
    },
}
CLOUD_CONFIG = {
    "sensor_server": "http://sensor.local",
    "camera_server": "http://camera.local",
}


def _live_llm_required() -> bool:
    flag = os.getenv("RUN_LLM_REACT_GUARD", "").lower() in {"1", "true", "yes"}
    return flag or os.getenv("CI", "").lower() in {"1", "true", "yes"}


def _distance_turns():
    turns = []
    for session in iter_golden_sessions(tier="smoke"):
        for turn in session.guard_turns("distance_param"):
            turns.append((session.session_id, turn))
    return turns


@pytest.mark.parametrize("session_id,turn", _distance_turns(), ids=lambda item: item if isinstance(item, str) else item["turn_id"])
def test_llm_react_preserves_cn_distance_param(session_id, turn):
    if not _live_llm_required():
        pytest.skip("set RUN_LLM_REACT_GUARD=1, or run under CI=true, to execute live LLM ReAct guard")

    expected_skill = turn["expected_skill_id"]
    expected_unit = float(turn["expected_settings"]["unit_distance_cm"])
    expected_distance = float(turn["expected_args"]["distance_cm"])

    with patch(
        "robot_sandbox.tools.observation_executor._get_json",
        return_value={
            "available": True,
            "front_distance_estimate_cm": 40,
            "confidence": 0.9,
            "reported_at": 9999999999,
        },
    ):
        envelope = decide_transcript(
            base_dir=ROBOT_SANDBOX_DIR,
            text=turn["expected_text"],
            router_config=ROUTER_CONFIG,
            cloud_config=CLOUD_CONFIG,
            dispatch_mode="dry_run",
            source="ci-robot-sandbox",
            device_id="ci-test",
        )

    assert not any(step.get("preflight") for step in envelope.react_turns), (
        f"{session_id}[{turn['turn_id']}] must exercise LLM ReAct, not exact preflight"
    )
    assert not envelope.errors, f"{session_id}[{turn['turn_id']}] ReAct errors: {envelope.errors}"

    calls = [call for call in envelope.validated_tool_calls if call.tool == turn.get("expected_tool", "dispatch_action")]
    assert calls, f"{session_id}[{turn['turn_id']}] no validated dispatch_action call"
    action_call = next((call for call in calls if call.args.get("skill_id") == expected_skill), calls[0])

    assert action_call.args.get("skill_id") == expected_skill
    assert float(action_call.args.get("distance_cm") or 0) == expected_distance
    assert envelope.tasks, f"{session_id}[{turn['turn_id']}] no task created"
    assert envelope.tasks[0].skill_id == expected_skill
    assert envelope.tasks[0].settings_override.get("unit_distance_cm") == expected_unit
    assert envelope.dispatch_results[0]["status"] == "dry_run"
    assert envelope.dispatch_results[0]["result"]["settings_override"]["unit_distance_cm"] == expected_unit


def _no_distance_turns():
    turns = []
    for session in iter_golden_sessions(tier="smoke"):
        for turn in session.guard_turns("no_distance_param"):
            turns.append((session.session_id, turn))
    return turns


@pytest.mark.parametrize("session_id,turn", _no_distance_turns(), ids=lambda item: item if isinstance(item, str) else item["turn_id"])
def test_llm_react_no_distance_for_non_movement_skill(session_id, turn):
    """Non-movement skills must NOT inject distance_cm or alter unit_distance_cm."""
    if not _live_llm_required():
        pytest.skip("set RUN_LLM_REACT_GUARD=1, or run under CI=true, to execute live LLM ReAct guard")

    with patch(
        "robot_sandbox.tools.observation_executor._get_json",
        return_value={
            "available": True,
            "front_distance_estimate_cm": 40,
            "confidence": 0.9,
            "reported_at": 9999999999,
        },
    ):
        envelope = decide_transcript(
            base_dir=ROBOT_SANDBOX_DIR,
            text=turn["expected_text"],
            router_config=ROUTER_CONFIG,
            cloud_config=CLOUD_CONFIG,
            dispatch_mode="dry_run",
            source="ci-robot-sandbox",
            device_id="ci-test",
        )

    assert not envelope.errors, f"{session_id}[{turn['turn_id']}] ReAct errors: {envelope.errors}"
    assert envelope.tasks, f"{session_id}[{turn['turn_id']}] no task created"
    assert envelope.tasks[0].skill_id == turn["expected_skill_id"]

    calls = [call for call in envelope.validated_tool_calls if call.tool == turn.get("expected_tool", "dispatch_action")]
    assert calls, f"{session_id}[{turn['turn_id']}] no validated dispatch_action call"
    action_call = calls[0]

    assert "distance_cm" not in action_call.args, (
        f"{session_id}[{turn['turn_id']}] non-movement skill must not inject distance_cm; "
        f"got args={action_call.args}"
    )
    unit_override = (envelope.tasks[0].settings_override or {}).get("unit_distance_cm")
    assert unit_override is None, (
        f"{session_id}[{turn['turn_id']}] non-movement skill must not change unit_distance_cm; "
        f"got unit_distance_cm={unit_override}"
    )
