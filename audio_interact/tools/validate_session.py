from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path
from typing import Any


REQUIRED_FILES = [
    "manifest.json",
    "audio/mic_proc_16k.wav",
    "events/runtime_events.jsonl",
    "events/vad_runtime.jsonl",
    "events/asr_runtime.jsonl",
    "events/tts_runtime.jsonl",
    "events/bargein_runtime.jsonl",
    "labels/vad_label.json",
    "labels/asr_label.json",
    "labels/tts_label.json",
    "labels/bargein_label.json",
]


def validate_session(session_dir: Path) -> list[str]:
    errors: list[str] = []
    if not session_dir.exists():
        return [f"session_dir not found: {session_dir}"]
    for rel in REQUIRED_FILES:
        if not (session_dir / rel).exists():
            errors.append(f"missing {rel}")
    manifest = _load_json(session_dir / "manifest.json", errors)
    if manifest:
        _require(manifest, "session_id", str, errors, "manifest")
        _require(manifest, "duration_ms", int, errors, "manifest")
        audio = manifest.get("audio")
        if not isinstance(audio, dict):
            errors.append("manifest.audio must be an object")
        else:
            _require(audio, "sample_rate", int, errors, "manifest.audio")
            _require(audio, "channels", int, errors, "manifest.audio")
            _require(audio, "chunk_ms", int, errors, "manifest.audio")
    _validate_wav(session_dir / "audio" / "mic_proc_16k.wav", errors)
    for rel in ("events/runtime_events.jsonl", "events/vad_runtime.jsonl", "events/asr_runtime.jsonl", "events/tts_runtime.jsonl", "events/bargein_runtime.jsonl"):
        _validate_jsonl(session_dir / rel, errors)
    for rel, key in (
        ("labels/vad_label.json", "speech_segments"),
        ("labels/asr_label.json", "turns"),
        ("labels/tts_label.json", "tts_segments"),
        ("labels/bargein_label.json", "bargein_cases"),
    ):
        payload = _load_json(session_dir / rel, errors)
        if payload and not isinstance(payload.get(key), list):
            errors.append(f"{rel}.{key} must be a list")
    return errors


def _load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid json {path.name}: {exc}")
        return {}
    if not isinstance(data, dict):
        errors.append(f"{path.name} must contain a json object")
        return {}
    return data


def _validate_jsonl(path: Path, errors: list[str]) -> None:
    if not path.exists():
        return
    try:
        with path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    errors.append(f"{path.name}:{lineno} is not an object")
                if "type" not in event:
                    errors.append(f"{path.name}:{lineno} missing type")
                if "ts_ms" in event and not isinstance(event["ts_ms"], int):
                    errors.append(f"{path.name}:{lineno} ts_ms must be int")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid jsonl {path.name}: {exc}")


def _validate_wav(path: Path, errors: list[str]) -> None:
    if not path.exists():
        return
    try:
        with wave.open(str(path), "rb") as wav_file:
            if wav_file.getnchannels() < 1:
                errors.append("mic_proc_16k.wav has no channels")
            if wav_file.getframerate() <= 0:
                errors.append("mic_proc_16k.wav has invalid sample rate")
    except wave.Error as exc:
        errors.append(f"invalid wav audio/mic_proc_16k.wav: {exc}")


def _require(payload: dict[str, Any], key: str, expected: type, errors: list[str], scope: str) -> None:
    if key not in payload:
        errors.append(f"{scope} missing {key}")
    elif not isinstance(payload[key], expected):
        errors.append(f"{scope}.{key} must be {expected.__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an audio_interact session package.")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="Print machine-readable result.")
    args = parser.parse_args()
    errors = validate_session(args.session_dir)
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    elif errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
    else:
        print(f"OK: {args.session_dir}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

