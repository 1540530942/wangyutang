from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from replay.audio_stream import stream_wav_chunks
from replay.tts_timeline import TTSState
from runtime.event_logger import read_jsonl


EVENT_FILES = [
    "runtime_events.jsonl",
    "vad_runtime.jsonl",
    "asr_runtime.jsonl",
    "tts_runtime.jsonl",
    "bargein_runtime.jsonl",
]


def replay_session(session_dir: Path, *, output_path: Path | None = None) -> list[dict[str, Any]]:
    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    chunk_ms = int(manifest.get("audio", {}).get("chunk_ms") or 32)
    audio_path = session_dir / "audio" / "mic_proc_16k.wav"
    events = _load_events(session_dir)
    chunks = stream_wav_chunks(audio_path, chunk_ms)
    tts_state = TTSState()
    outputs: list[dict[str, Any]] = []
    cursor = 0
    events.sort(key=lambda item: (int(item.get("ts_ms", 0)), str(item.get("type", ""))))

    for chunk in chunks:
        injected: list[dict[str, Any]] = []
        while cursor < len(events) and int(events[cursor].get("ts_ms", 0)) <= chunk.end_ms:
            event = events[cursor]
            cursor += 1
            if str(event.get("type", "")).startswith("tts."):
                tts_state.update(event)
            injected.append(event)
        outputs.append(
            {
                "ts_ms": chunk.start_ms,
                "type": "replay.chunk",
                "chunk_id": chunk.chunk_id,
                "start_ms": chunk.start_ms,
                "end_ms": chunk.end_ms,
                "bytes": len(chunk.pcm16),
                "tts_state": tts_state.snapshot(),
                "events": injected,
            }
        )

    if cursor < len(events):
        outputs.append(
            {
                "ts_ms": int(events[cursor].get("ts_ms", 0)),
                "type": "replay.trailing_events",
                "events": events[cursor:],
            }
        )

    target = output_path or (session_dir / "replay" / "replay_outputs.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for item in outputs:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return outputs


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    events_dir = session_dir / "events"
    seen: set[tuple[int, str, str, str]] = set()
    for filename in EVENT_FILES:
        for event in read_jsonl(events_dir / filename):
            key = (
                int(event.get("ts_ms", 0)),
                str(event.get("type", "")),
                str(event.get("segment_id", "")),
                str(event.get("turn_id", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    return events

