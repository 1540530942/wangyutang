from __future__ import annotations

from typing import Any


def evaluate_bargein(bargein_events: list[dict[str, Any]], bargein_label: dict[str, Any]) -> dict[str, Any]:
    cases = list(bargein_label.get("bargein_cases") or [])
    commits = [event for event in bargein_events if event.get("type") == "bargein.commit"]
    evaluated: list[dict[str, Any]] = []
    for case in cases:
        commit = _match_commit(case, commits)
        should_interrupt = bool(case.get("should_interrupt"))
        commit_ms = int(commit.get("ts_ms", 0)) if commit else None
        # Negative (echo-only) cases have no user speech, so window bounds are
        # absent — coerce to None rather than crashing (spec requires these cases).
        user_end = _int_or_none(case.get("user_speech_end_ms"))
        after_ms = _int_or_none(case.get("expected_interrupt_commit_after_ms"))
        if after_ms is None:
            after_ms = user_end
        before_ms = _int_or_none(case.get("expected_interrupt_commit_before_ms"))
        if before_ms is None and after_ms is not None:
            before_ms = after_ms + 500
        evaluated.append(
            {
                "case_id": case.get("case_id"),
                "should_interrupt": should_interrupt,
                "detected": commit is not None,
                "commit_ms": commit_ms,
                "user_speech_end_ms": user_end,
                "commit_delay_after_user_end_ms": commit_ms - user_end if (commit_ms is not None and user_end is not None) else None,
                "early_interrupt": commit_ms is not None and after_ms is not None and commit_ms < after_ms,
                "late_interrupt": commit_ms is not None and before_ms is not None and commit_ms > before_ms,
                "missed_bargein": should_interrupt and commit is None,
                "false_bargein": not should_interrupt and commit is not None,
            }
        )
    return {
        "case_count": len(cases),
        "commit_count": len(commits),
        "cases": evaluated,
        "bargein_detect_rate": sum(1 for item in evaluated if item["detected"]) / len(cases) if cases else None,
        "early_interrupt_rate": sum(1 for item in evaluated if item["early_interrupt"]) / len(cases) if cases else None,
        "late_interrupt_rate": sum(1 for item in evaluated if item["late_interrupt"]) / len(cases) if cases else None,
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _match_commit(case: dict[str, Any], commits: list[dict[str, Any]]) -> dict[str, Any] | None:
    tts_id = str(case.get("tts_id") or "")
    if tts_id:
        # A case scoped to a tts_id matches ONLY its own commit; no fallback, or an
        # echo-only negative case would falsely borrow another turn's commit.
        return next((e for e in commits if str(e.get("tts_id") or "") == tts_id), None)
    return commits[0] if commits else None

