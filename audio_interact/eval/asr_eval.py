from __future__ import annotations

from typing import Any

from eval.text_metrics import cer


def evaluate_asr(asr_events: list[dict[str, Any]], asr_label: dict[str, Any]) -> dict[str, Any]:
    labels = list(asr_label.get("turns") or [])
    finals = [event for event in asr_events if event.get("type") == "asr.final"]
    final_by_turn = {str(event.get("turn_id") or f"turn_{index + 1:03d}"): event for index, event in enumerate(finals)}
    scores: list[float] = []
    empty = 0
    matched = 0
    for index, label in enumerate(labels):
        turn_id = str(label.get("turn_id") or f"turn_{index + 1:03d}")
        event = final_by_turn.get(turn_id)
        if event is None and index < len(finals):
            event = finals[index]
        if event is None:
            continue
        matched += 1
        hyp = str(event.get("text") or "")
        ref = str(label.get("normalized_text") or label.get("raw_text") or "")
        if not hyp:
            empty += 1
        scores.append(cer(ref, hyp))
    duplicate_turns = len(finals) - len({str(event.get("turn_id") or index) for index, event in enumerate(finals)})
    return {
        "label_turns": len(labels),
        "final_count": len(finals),
        "turn_recall": matched / len(labels) if labels else None,
        "cer": sum(scores) / len(scores) if scores else None,
        "empty_final_rate": empty / matched if matched else None,
        "duplicate_final_count": max(0, duplicate_turns),
    }

