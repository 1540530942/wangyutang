from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioChunk:
    chunk_id: int
    start_ms: int
    end_ms: int
    pcm16: bytes


def stream_wav_chunks(path: Path, chunk_ms: int) -> list[AudioChunk]:
    chunks: list[AudioChunk] = []
    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frames_per_chunk = max(1, int(sample_rate * chunk_ms / 1000))
        chunk_id = 0
        while True:
            data = wav_file.readframes(frames_per_chunk)
            if not data:
                break
            start_ms = int(round(chunk_id * frames_per_chunk * 1000 / sample_rate))
            frames = len(data) // max(1, channels * sample_width)
            end_ms = int(round((chunk_id * frames_per_chunk + frames) * 1000 / sample_rate))
            chunks.append(AudioChunk(chunk_id=chunk_id, start_ms=start_ms, end_ms=end_ms, pcm16=data))
            chunk_id += 1
    return chunks

