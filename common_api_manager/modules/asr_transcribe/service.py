from __future__ import annotations

import base64
import io
import time
import wave
from typing import Any

from fastapi import HTTPException

from common.settings import settings


def load_asr_sdk():
    try:
        from dashscope.audio.qwen_omni import (
            MultiModality,
            OmniRealtimeCallback,
            OmniRealtimeConversation,
        )
        from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail='DashScope ASR SDK not installed. Run: python -m pip install -U "dashscope>=1.25.6"',
        ) from exc
    return OmniRealtimeConversation, OmniRealtimeCallback, MultiModality, TranscriptionParams


def require_api_key() -> str:
    if not settings.dashscope_api_key:
        raise HTTPException(
            status_code=500,
            detail="Missing DASHSCOPE_API_KEY. Set it in environment variables or common_api_manager/.env.",
        )
    return settings.dashscope_api_key


def wav_to_pcm16_mono(audio: bytes) -> tuple[bytes, int]:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
    except wave.Error as exc:
        raise HTTPException(status_code=400, detail=f"Only PCM WAV audio is supported by this endpoint: {exc}") from exc

    if sample_width != 2:
        raise HTTPException(status_code=400, detail="Only 16-bit PCM WAV audio is supported.")
    if channels == 1:
        return frames, sample_rate
    if channels != 2:
        raise HTTPException(status_code=400, detail="Only mono or stereo WAV audio is supported.")

    mono = bytearray()
    for index in range(0, len(frames), 4):
        left = int.from_bytes(frames[index : index + 2], "little", signed=True)
        right = int.from_bytes(frames[index + 2 : index + 4], "little", signed=True)
        mono.extend(int((left + right) / 2).to_bytes(2, "little", signed=True))
    return bytes(mono), sample_rate


def resample_pcm16(pcm_audio: bytes, from_rate: int, to_rate: int) -> bytes:
    if from_rate == to_rate:
        return pcm_audio

    samples = [
        int.from_bytes(pcm_audio[index : index + 2], "little", signed=True)
        for index in range(0, len(pcm_audio), 2)
    ]
    if not samples:
        return b""

    ratio = from_rate / to_rate
    output_length = max(1, round(len(samples) / ratio))
    output = bytearray()
    for index in range(output_length):
        source_index = index * ratio
        left_index = int(source_index)
        right_index = min(left_index + 1, len(samples) - 1)
        fraction = source_index - left_index
        value = round(samples[left_index] * (1 - fraction) + samples[right_index] * fraction)
        output.extend(int(value).to_bytes(2, "little", signed=True))
    return bytes(output)


def transcribe_with_dashscope(
    pcm_audio: bytes,
    sample_rate: int,
    language: str,
    chunk_ms: int = 100,
) -> dict[str, Any]:
    require_api_key()
    OmniRealtimeConversation, OmniRealtimeCallback, MultiModality, TranscriptionParams = load_asr_sdk()

    class RealtimeASRCallback(OmniRealtimeCallback):
        def __init__(self) -> None:
            super().__init__()
            self.final_texts: list[str] = []
            self.partial_text = ""
            self.events: list[dict[str, Any]] = []
            self.errors: list[dict[str, Any]] = []
            self.session_id = ""

        def on_event(self, response: dict[str, Any]) -> None:
            event_type = response.get("type", "")
            self.events.append({"type": event_type, "time": time.time()})

            if event_type == "session.created":
                self.session_id = response.get("session", {}).get("id", "")
            elif event_type == "conversation.item.input_audio_transcription.text":
                text = response.get("text", "")
                stash = response.get("stash", "")
                partial = f"{text}{stash}".strip()
                if partial:
                    self.partial_text = partial
            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "").strip()
                if transcript:
                    self.final_texts.append(transcript)
            elif event_type == "session.finished":
                transcript = response.get("transcript", "").strip()
                if transcript and transcript not in self.final_texts:
                    self.final_texts.append(transcript)
            elif event_type == "error":
                self.errors.append(response)

        def final_text(self) -> str:
            text = "".join(self.final_texts).strip()
            return text or self.partial_text.strip()

    callback = RealtimeASRCallback()
    conversation = OmniRealtimeConversation(model=settings.asr_model, url=settings.realtime_url, callback=callback)
    chunk_bytes = max(2, int(sample_rate * 2 * chunk_ms / 1000))
    if chunk_bytes % 2:
        chunk_bytes += 1

    started = time.time()
    try:
        conversation.connect()
        transcription_params = TranscriptionParams(
            language=language,
            sample_rate=sample_rate,
            input_audio_format="pcm",
        )
        conversation.update_session(
            output_modalities=[MultiModality.TEXT],
            enable_turn_detection=True,
            turn_detection_type="server_vad",
            turn_detection_threshold=0.0,
            turn_detection_silence_duration_ms=700,
            enable_input_audio_transcription=True,
            transcription_params=transcription_params,
        )

        for offset in range(0, len(pcm_audio), chunk_bytes):
            audio_b64 = base64.b64encode(pcm_audio[offset : offset + chunk_bytes]).decode("ascii")
            conversation.append_audio(audio_b64)
            time.sleep(chunk_ms / 1000)

        try:
            conversation.end_session(timeout=20)
        except TypeError:
            conversation.end_session()
            time.sleep(2)
    finally:
        try:
            conversation.close()
        except Exception:
            pass

    return {
        "text": callback.final_text(),
        "session_id": callback.session_id,
        "model": settings.asr_model,
        "language": language,
        "sample_rate": sample_rate,
        "elapsed_seconds": round(time.time() - started, 3),
        "errors": callback.errors,
        "events": callback.events[-20:],
    }


def transcribe_audio_bytes(audio: bytes, language: str) -> dict[str, Any]:
    if not audio:
        raise HTTPException(status_code=400, detail="empty audio file")
    if len(audio) > settings.max_audio_bytes:
        raise HTTPException(status_code=413, detail=f"audio file too large, max {settings.max_audio_bytes} bytes")

    pcm_audio, sample_rate = wav_to_pcm16_mono(audio)
    if not pcm_audio:
        raise HTTPException(status_code=400, detail="audio file has no PCM frames")
    pcm_audio = resample_pcm16(pcm_audio, from_rate=sample_rate, to_rate=settings.target_sample_rate)
    return transcribe_with_dashscope(
        pcm_audio,
        sample_rate=settings.target_sample_rate,
        language=language or settings.default_language,
    )

