"""Generate the synthetic full-duplex barge-in golden session.

Per docs/audio-interact-fullduplex-golden-spec.md this is SYNTHETIC data: it is
sufficient to validate the parsers, timeline alignment, barge-in scoring, and the
dashboard extractor — NOT to prove WonderEchoPro hardware behavior. The events are
produced through the real journal code path (runtime.session_writer) so the golden
stays faithful to what the live server writes.

Run from the audio_interact dir:  python tests/golden/make_fullduplex_golden.py
"""
from __future__ import annotations

import json
import math
import shutil
import struct
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

from runtime.event_logger import EventLogger  # noqa: E402
from runtime.id_generator import ordinal_id  # noqa: E402
from runtime.session_writer import (  # noqa: E402
    emit_bargein_commit, emit_tts_cancel, emit_turn_result, emit_vad_end,
    emit_vad_start, finalize_streaming_session, open_streaming_journal,
)

SESSION_ID = "fdx-bargein-synthetic-001"
SR = 16000

# One shared ground-truth timeline (ms), mirrored by both events and labels.
T = {
    "u1_start": 1200, "u1_end": 2600,          # turn_001 "往前走一点"
    "tts1_req": 3000, "tts1_begin": 3200, "tts1_first": 3400,
    "edge_play1_start": 3120,
    "barge_speech": 5100,                       # user "停下" onset, during TTS
    "barge_commit": 5287, "barge_cancel": 5290,
    "edge_cancel_recv": 5300, "edge_play1_stop": 5388,   # tail = 88ms
    "u2_end": 6500,                             # "停下" speech end
    "tts2_req": 6900, "tts2_begin": 7050, "tts2_done": 8000,
    "edge_play2_start": 7020, "edge_play2_stop": 8010,   # echo-only, natural end
    "session_end": 9000,
}


def _pcm(ms: int, hz: int = 320) -> bytes:
    return b"".join(
        struct.pack("<h", int(2200 * math.sin(2 * math.pi * hz * i / SR)))
        for i in range(int(SR * ms / 1000))
    )


def build(root: Path) -> Path:
    j = open_streaming_journal(root, session_id=SESSION_ID, device_id="turbopi-01", sample_rate=SR)

    # turn_001 — normal turn, TTS starts then gets interrupted
    emit_vad_start(j, segment_id=ordinal_id("seg", 0), start_ms=T["u1_start"], confidence=0.94)
    emit_vad_end(j, segment_id=ordinal_id("seg", 0), start_ms=T["u1_start"], end_ms=T["u1_end"], reason="silence")
    emit_turn_result(j, {
        "vad_start_seconds": T["u1_start"] / 1000, "vad_end_seconds": T["u1_end"] / 1000,
        "text": "往前走一点", "wake_status": "awake", "skill_id": "move_forward", "status": "ok",
        "tts_text": "好的，我现在往前走", "asr_elapsed_ms": 420, "route_elapsed_ms": 640,
        "envelope_id": "env_fdx_a1b2",
    }, segment_id=ordinal_id("seg", 0), turn_id=ordinal_id("turn", 0), emit_tts=False)
    j.emit("tts", ts_ms=T["tts1_req"], type="tts.request", tts_id="tts_001", turn_id="turn_001", text="好的，我现在往前走")
    j.emit("tts", ts_ms=T["tts1_begin"], type="tts.begin", tts_id="tts_001", turn_id="turn_001")
    j.emit("tts", ts_ms=T["tts1_first"], type="tts.first_chunk", tts_id="tts_001", turn_id="turn_001", tts_first_ms=200)

    # barge-in: user "停下" over TTS → commit + cancel (server clock)
    # G8 clock sync: edge timestamps in this synthetic pack are session-aligned,
    # so the measured offset is 0 (rtt is still realistic).
    j.emit("runtime", ts_ms=500, type="clock.sync", offset_ms=0, rtt_ms=42, probes=5, method="ws_probe_median")

    ss = emit_vad_start(j, segment_id=ordinal_id("seg", 1), start_ms=T["barge_speech"], during_tts=True, confidence=0.9)
    emit_bargein_commit(j, ts_ms=T["barge_commit"], segment_id=ordinal_id("seg", 1),
                        turn_id="turn_002", tts_id="tts_001", cause=ss["event_id"])
    emit_tts_cancel(j, ts_ms=T["barge_cancel"], tts_id="tts_001", turn_id="turn_001",
                    reason="barge_in", cause=ss["event_id"])
    emit_vad_end(j, segment_id=ordinal_id("seg", 1), start_ms=T["barge_speech"], end_ms=T["u2_end"], reason="silence")
    emit_turn_result(j, {
        "vad_start_seconds": T["barge_speech"] / 1000, "vad_end_seconds": T["u2_end"] / 1000,
        "text": "停下", "wake_status": "awake", "skill_id": "stop", "status": "ok",
        "tts_text": "好的，停下", "asr_elapsed_ms": 380, "route_elapsed_ms": 210,
        "envelope_id": "env_fdx_c3d4",
    }, segment_id=ordinal_id("seg", 1), turn_id="turn_002", emit_tts=False)

    # tts_002 — reply plays to completion with NO user speech (echo-only negative case)
    j.emit("tts", ts_ms=T["tts2_req"], type="tts.request", tts_id="tts_002", turn_id="turn_002", text="好的，停下")
    j.emit("tts", ts_ms=T["tts2_begin"], type="tts.begin", tts_id="tts_002", turn_id="turn_002")
    j.emit("tts", ts_ms=T["tts2_done"], type="tts.done", tts_id="tts_002", turn_id="turn_002", tts_elapsed_ms=280)

    finalize_streaming_session(j, full_pcm=_pcm(T["session_end"]), tts_pcm=_pcm(600, hz=660))

    # Edge telemetry (Pi clock), appended exactly as POST /edge-events would merge it.
    edge = [
        ("runtime", {"ts_ms": T["edge_play1_start"], "type": "playback.play_start", "bytes": 40960}),
        ("bargein", {"ts_ms": T["edge_cancel_recv"], "type": "bargein.tts_cancel_recv", "tts_id": "tts_001"}),
        ("runtime", {"ts_ms": T["edge_play1_stop"], "type": "playback.play_stop", "reason": "killed", "rc": -9}),
        ("runtime", {"ts_ms": T["edge_play2_start"], "type": "playback.play_start", "bytes": 20480}),
        ("runtime", {"ts_ms": T["edge_play2_stop"], "type": "playback.play_stop", "reason": "ended", "rc": 0}),
    ]
    events_dir = j.session_dir / "events"
    runtime_log = EventLogger(events_dir / "runtime_events.jsonl")
    for idx, (cat, ev) in enumerate(edge):
        payload = {**ev, "session_id": SESSION_ID, "source": "edge",
                   "event_id": f"edge_{SESSION_ID}_{idx + 1:06d}"}
        if cat != "runtime":
            EventLogger(events_dir / f"{cat}_runtime.jsonl").emit(**payload)
        runtime_log.emit(**payload)

    _write_labels(j.session_dir)
    _write_manifest(j.session_dir)

    # Flatten to sessions/<id>/ (drop the date dir) to match the committed golden
    # convention and give this fixture a stable, reproducible path.
    stable = root / "sessions" / SESSION_ID
    if stable.exists():
        shutil.rmtree(stable)
    shutil.move(str(j.session_dir), str(stable))
    day_dir = j.session_dir.parent
    if day_dir.exists() and not any(day_dir.iterdir()):
        day_dir.rmdir()
    return stable


