from __future__ import annotations

from typing import Any


def evaluate_tts(tts_events: list[dict[str, Any]], tts_label: dict[str, Any], asr_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    labels = list(tts_label.get("tts_segments") or [])
    requests = [event for event in tts_events if event.get("type") == "tts.request"]
    stops = [event for event in tts_events if event.get("type") in {"tts.stop", "tts.play_end"}]
    interrupted = [event for event in stops if "barge" in str(event.get("reason") or event.get("stop_reason") or "")]
    leak_count = 0
    if asr_events:
        asr_text = "\n".join(str(event.get("text") or "") for event in asr_events if event.get("type") == "asr.final")
        for event in requests:
            text = str(event.get("text") or "")
            if text and text in asr_text:
                leak_count += 1
    return {
        "label_tts_segments": len(labels),
        "tts_request_count": len(requests),
        "tts_stop_count": len(stops),
        "tts_interrupted_count": len(interrupted),
        "tts_leak_to_asr_count": leak_count,
    }

