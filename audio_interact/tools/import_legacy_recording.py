from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.session_writer import SessionWriter  # noqa: E402


def import_legacy_recording(recording_dir: Path, *, data_root: Path | None = None) -> Path:
    session_json = recording_dir / "session.json"
    full_wav = recording_dir / "full.wav"
    if not session_json.exists():
        raise FileNotFoundError(f"missing {session_json}")
    if not full_wav.exists():
        raise FileNotFoundError(f"missing {full_wav}")
    meta = json.loads(session_json.read_text(encoding="utf-8"))
    root = data_root or _infer_data_root(recording_dir)
    session_id = str(meta.get("session_id") or recording_dir.name)
    writer = SessionWriter(
        root,
        session_id=session_id,
        device_id=str(meta.get("device_id") or "unknown"),
        sample_rate=int(meta.get("sample_rate") or 16000),
        source="legacy_recording_import",
        session_day=_recording_day(recording_dir),
    )
    writer.copy_audio(full_wav, "mic_raw_16k.wav")
    writer.copy_audio(full_wav, "mic_proc_16k.wav")
    for index, utterance in enumerate(meta.get("utterances") or []):
        start_ms = _seconds_to_ms(utterance.get("vad_start_seconds"))
        end_ms = _seconds_to_ms(utterance.get("vad_end_seconds"))
        segment_id = f"seg_{index + 1:03d}"
        turn_id = f"turn_{index + 1:03d}"
        writer.emit("vad", ts_ms=start_ms, type="vad.speech_start", segment_id=segment_id)
        writer.emit("vad", ts_ms=end_ms, type="vad.speech_end", segment_id=segment_id, start_ms=start_ms, end_ms=end_ms, reason=utterance.get("reason"))
        writer.emit(
            "asr",
            ts_ms=end_ms,
            type="asr.final",
            segment_id=segment_id,
            turn_id=turn_id,
            audio_start_ms=start_ms,
            audio_end_ms=end_ms,
            text=str(utterance.get("text") or ""),
            wake_status=utterance.get("wake_status"),
            status=utterance.get("status"),
        )
    writer.close(duration_ms=int(round(float(meta.get("duration_seconds") or 0) * 1000)))
    return writer.session_dir


def _infer_data_root(recording_dir: Path) -> Path:
    parts = recording_dir.resolve().parts
    if "recordings" in parts:
        index = parts.index("recordings")
        return Path(*parts[:index])
    return recording_dir.parent


def _recording_day(recording_dir: Path) -> str | None:
    parent = recording_dir.parent.name
    if len(parent) == 10 and parent[4] == "-" and parent[7] == "-":
        return parent
    return None


def _seconds_to_ms(value: object) -> int:
    try:
        return max(0, int(round(float(value) * 1000)))
    except (TypeError, ValueError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert legacy recordings/<day>/<session> into a standard session package.")
    parser.add_argument("recording_dir", type=Path)
    parser.add_argument("--data-root", type=Path, default=None)
    args = parser.parse_args()
    session_dir = import_legacy_recording(args.recording_dir, data_root=args.data_root)
    print(session_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