def _write_labels(session_dir: Path) -> None:
    labels = session_dir / "labels"
    (labels / "vad_label.json").write_text(json.dumps({
        "session_id": SESSION_ID,
        "speech_segments": [
            {"label_segment_id": "gt_seg_001", "speaker": "user", "start_ms": T["u1_start"], "end_ms": T["u1_end"], "text": "往前走一点", "turn_id": "turn_001"},
            {"label_segment_id": "gt_seg_002", "speaker": "user", "start_ms": T["barge_speech"], "end_ms": T["u2_end"], "text": "停下", "turn_id": "turn_002", "overlap_tts_id": "tts_001"},
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (labels / "asr_label.json").write_text(json.dumps({
        "session_id": SESSION_ID,
        "turns": [
            {"turn_id": "turn_001", "start_ms": T["u1_start"], "end_ms": T["u1_end"], "raw_text": "往前走一点", "normalized_text": "往前走一点"},
            {"turn_id": "turn_002", "start_ms": T["barge_speech"], "end_ms": T["u2_end"], "raw_text": "停下", "normalized_text": "停下"},
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (labels / "tts_label.json").write_text(json.dumps({
        "session_id": SESSION_ID,
        "tts_segments": [
            {"tts_id": "tts_001", "turn_id": "turn_001", "text": "好的，我现在往前走", "play_start_ms": T["edge_play1_start"], "expected_play_end_ms": 6200, "actual_stop_ms": T["edge_play1_stop"], "interrupted": True, "stop_reason": "barge_in"},
            {"tts_id": "tts_002", "turn_id": "turn_002", "text": "好的，停下", "play_start_ms": T["edge_play2_start"], "expected_play_end_ms": T["edge_play2_stop"], "actual_stop_ms": T["edge_play2_stop"], "interrupted": False},
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (labels / "bargein_label.json").write_text(json.dumps({
        "session_id": SESSION_ID,
        "bargein_cases": [
            {
                "case_id": "bargein_middle_001", "tts_id": "tts_001", "user_turn_id": "turn_002",
                "user_speech_start_ms": T["barge_speech"], "user_speech_end_ms": T["u2_end"],
                "user_text": "停下", "should_interrupt": True,
                "expected_interrupt_commit_after_ms": T["barge_speech"],
                "expected_interrupt_commit_before_ms": T["barge_speech"] + 500,
                "expected_post_cancel_tail_ms_max": 250,
            },
            {
                "case_id": "echo_only_001", "tts_id": "tts_002", "user_turn_id": None,
                "user_speech_start_ms": None, "user_speech_end_ms": None,
                "should_interrupt": False,
            },
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_manifest(session_dir: Path) -> None:
    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest.update({
        "case_id": "wonderecho_fullduplex_bargein_synthetic_001",
        "label": "全双工打断（合成金标）",
        "mode": "full_duplex",
        "source": "synthetic",
        "tiers": ["offline"],
        "guards": ["vad_asr", "full_duplex", "bargein", "echo_false_positive", "aec_residual"],
        "full_duplex": {"hardware_validated": False, "synthesis": "runtime.session_writer emit_* + edge merge"},
        "labels": {
            "vad": "labels/vad_label.json", "asr": "labels/asr_label.json",
            "tts": "labels/tts_label.json", "bargein": "labels/bargein_label.json",
        },
    })
    (session_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    out = build(Path(__file__).resolve().parents[1] / "golden")
    print(f"golden session written: {out}")
