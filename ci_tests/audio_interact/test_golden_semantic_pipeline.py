"""Cross-module semantic guard for golden sessions.

This test replays full session audio through audio_interact with route disabled,
then feeds routed turns into robot_sandbox LLM ReAct in dry-run mode. It proves
the full semantic path without creating real action tasks.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ci_tests.golden_sessions import iter_golden_sessions, strip_text
from ci_tests.session_replay import replay_session_audio
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


def _live_llm_required() -> bool:
    flag = os.getenv("RUN_LLM_REACT_GUARD", "").lower() in {"1", "true", "yes"}
    return flag or os.getenv("CI", "").lower() in {"1", "true", "yes"}


@pytest.fixture(scope="module")
def replayed_sessions():
    if not _live_llm_required():
        pytest.skip("set RUN_LLM_REACT_GUARD=1, or run under CI=true, to execute semantic pipeline guard")
    try:
        import websockets  # noqa: F401
    except ImportError:
        pytest.skip("websockets library not installed")

    from ci_tests.conftest import WS_URL

    results = {}
    for session in iter_golden_sessions(tier="smoke"):
        results[session.session_id] = (session, asyncio.run(replay_session_audio(WS_URL, session, route=False)))
    return results


def test_audio_to_react_distance_pipeline(replayed_sessions):
    for session_id, (session, actual_turns) in replayed_sessions.items():
        for expected in session.guard_turns("distance_param"):
            idx = int(expected["index"])
            assert idx < len(actual_turns), f"{session_id}[{idx}] missing replayed turn"
            actual_text = actual_turns[idx].get("text", "")
            assert strip_text(actual_text) == strip_text(expected["expected_text"])
            assert actual_turns[idx].get("wake_status") == expected["expected_wake_status"]

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
                    text=actual_text,
                    router_config=ROUTER_CONFIG,
                    cloud_config={"sensor_server": "http://sensor.local"},
                    dispatch_mode="dry_run",
                    source="ci-audio-interact",
                    device_id="ci-test",
                )

            expected_unit = float(expected["expected_settings"]["unit_distance_cm"])
            assert not any(step.get("preflight") for step in envelope.react_turns)
            assert envelope.tasks[0].skill_id == expected["expected_skill_id"]
            assert envelope.tasks[0].settings_override.get("unit_distance_cm") == expected_unit
            assert envelope.dispatch_results[0]["status"] == "dry_run"
