from __future__ import annotations

from typing import Any


def evaluate_vad(vad_events: list[dict[str, Any]], vad_label: dict[str, Any]) -> dict[str, Any]:
    labels = list(vad_label.get("speech_segments") or [])
    preds = _pred_segments(vad_events)
    matched = 0
    start_errors: list[int] = []
    end_errors: list[int] = []
    for label in labels:
        best = _best_overlap(label, preds)
        if best is None:
            continue
        matched += 1
        start_errors.append(int(best.get("start_ms", 0)) - int(label.get("start_ms", 0)))
        end_errors.append(int(best.get("end_ms", 0)) - int(label.get("end_ms", 0)))
    false_alarm = max(0, len(preds) - matched)
    return {
        "label_segments": len(labels),
        "pred_segments": len(preds),
        "speech_recall": matched / len(labels) if labels else None,
        "false_alarm_count": false_alarm,
        "start_error_ms_p95": _percentile_abs(start_errors, 0.95),
        "end_error_ms_p95": _percentile_abs(end_errors, 0.95),
        "early_cut_rate": sum(1 for value in end_errors if value < 0) / len(end_errors) if end_errors else None,
    }


def _pred_segments(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # fixed_window 是定长上传的占位段,不是真实 VAD 预测,不参与评测
    segments = [
        event
        for event in events
        if event.get("type") == "vad.segment" and event.get("source") != "fixed_window"
    ]
    if segments:
        return segments
    starts: dict[str, int] = {}
    preds: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("type")
        segment_id = str(event.get("segment_id") or f"seg_{len(starts) + 1:03d}")
        if event_type == "vad.speech_start":
            starts[segment_id] = int(event.get("ts_ms", 0))
        elif event_type == "vad.speech_end":
            start_ms = int(event.get("start_ms", starts.get(segment_id, 0)))
            end_ms = int(event.get("end_ms", event.get("ts_ms", 0)))
            preds.append({"segment_id": segment_id, "start_ms": start_ms, "end_ms": end_ms})
    return preds


def _best_overlap(label: dict[str, Any], preds: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    label_start = int(label.get("start_ms", 0))
    label_end = int(label.get("end_ms", 0))
    for pred in preds:
        pred_start = int(pred.get("start_ms", 0))
        pred_end = int(pred.get("end_ms", 0))
        overlap = max(0, min(label_end, pred_end) - max(label_start, pred_start))
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, pred)
    return best[1] if best else None


def _percentile_abs(values: list[int], ratio: float) -> int | None:
    if not values:
        return None
    ordered = sorted(abs(value) for value in values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio)))
    return ordered[index]

