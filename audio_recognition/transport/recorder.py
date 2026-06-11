from __future__ import annotations

import subprocess
import time
import wave
from pathlib import Path
from typing import Any


def normalize_wav_volume(path: Path, config: dict[str, Any]) -> None:
    target_rms = int(config.get("normalize_target_rms") or 0)
    if target_rms <= 0:
        return

    max_gain = float(config.get("normalize_max_gain") or 8.0)
    with wave.open(str(path), "rb") as reader:
        params = reader.getparams()
        frames = reader.readframes(reader.getnframes())
    if not frames or params.sampwidth != 2:
        return

    import audioop

    rms = audioop.rms(frames, params.sampwidth)
    if rms <= 0 or rms >= target_rms:
        return
    gain = min(target_rms / rms, max_gain)
    normalized = audioop.mul(frames, params.sampwidth, gain)
    with wave.open(str(path), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(normalized)


def record_wav(config: dict[str, Any]) -> Path:
    output_dir = Path(str(config.get("output_dir") or "/tmp/audio_recognition"))
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / f"command_{int(time.time() * 1000)}.wav"

    command = [
        "arecord",
        "-D",
        str(config.get("device") or "plughw:CARD=Device,DEV=0"),
        "-f",
        "S16_LE",
        "-r",
        str(int(config.get("sample_rate") or 16000)),
        "-c",
        str(int(config.get("channels") or 1)),
        "-d",
        str(int(config.get("seconds") or 4)),
        str(wav_path),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=int(config.get("seconds") or 4) + 8)
    if completed.returncode != 0:
        raise RuntimeError(f"arecord failed: {completed.stderr.strip()}")
    normalize_wav_volume(wav_path, config)
    return wav_path
