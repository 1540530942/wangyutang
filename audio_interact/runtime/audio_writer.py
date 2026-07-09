from __future__ import annotations

import io
import wave
from pathlib import Path


def pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16)
    return buf.getvalue()


def write_wav_bytes(path: Path, wav_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes)


def write_pcm16_wav(path: Path, pcm16: bytes, sample_rate: int, channels: int = 1) -> None:
    write_wav_bytes(path, pcm16_to_wav_bytes(pcm16, sample_rate, channels))


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
    if rate <= 0:
        return 0
    return int(round(frames * 1000 / rate))

