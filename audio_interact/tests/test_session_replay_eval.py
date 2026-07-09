from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.report_generator import generate_report  # noqa: E402
from replay.replay_session import replay_session  # noqa: E402
from runtime.audio_writer import pcm16_to_wav_bytes  # noqa: E402
from runtime.session_writer import SessionWriter  # noqa: E402
from tools.run_regression import run_regression  # noqa: E402
from tools.validate_session import validate_session  # noqa: E402


def test_session_storage_replay_eval_loop(tmp_path: Path) -> None:
    writer = SessionWriter(
        tmp_path,
        session_id="synthetic_bargein",
        device_id="test-pi",
        sample_rate=16000,
        chunk_ms=40,
        source="unit_test",
    )
    audio = _tone_wav(duration_ms=2000)
    writer.write_audio_wav("mic_raw_16k.wav", audio)
    writer.write_audio_wav("mic_proc_16k.wav", audio)
    writer.write_audio_wav("tts_ref_16k.wav", _tone_wav(duration_ms=600, hz=660))

    writer.emit("vad", ts_ms=100, type="vad.speech_start", segment_id="seg_001", confidence=0.9)
    writer.emit("vad", ts_ms=650, type="vad.speech_end", segment_id="seg_001", start_ms=100, end_ms=650)
    writer.emit("asr", ts_ms=710, type="asr.final", segment_id="seg_001", turn_id="turn_001", audio_start_ms=100, audio_end_ms=650, text="往前走")
    writer.emit("tts", ts_ms=800, type="tts.request", tts_id="tts_001", text="好的，我现在往前走")
    writer.emit("tts", ts_ms=900, type="tts.play_start", tts_id="tts_001", text="好的，我现在往前走")
    writer.emit("bargein", ts_ms=1220, type="bargein.candidate_start", tts_id="tts_001", vad_segment_id="seg_002")
    writer.emit("vad", ts_ms=1200, type="vad.speech_start", segment_id="seg_002", confidence=0.88)
    writer.emit("vad", ts_ms=1500, type="vad.speech_end", segment_id="seg_002", start_ms=1200, end_ms=1500)
    writer.emit("bargein", ts_ms=1580, type="bargein.commit", tts_id="tts_001", vad_segment_id="seg_002")
    writer.emit("tts", ts_ms=1590, type="tts.stop", tts_id="tts_001", reason="barge_in_commit")
    writer.emit("asr", ts_ms=1620, type="asr.final", segment_id="seg_002", turn_id="turn_002", audio_start_ms=1200, audio_end_ms=1500, text="停下")
    writer.close(duration_ms=2000)

    _write_json(
        writer.labels_dir / "vad_label.json",
        {
            "session_id": writer.session_id,
            "speech_segments": [
                {"label_segment_id": "gt_seg_001", "speaker": "user", "start_ms": 100, "end_ms": 650, "text": "往前走", "turn_id": "turn_001"},
                {"label_segment_id": "gt_seg_002", "speaker": "user", "start_ms": 1200, "end_ms": 1500, "text": "停下", "turn_id": "turn_002", "overlap_tts_id": "tts_001"},
            ],
        },
    )
    _write_json(
        writer.labels_dir / "asr_label.json",
        {
            "session_id": writer.session_id,
            "turns": [
                {"turn_id": "turn_001", "start_ms": 100, "end_ms": 650, "raw_text": "往前走", "normalized_text": "往前走"},
                {"turn_id": "turn_002", "start_ms": 1200, "end_ms": 1500, "raw_text": "停下", "normalized_text": "停下"},
            ],
        },
    )
    _write_json(
        writer.labels_dir / "tts_label.json",
        {
            "session_id": writer.session_id,
            "tts_segments": [
                {"tts_id": "tts_001", "text": "好的，我现在往前走", "play_start_ms": 900, "expected_play_end_ms": 1700, "actual_stop_ms": 1590, "interrupted": True, "stop_reason": "barge_in_commit"}
            ],
        },
    )
    _write_json(
        writer.labels_dir / "bargein_label.json",
        {
            "session_id": writer.session_id,
            "bargein_cases": [
                {
                    "case_id": "bargein_001",
                    "tts_id": "tts_001",
                    "user_turn_id": "turn_002",
                    "user_speech_start_ms": 1200,
                    "user_speech_end_ms": 1500,
                    "user_text": "停下",
                    "should_interrupt": True,
                    "expected_interrupt_commit_after_ms": 1500,
                    "expected_interrupt_commit_before_ms": 2000,
                }
            ],
        },
    )

    assert validate_session(writer.session_dir) == []
    replay_outputs = replay_session(writer.session_dir)
    assert replay_outputs
    assert (writer.replay_dir / "replay_outputs.jsonl").exists()

    report = generate_report(writer.session_dir)
    assert report["vad_eval"]["speech_recall"] == 1.0
    assert report["asr_eval"]["cer"] == 0.0
    assert report["bargein_eval"]["cases"][0]["commit_delay_after_user_end_ms"] == 80
    assert not report["bargein_eval"]["cases"][0]["early_interrupt"]

    regression = run_regression(tmp_path / "sessions")
    assert regression["ok"] is True
    assert regression["count"] == 1


def _tone_wav(*, duration_ms: int, hz: int = 440, sample_rate: int = 16000) -> bytes:
    frames = []
    total = int(sample_rate * duration_ms / 1000)
    for i in range(total):
        value = int(1000 * math.sin(2 * math.pi * hz * i / sample_rate))
        frames.append(struct.pack("<h", value))
    return pcm16_to_wav_bytes(b"".join(frames), sample_rate)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

