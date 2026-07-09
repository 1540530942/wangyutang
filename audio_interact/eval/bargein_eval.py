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
        after_ms = int(case.get("expected_interrupt_commit_after_ms", case.get("user_speech_end_ms", 0)))
        before_ms = int(case.get("expected_interrupt_commit_before_ms", after_ms + 500))
        evaluated.append(
            {
                "case_id": case.get("case_id"),
                "should_interrupt": should_interrupt,
                "detected": commit is not None,
                "commit_ms": commit_ms,
                "user_speech_end_ms": case.get("user_speech_end_ms"),
                "commit_delay_after_user_end_ms": commit_ms - int(case.get("user_speech_end_ms", 0)) if commit_ms is not None else None,
                "early_interrupt": commit_ms is not None and commit_ms < after_ms,
                "late_interrupt": commit_ms is not None and commit_ms > before_ms,
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


def _match_commit(case: dict[str, Any], commits: list[dict[str, Any]]) -> dict[str, Any] | None:
    tts_id = str(case.get("tts_id") or "")
    for event in commits:
        if tts_id and str(event.get("tts_id") or "") == tts_id:
            return event
    return commits[0] if commits else None

