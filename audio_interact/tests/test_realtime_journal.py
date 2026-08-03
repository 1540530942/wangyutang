"""Real-time interaction event journal (account one) + barge-in + edge merge.

Covers the P0 guarantees from docs/full-link-trace-design.md:
- every event carries a stable, monotonic event_id;
- events are durable the instant they happen (crash before finalize keeps them);
- a live journal finalizes into a valid, replayable session package;
- barge-in commit + tts.cancel are recorded with a cause chain;
- edge telemetry merges idempotently (retries never double-count).
"""
from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# server reads its data dir from env at import time; point it somewhere writable.
_TMP_DATA = tempfile.mkdtemp(prefix="ai_journal_test_")
os.environ.setdefault("AUDIO_INTERACT_DATA_DIR", _TMP_DATA)
os.environ.setdefault("DATA_DIR", _TMP_DATA)

from runtime.audio_writer import pcm16_to_wav_bytes  # noqa: E402
from runtime.event_logger import read_jsonl  # noqa: E402
from runtime.id_generator import ordinal_id  # noqa: E402
from runtime.session_writer import (  # noqa: E402
    emit_bargein_commit,
    emit_tts_cancel,
    emit_turn_result,
    emit_vad_end,
    emit_vad_start,
    finalize_streaming_session,
    open_streaming_journal,
    seconds_to_ms,
)
from tools.validate_session import validate_session  # noqa: E402


def _pcm16(duration_ms: int, hz: int = 440, sample_rate: int = 16000) -> bytes:
    total = int(sample_rate * duration_ms / 1000)
    return b"".join(
        struct.pack("<h", int(1000 * math.sin(2 * math.pi * hz * i / sample_rate)))
        for i in range(total)
    )


def _drive_live_session(root: Path) -> object:
    """Emit a two-turn session with a barge-in exactly as the live server would."""
    journal = open_streaming_journal(
        root, session_id="pi-live-001", device_id="turbopi-01", sample_rate=16000
    )
    # turn 0: "往前走" — normal turn, TTS starts
    emit_vad_start(journal, segment_id=ordinal_id("seg", 0), start_ms=seconds_to_ms(0.1))
    emit_vad_end(journal, segment_id=ordinal_id("seg", 0), start_ms=100, end_ms=650, reason="silence")
    emit_turn_result(
        journal,
        {"vad_start_seconds": 0.1, "vad_end_seconds": 0.65, "text": "往前走",
         "wake_status": "awake", "skill_id": "move_forward", "status": "ok",
         "tts_text": "好的，我现在往前走", "asr_elapsed_ms": 420, "route_elapsed_ms": 300},
        segment_id=ordinal_id("seg", 0), turn_id=ordinal_id("turn", 0), emit_tts=False,
    )
    journal.emit("tts", type="tts.request", tts_id=ordinal_id("tts", 0), turn_id=ordinal_id("turn", 0))
    journal.emit("tts", type="tts.begin", tts_id=ordinal_id("tts", 0), turn_id=ordinal_id("turn", 0))
    # turn 1: user barges in with "停下" while tts_001 plays
    ss = emit_vad_start(journal, segment_id=ordinal_id("seg", 1), start_ms=2048, during_tts=True)
    commit = emit_bargein_commit(
        journal, ts_ms=2110, segment_id=ordinal_id("seg", 1),
        turn_id=ordinal_id("turn", 1), tts_id=ordinal_id("tts", 0), cause=ss["event_id"],
    )
    emit_tts_cancel(
        journal, ts_ms=2110, tts_id=ordinal_id("tts", 0),
        turn_id=ordinal_id("turn", 0), reason="barge_in", cause=ss["event_id"],
    )
    emit_vad_end(journal, segment_id=ordinal_id("seg", 1), start_ms=2048, end_ms=2688, reason="silence")
    emit_turn_result(
        journal,
        {"vad_start_seconds": 2.048, "vad_end_seconds": 2.688, "text": "停下",
         "wake_status": "awake", "skill_id": "stop", "status": "ok", "tts_text": ""},
        segment_id=ordinal_id("seg", 1), turn_id=ordinal_id("turn", 1), emit_tts=False,
    )
    return journal, commit


