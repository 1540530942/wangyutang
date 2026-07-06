"""arecord wrapper — zero third-party dependencies."""
from __future__ import annotations

import subprocess
import time
import wave
from pathlib import Path
from typing import Any


def record_wav(config: dict[str, Any]) -> Path:
    output_dir = Path(str(config.get("output_dir") or "/tmp/audio_interact"))
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / f"cmd_{int(time.time() * 1000)}.wav"
    command = [
        "arecord",
        "-D", str(config.get("device") or "plughw:CARD=Device,DEV=0"),
        "-f", "S16_LE",
        "-r", str(int(config.get("sample_rate") or 16000)),
        "-c", str(int(config.get("channels") or 1)),
        "-d", str(int(config.get("seconds") or 4)),
        str(wav_path),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=int(config.get("seconds") or 4) + 8)
    if completed.returncode != 0:
        raise RuntimeError(f"arecord failed: {completed.stderr.strip()}")
    return wav_path
