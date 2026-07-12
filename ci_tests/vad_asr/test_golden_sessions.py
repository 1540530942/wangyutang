"""Golden session VAD/ASR checks.

This layer replays full session audio and asserts per-turn VAD, ASR, and wake
state labels. It intentionally does not route actions.
"""
from __future__ import annotations

import asyncio

import pytest

from ci_tests.golden_sessions import iter_golden_sessions, strip_text
from ci_tests.session_replay import replay_session_audio


VAD_TOL_MS = 400


@pytest.fixture(scope="module")
def golden_sessions():
    sessions = iter_golden_sessions(tier="smoke")
    if not sessions:
        pytest.skip("no smoke golden sessions found")
    return sessions


@pytest.fixture(scope="module")
def replayed_sessions(golden_sessions):
    try:
        import websockets  # noqa: F401
    except ImportError:
        pytest.skip("websockets library not installed")

    from ci_tests.conftest import WS_URL

    results = {}
    for session in golden_sessions:
        try:
            results[session.session_id] = asyncio.run(replay_session_audio(WS_URL, session, route=False))
        except Exception as exc:
            pytest.fail(f"WS replay failed for {session.session_id}: {exc}")
    return results


def test_session_turn_count(golden_sessions, replayed_sessions):
    for session in golden_sessions:
        expected = len(session.turns)
        actual = len(replayed_sessions[session.session_id])
        assert actual == expected, (
            f"{session.session_id}: expected {expected} turns, got {actual}; "
            f"texts={[item.get('text') for item in replayed_sessions[session.session_id]]}"
        )


def test_session_turn_vad_asr_wake(golden_sessions, replayed_sessions):
    for session in golden_sessions:
        actual_turns = replayed_sessions[session.session_id]
        for expected in session.turns:
            idx = int(expected["index"])
            assert idx < len(actual_turns), f"{session.session_id}[{idx}] missing actual turn"
            actual = actual_turns[idx]

            assert strip_text(actual.get("text", "")) == strip_text(expected["expected_text"]), (
                f"{session.session_id}[{idx}] ASR text mismatch: "
                f"expected={expected['expected_text']!r} actual={actual.get('text')!r}"
            )
            assert actual.get("wake_status") == expected["expected_wake_status"], (
                f"{session.session_id}[{idx}] wake_status mismatch: "
                f"expected={expected['expected_wake_status']!r} actual={actual.get('wake_status')!r}"
            )
            for key in ("vad_start_ms", "vad_end_ms"):
                got = actual.get(key)
                if got is None:
                    continue
                diff = abs(int(got) - int(expected[key]))
                assert diff <= VAD_TOL_MS, (
                    f"{session.session_id}[{idx}] {key} diff {diff}ms > {VAD_TOL_MS}ms; "
                    f"expected={expected[key]} actual={got}"
                )


def test_wake_state_progression(golden_sessions, replayed_sessions):
    for session in golden_sessions:
        actual_turns = replayed_sessions[session.session_id]
        seen_awake = False
        for expected, actual in zip(session.turns, actual_turns, strict=True):
            route = expected.get("expected_route")
            if route == "wake_word":
                seen_awake = True
                assert actual.get("wake_status") == "awake"
            elif route == "none":
                assert not seen_awake
                assert actual.get("wake_status") == "sleeping"
            elif route in {"action", "observation"}:
                assert seen_awake, f"{session.session_id}[{expected['index']}] routed before wake"
                assert actual.get("wake_status") == "awake"