def test_events_have_monotonic_event_ids(tmp_path: Path) -> None:
    journal, _ = _drive_live_session(tmp_path)
    runtime = read_jsonl(journal.events_dir / "runtime_events.jsonl")
    ids = [e["event_id"] for e in runtime]
    assert all(i.startswith("evt_") for i in ids)
    assert ids == sorted(ids)                 # monotonic in emit order
    assert len(ids) == len(set(ids))          # unique


def test_events_durable_before_finalize(tmp_path: Path) -> None:
    """Simulate a crash: events were emitted but finalize() never ran."""
    journal, _ = _drive_live_session(tmp_path)
    # No finalize_streaming_session() call — mimic kill -9 mid-session.
    vad = read_jsonl(journal.events_dir / "vad_runtime.jsonl")
    bargein = read_jsonl(journal.events_dir / "bargein_runtime.jsonl")
    assert [e["type"] for e in vad].count("vad.speech_start") == 2
    assert any(e["type"] == "bargein.commit" for e in bargein)
    # The session.start marker is on disk too, so the partial session is replayable.
    runtime = read_jsonl(journal.events_dir / "runtime_events.jsonl")
    assert runtime[0]["type"] == "session.start"


def test_bargein_commit_has_cause_chain(tmp_path: Path) -> None:
    journal, commit = _drive_live_session(tmp_path)
    bargein = read_jsonl(journal.events_dir / "bargein_runtime.jsonl")
    tts = read_jsonl(journal.events_dir / "tts_runtime.jsonl")
    vad = read_jsonl(journal.events_dir / "vad_runtime.jsonl")
    speech_start = next(e for e in vad if e["type"] == "vad.speech_start" and e.get("during_tts"))
    commit_ev = next(e for e in bargein if e["type"] == "bargein.commit")
    cancel_ev = next(e for e in tts if e["type"] == "tts.cancel")
    # commit and cancel both point back at the speech_start that triggered them.
    assert commit_ev["cause"] == speech_start["event_id"]
    assert cancel_ev["cause"] == speech_start["event_id"]
    assert commit_ev["tts_id"] == "tts_001"


def test_finalize_produces_valid_session(tmp_path: Path) -> None:
    journal, _ = _drive_live_session(tmp_path)
    rel = finalize_streaming_session(
        journal, full_pcm=_pcm16(3000), tts_pcm=_pcm16(600, hz=660)
    )
    assert rel
    assert validate_session(journal.session_dir) == []
    assert (journal.session_dir / "audio" / "tts_ref_16k.wav").exists()
    manifest = (journal.session_dir / "manifest.json")
    assert manifest.exists()


def test_edge_events_merge_is_idempotent(tmp_path: Path) -> None:
    import server

    server.AUDIO_DATA_DIR = tmp_path
    journal, _ = _drive_live_session(tmp_path)
    finalize_streaming_session(journal, full_pcm=_pcm16(3000))

    edge_events = [
        {"event_id": "edge_pi-live-001_000001", "ts_ms": 3200, "type": "playback.play_start", "bytes": 4096},
        {"event_id": "edge_pi-live-001_000002", "ts_ms": 3290, "type": "playback.play_stop", "reason": "killed", "rc": -9},
        {"event_id": "edge_pi-live-001_000003", "ts_ms": 2100, "type": "bargein.tts_cancel_recv", "tts_id": "tts_001"},
    ]
    first = server.post_edge_events("pi-live-001", {"events": edge_events}, None)
    assert first["merged"] == 3 and first["skipped"] == 0

    # Re-upload (flaky uplink retry): nothing new is written.
    second = server.post_edge_events("pi-live-001", {"events": edge_events}, None)
    assert second["merged"] == 0 and second["skipped"] == 3

    runtime = read_jsonl(journal.events_dir / "runtime_events.jsonl")
    play_stops = [e for e in runtime if e.get("type") == "playback.play_stop"]
    assert len(play_stops) == 1                    # merged once, not twice
    assert play_stops[0]["source"] == "edge"
    bargein = read_jsonl(journal.events_dir / "bargein_runtime.jsonl")
    assert any(e["type"] == "bargein.tts_cancel_recv" for e in bargein)
