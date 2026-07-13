from __future__ import annotations

import asyncio
import base64
import collections
import io
import json
import os
import secrets
import struct
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import requests
from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from runtime.session_writer import (
    write_segment_session_package as _write_segment_session_package,
    write_streaming_session_package as _write_streaming_session_package,
)
from settings import load_settings, save_settings
from wake_state import WakeDecision, WakeStateStore

_STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


# robot_sandbox was formerly named audio_recognition; accept the old env var as a fallback.
ROBOT_SANDBOX_URL = os.getenv("ROBOT_SANDBOX_URL") or os.getenv("AUDIO_RECOGNITION_URL", "http://robot-sandbox:8095")
COMMON_ASR_URL = os.getenv("COMMON_ASR_URL", "https://www.wangyutang.cn/common/api/asr/transcribe")
TTS_URL = os.getenv("AUDIO_TTS_URL", "https://www.wangyutang.cn/common/api/tts/speech")
TTS_MODEL = os.getenv("AUDIO_TTS_MODEL", "qwen3-tts-12hz-1.7b-customvoice")
TTS_VOICE = os.getenv("AUDIO_TTS_VOICE", "vivian")
TTS_INSTRUCTIONS = os.getenv("AUDIO_TTS_INSTRUCTIONS", "用清新自然、甜美温柔的语气说，声音明亮亲切，语调轻快柔和")
TTS_TIMEOUT = int(os.getenv("AUDIO_TTS_TIMEOUT", "30"))
TTS_SUPPORTED_VOICES = [
    voice.strip()
    for voice in os.getenv("AUDIO_TTS_SUPPORTED_VOICES", "aiden,dylan,eric,ono_anna,ryan,serena,sohee,uncle_fu,vivian").split(",")
    if voice.strip()
]
ASR_TIMEOUT = int(os.getenv("ASR_TIMEOUT", "60"))
ROUTE_TIMEOUT = int(os.getenv("ROUTE_TIMEOUT", "90"))
STREAM_SAMPLE_RATE = int(os.getenv("STREAM_SAMPLE_RATE", "16000"))
SILERO_REPO = os.getenv("SILERO_REPO", "snakers4/silero-vad")
SILERO_MODEL = os.getenv("SILERO_MODEL", "silero_vad")
SILERO_THRESHOLD = float(os.getenv("SILERO_THRESHOLD", "0.55"))
SILERO_START_FRAMES = int(os.getenv("SILERO_START_FRAMES", "3"))
SILERO_END_FRAMES = int(os.getenv("SILERO_END_FRAMES", "20"))
SILERO_PRE_FRAMES = int(os.getenv("SILERO_PRE_FRAMES", "8"))
SILERO_MAX_SECONDS = float(os.getenv("SILERO_MAX_SECONDS", "12"))

# Audio file storage (P3)
from settings import DATA_DIR as _SETTINGS_DATA_DIR
AUDIO_DATA_DIR = Path(os.getenv("AUDIO_INTERACT_DATA_DIR", str(_SETTINGS_DATA_DIR)))
SEGMENTS_DIR = AUDIO_DATA_DIR / "segments"
SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
# Full (un-truncated) streaming-session recordings, organized by date, with the
# VAD utterance markers + ASR results, for later tracing / replay simulation.
RECORDINGS_DIR = AUDIO_DATA_DIR / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)


def safe_write_streaming_session_package(**kwargs: Any) -> str | None:
    try:
        return _write_streaming_session_package(**kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] session_package_failed: {exc}", flush=True)
        return None


def safe_write_segment_session_package(**kwargs: Any) -> str:
    try:
        return _write_segment_session_package(**kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] segment_session_package_failed: {exc}", flush=True)
        return ""


def save_session_recording(session_id: str, device_id: str, sample_rate: int, full_pcm: bytes, utterances: list[dict[str, Any]]) -> str | None:
    """Persist a full streaming session in legacy and standard layouts.

    Legacy layout:
      <AUDIO_DATA_DIR>/recordings/<YYYY-MM-DD>/<session_id>/full.wav
      <AUDIO_DATA_DIR>/recordings/<YYYY-MM-DD>/<session_id>/session.json

    Standard replay/eval layout:
      <AUDIO_DATA_DIR>/sessions/<YYYY-MM-DD>/<session_id>/{manifest,audio,events,labels,replay,reports}
    """
    if not full_pcm:
        return None
    standard_rel = safe_write_streaming_session_package(
        data_root=AUDIO_DATA_DIR,
        session_id=session_id,
        device_id=device_id,
        sample_rate=sample_rate,
        full_pcm=full_pcm,
        utterances=utterances,
        chunk_ms=int(1000 * 512 / sample_rate),
    )
    day = time.strftime("%Y-%m-%d")
    safe_session = "".join(c for c in session_id if c.isalnum() or c in "-_") or uuid.uuid4().hex[:12]
    out_dir = RECORDINGS_DIR / day / safe_session
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "full.wav").write_bytes(pcm16_to_wav(full_pcm, sample_rate))
    meta = {
        "session_id": session_id,
        "device_id": device_id,
        "sample_rate": sample_rate,
        "recorded_at": time.time(),
        "duration_seconds": round(len(full_pcm) / 2 / sample_rate, 3),
        "truncated": False,
        "utterances": utterances,
        "session_package": standard_rel,
    }
    (out_dir / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return standard_rel or f"recordings/{day}/{safe_session}"

app = FastAPI(title="Audio Interact Service", version="0.3.0")
WAKE_STATES = WakeStateStore()
SILERO_VAD_MODEL: Any | None = None
SILERO_TORCH: Any | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "audio-interact",
        "vad": "silero_streaming",
        "sample_rate": STREAM_SAMPLE_RATE,
        "ts": time.time(),
    }


@app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = str(uuid.uuid4())[:12]
    device_id = "turbopi-01"
    audio_buf = bytearray()
    stream_vad: StreamingSileroVad | None = None
    # Full un-truncated streaming audio + per-utterance VAD/ASR markers.
    session_full = bytearray()
    session_utterances: list[dict[str, Any]] = []
    pending_start: float | None = None
    route_enabled = True  # web模式 dispatches actions; VAD_ASR_TTS debug does ASR-only

    def flush_session() -> str | None:
        nonlocal session_full, session_utterances, pending_start
        rel = save_session_recording(session_id, device_id, STREAM_SAMPLE_RATE, bytes(session_full), list(session_utterances))
        session_full = bytearray()
        session_utterances = []
        pending_start = None
        return rel

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            if msg.get("bytes"):
                if stream_vad is not None:
                    session_full.extend(msg["bytes"])  # keep the full, un-truncated stream
                    events = stream_vad.feed(msg["bytes"])
                    for event in events:
                        if event.get("type") == "speech_start":
                            pending_start = event.get("offset_seconds")
                            await websocket.send_text(json.dumps(event, ensure_ascii=False))
                        elif event.get("type") == "speech_end" and event.get("wav_bytes"):
                            wav_bytes = event.pop("wav_bytes")
                            end_offset = event.get("offset_seconds")
                            await websocket.send_text(json.dumps(event, ensure_ascii=False))
                            await websocket.send_text(
                                json.dumps(
                                    {"type": "asr_started", "session_id": session_id, "device_id": device_id},
                                    ensure_ascii=False,
                                )
                            )
                            loop = asyncio.get_event_loop()
                            result = await loop.run_in_executor(None, _process, wav_bytes, device_id, session_id, route_enabled)
                            result["streaming_vad"] = "silero"
                            await websocket.send_text(json.dumps(result, ensure_ascii=False))
                            # record this utterance's VAD window + ASR/command outcome
                            session_utterances.append({
                                "index": len(session_utterances),
                                "vad_start_seconds": pending_start,
                                "vad_end_seconds": end_offset,
                                "reason": event.get("reason"),
                                "text": result.get("text", ""),
                                "wake_status": result.get("wake_status", ""),
                                "skill_id": result.get("skill_id", ""),
                                "status": result.get("status", ""),
                                "action_task": result.get("action_task"),
                                "tts_text": result.get("tts_text", ""),
                                "action_error": result.get("action_error", ""),
                            })
                            pending_start = None
                            tts_text = result.get("tts_text", "")
                            if tts_text and TTS_URL:
                                asyncio.create_task(_push_tts(websocket, tts_text))
                        else:
                            await websocket.send_text(json.dumps(event, ensure_ascii=False))
                else:
                    audio_buf.extend(msg["bytes"])

            elif msg.get("text"):
                frame = json.loads(msg["text"])
                frame_type = frame.get("type", "")

                if frame_type == "start":
                    session_id = str(frame.get("session_id") or session_id)
                    device_id = str(frame.get("device_id") or device_id)
                    stream_vad = None
                    audio_buf.clear()
                    await websocket.send_text(json.dumps({"type": "ready", "session_id": session_id}))

                elif frame_type == "start_stream":
                    session_id = str(frame.get("session_id") or session_id)
                    device_id = str(frame.get("device_id") or device_id)
                    route_enabled = bool(frame.get("route", True))
                    sample_rate = int(frame.get("sample_rate") or STREAM_SAMPLE_RATE)
                    if sample_rate != STREAM_SAMPLE_RATE:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "stage": "vad",
                                    "message": f"stream sample_rate must be {STREAM_SAMPLE_RATE}",
                                    "session_id": session_id,
                                },
                                ensure_ascii=False,
                            )
                        )
                        continue
                    try:
                        stream_vad = StreamingSileroVad(session_id=session_id, device_id=device_id)
                    except Exception as exc:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "stage": "vad",
                                    "message": str(exc),
                                    "session_id": session_id,
                                },
                                ensure_ascii=False,
                            )
                        )
                        continue
                    audio_buf.clear()
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "stream_ready",
                                "session_id": session_id,
                                "device_id": device_id,
                                "vad": "silero",
                                "sample_rate": STREAM_SAMPLE_RATE,
                            },
                            ensure_ascii=False,
                        )
                    )

                elif frame_type == "end":
                    if not audio_buf:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": "no audio received",
                                    "session_id": session_id,
                                }
                            )
                        )
                        continue

                    wav_bytes = bytes(audio_buf)
                    audio_buf.clear()
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, _process, wav_bytes, device_id, session_id, route_enabled)
                    await websocket.send_text(json.dumps(result, ensure_ascii=False))
                    tts_text = result.get("tts_text", "")
                    if tts_text and TTS_URL:
                        asyncio.create_task(_push_tts(websocket, tts_text))

                elif frame_type in {"stop_stream", "end_stream"}:
                    if stream_vad is not None:
                        final_wav = stream_vad.finish()
                        stream_vad = None
                        if final_wav:
                            await websocket.send_text(
                                json.dumps(
                                    {"type": "asr_started", "session_id": session_id, "device_id": device_id},
                                    ensure_ascii=False,
                                )
                            )
                            loop = asyncio.get_event_loop()
                            result = await loop.run_in_executor(None, _process, final_wav, device_id, session_id, route_enabled)
                            result["streaming_vad"] = "silero"
                            await websocket.send_text(json.dumps(result, ensure_ascii=False))
                            tts_text = result.get("tts_text", "")
                            if tts_text and TTS_URL:
                                asyncio.create_task(_push_tts(websocket, tts_text))
                    saved = flush_session()
                    await websocket.send_text(json.dumps({"type": "stream_stopped", "session_id": session_id, "recording": saved}))

    except WebSocketDisconnect:
        pass
    finally:
        # Persist the full session recording even if the client just disconnected.
        try:
            flush_session()
        except Exception:
            pass


def load_silero_vad() -> tuple[Any, Any]:
    global SILERO_TORCH, SILERO_VAD_MODEL
    if SILERO_TORCH is not None and SILERO_VAD_MODEL is not None:
        return SILERO_TORCH, SILERO_VAD_MODEL
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Silero VAD requires torch in the audio-interact container.") from exc
    try:
        from silero_vad import load_silero_vad

        model = load_silero_vad()
    except Exception:
        try:
            model, _utils = torch.hub.load(repo_or_dir=SILERO_REPO, model=SILERO_MODEL, trust_repo=True)
        except TypeError:
            model, _utils = torch.hub.load(repo_or_dir=SILERO_REPO, model=SILERO_MODEL)
    model.eval()
    SILERO_TORCH = torch
    SILERO_VAD_MODEL = model
    return torch, model


@dataclass
class StreamingSileroVad:
    session_id: str
    device_id: str
    sample_rate: int = STREAM_SAMPLE_RATE
    frame_samples: int = 512
    pending: bytearray = field(default_factory=bytearray)
    pre_frames: collections.deque[bytes] = field(default_factory=lambda: collections.deque(maxlen=SILERO_PRE_FRAMES))
    speech_frames: list[bytes] = field(default_factory=list)
    speech_count: int = 0
    silence_count: int = 0
    speaking: bool = False
    started_at: float = 0.0
    total_frames: int = 0

    def __post_init__(self) -> None:
        self.torch, self.model = load_silero_vad()
        reset_states = getattr(self.model, "reset_states", None)
        if callable(reset_states):
            reset_states()

    def feed(self, pcm16: bytes) -> list[dict[str, Any]]:
        self.pending.extend(pcm16)
        events: list[dict[str, Any]] = []
        frame_bytes = self.frame_samples * 2
        while len(self.pending) >= frame_bytes:
            frame = bytes(self.pending[:frame_bytes])
            del self.pending[:frame_bytes]
            events.extend(self._consume_frame(frame))
        return events

    def finish(self) -> bytes | None:
        if not self.speaking or not self.speech_frames:
            return None
        frames = self.speech_frames
        self._reset_segment()
        return pcm16_to_wav(b"".join(frames), self.sample_rate)

    def _consume_frame(self, frame: bytes) -> list[dict[str, Any]]:
        probability = self._speech_probability(frame)
        is_speech = probability >= SILERO_THRESHOLD
        self.total_frames += 1
        now_offset = self.total_frames * self.frame_samples / self.sample_rate
        events: list[dict[str, Any]] = [
            {
                "type": "vad",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "probability": round(probability, 4),
                "speaking": self.speaking,
            }
        ]

        if not self.speaking:
            self.pre_frames.append(frame)
            self.speech_count = self.speech_count + 1 if is_speech else 0
            if self.speech_count >= SILERO_START_FRAMES:
                self.speaking = True
                self.started_at = time.time()
                self.speech_frames = list(self.pre_frames)
                self.silence_count = 0
                events.append(
                    {
                        "type": "speech_start",
                        "session_id": self.session_id,
                        "device_id": self.device_id,
                        "probability": round(probability, 4),
                        "offset_seconds": round(max(0.0, now_offset - SILERO_START_FRAMES * self.frame_samples / self.sample_rate), 3),
                    }
                )
            return events

        self.speech_frames.append(frame)
        self.silence_count = 0 if is_speech else self.silence_count + 1
        duration_seconds = len(self.speech_frames) * self.frame_samples / self.sample_rate
        should_end = self.silence_count >= SILERO_END_FRAMES or duration_seconds >= SILERO_MAX_SECONDS
        if should_end:
            wav_bytes = pcm16_to_wav(b"".join(self.speech_frames), self.sample_rate)
            reason = "silence" if self.silence_count >= SILERO_END_FRAMES else "max_duration"
            events.append(
                {
                    "type": "speech_end",
                    "session_id": self.session_id,
                    "device_id": self.device_id,
                    "reason": reason,
                    "duration_seconds": round(duration_seconds, 3),
                    "offset_seconds": round(now_offset, 3),
                    "wav_bytes": wav_bytes,
                }
            )
            self._reset_segment()
        return events

    def _speech_probability(self, frame: bytes) -> float:
        samples = struct.unpack("<512h", frame)
        tensor = self.torch.tensor([sample / 32768.0 for sample in samples], dtype=self.torch.float32)
        with self.torch.no_grad():
            return float(self.model(tensor, self.sample_rate).item())

    def _reset_segment(self) -> None:
        self.pre_frames.clear()
        self.speech_frames = []
        self.speech_count = 0
        self.silence_count = 0
        self.speaking = False
        self.started_at = 0.0


def pcm16_to_wav(pcm16: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16)
    return buf.getvalue()


def _fetch_tts_audio(text: str, *, voice: str | None = None, instructions: str | None = None) -> bytes:
    payload = {
        "model": TTS_MODEL,
        "input": text,
        "voice": voice or TTS_VOICE,
        "language": "chinese",
        "instructions": instructions or TTS_INSTRUCTIONS,
        "response_format": "wav",
    }
    resp = requests.post(TTS_URL, json=payload, timeout=TTS_TIMEOUT)
    resp.raise_for_status()
    audio = resp.content
    if not audio.startswith(b"RIFF") and not audio.startswith(b"ID3"):
        raise RuntimeError("TTS response is not audio")
    return audio


async def _push_tts(ws: WebSocket, text: str) -> None:
    try:
        loop = asyncio.get_event_loop()
        audio = await loop.run_in_executor(None, _fetch_tts_audio, text)
        await ws.send_bytes(audio)
    except Exception as exc:
        print(f"[WARN] tts_failed: {exc}", flush=True)


def _process(wav_bytes: bytes, device_id: str, session_id: str, route_action: bool = True) -> dict[str, Any]:
    started = time.time()

    try:
        resp = requests.post(
            COMMON_ASR_URL,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"language": "zh"},
            timeout=ASR_TIMEOUT,
        )
        resp.raise_for_status()
        asr_payload = resp.json()
        asr_done_at = time.time()
        text = str(asr_payload.get("text") or "").strip()
    except Exception as exc:
        return {
            "type": "error",
            "session_id": session_id,
            "stage": "asr",
            "message": str(exc),
            "elapsed_ms": elapsed_ms(started),
        }

    if not text:
        return {
            "type": "result",
            "session_id": session_id,
            "text": "",
            "route_text": "",
            "wake_status": "empty",
            "skill_id": "",
            "status": "empty",
            "elapsed_ms": elapsed_ms(started),
        }

    wake = WAKE_STATES.decide(device_id, text)
    if not route_action:
        # ASR/debug only (VAD_ASR_TTS): report text + wake status, do NOT call
        # robot_sandbox and do NOT dispatch any action.
        return {
            "type": "result",
            "session_id": session_id,
            "text": text,
            "route_text": wake.route_text,
            "wake_status": wake.status,
            "skill_id": "",
            "status": "asr_only",
            "elapsed_ms": elapsed_ms(started),
        }
    if not wake.should_route:
        return wake_only_result(session_id=session_id, text=text, wake=wake, started=started)

    try:
        resp = requests.post(
            f"{ROBOT_SANDBOX_URL}/api/recognize-text",
            json={
                "device_id": device_id,
                "text": wake.route_text,
                "source": "audio-interact",
                "route_action": True,
                "raw": {
                    "asr": asr_payload,
                    "asr_text": text,
                    "wake_status": wake.status,
                    "wake_message": wake.message,
                    "capture_at": started,
                    "asr_done_at": asr_done_at,
                    "asr_elapsed_ms": int((asr_done_at - started) * 1000),
                },
            },
            timeout=ROUTE_TIMEOUT,
        )
        resp.raise_for_status()
        route = resp.json()
    except Exception as exc:
        return {
            "type": "result",
            "session_id": session_id,
            "text": text,
            "route_text": wake.route_text,
            "wake_status": wake.status,
            "skill_id": "",
            "status": "route_error",
            "message": str(exc),
            "elapsed_ms": elapsed_ms(started),
        }

    return {
        "type": "result",
        "session_id": session_id,
        "text": text,
        "route_text": wake.route_text,
        "wake_status": wake.status,
        "skill_id": route.get("skill_id", ""),
        "action_task": route.get("action_task"),
        "face_task": route.get("face_task"),
        "plan": route.get("plan"),
        "tts_text": str(route.get("tts_text") or ""),
        "status": "ok",
        "elapsed_ms": elapsed_ms(started),
    }


def wake_only_result(*, session_id: str, text: str, wake: WakeDecision, started: float) -> dict[str, Any]:
    return {
        "type": "result",
        "session_id": session_id,
        "text": text,
        "route_text": wake.route_text,
        "wake_status": wake.status,
        "skill_id": "",
        "status": wake.message or wake.status,
        "elapsed_ms": elapsed_ms(started),
    }


def elapsed_ms(started: float) -> int:
    return int((time.time() - started) * 1000)


# ---------------------------------------------------------------------------
# P2 — settings API
# ---------------------------------------------------------------------------

def _require_token(x_audio_token: str | None) -> None:
    from settings import DATA_DIR as _dd
    token_file = _dd / ".audio_token"
    if not token_file.exists():
        return
    import secrets as _sec
    expected = token_file.read_text(encoding="utf-8").strip()
    if not expected:
        return
    if not x_audio_token or not _sec.compare_digest(x_audio_token, expected):
        raise HTTPException(status_code=401, detail="invalid audio token")


@app.get("/api/settings")
def get_settings_route() -> dict[str, Any]:
    return {"settings": load_settings()}


@app.post("/api/settings")
def update_settings_route(
    payload: dict[str, Any],
    x_audio_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _require_token(x_audio_token)
    settings = save_settings(payload)
    return {"ok": True, "settings": settings}


@app.post("/api/manual-recording/start")
def start_manual_recording_route(x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    _require_token(x_audio_token)
    settings = save_settings({"manual_recording_enabled": True})
    return {"ok": True, "settings": settings}


@app.post("/api/manual-recording/stop")
def stop_manual_recording_route(x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    _require_token(x_audio_token)
    settings = save_settings({"manual_recording_enabled": False})
    return {"ok": True, "settings": settings}


# ---------------------------------------------------------------------------
# P3 — audio segment upload endpoint  (WonderEchoPro cloud convergence point)
# ---------------------------------------------------------------------------

def _store_segment(wav_bytes: bytes) -> tuple[str, str]:
    """Persist a WAV segment and return (filename, audio_url)."""
    name = f"seg-{int(time.time() * 1000)}-{secrets.token_hex(4)}.wav"
    (SEGMENTS_DIR / name).write_bytes(wav_bytes)
    return name, f"/api/audio/{name}"


def _call_robot_sandbox(text: str, device_id: str, audio_url: str, asr_meta: dict[str, Any], wake_status: str) -> dict[str, Any]:
    """Call robot_sandbox /api/command; returns command response dict."""
    payload = {
        "text": text,
        "device_id": device_id,
        "dispatch_mode": os.getenv("SANDBOX_DISPATCH_MODE", "cloud_queue"),
        "context": {
            "source": "audio_interact",
            "audio_url": audio_url,
            "asr": asr_meta,
            "wake_status": wake_status,
        },
    }
    resp = requests.post(
        f"{ROBOT_SANDBOX_URL}/api/command",
        json=payload,
        timeout=ROUTE_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


@app.post("/api/audio/segment")
async def audio_segment(
    file: UploadFile = File(...),
    device_id: str = Form("turbopi-01"),
    session_id: str = Form(""),
) -> dict[str, Any]:
    """Receive a WAV segment from WonderEchoPro Pi listener or web client.

    Returns ASR text, wake status, command result, tts_text, and tts_audio_base64
    so the Pi can play back the response locally without a separate TTS request.
    """
    started = time.time()
    wav_bytes = await file.read()
    if not wav_bytes:
        raise HTTPException(status_code=400, detail="empty audio file")

    sess = session_id or str(uuid.uuid4())[:12]

    # Store audio and build URL for envelope reference
    seg_name, audio_url = _store_segment(wav_bytes)
    session_package = ""

    # ASR
    try:
        asr_resp = requests.post(
            COMMON_ASR_URL,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"language": "zh"},
            timeout=ASR_TIMEOUT,
        )
        asr_resp.raise_for_status()
        asr_payload = asr_resp.json()
        text = str(asr_payload.get("text") or "").strip()
    except Exception as exc:
        session_package = safe_write_segment_session_package(
            data_root=AUDIO_DATA_DIR,
            session_id=sess,
            device_id=device_id,
            wav_bytes=wav_bytes,
            asr_text="",
            wake_status="asr_error",
        )
        return {
            "ok": False,
            "session_id": sess,
            "stage": "asr",
            "message": str(exc),
            "session_package": session_package,
            "elapsed_ms": elapsed_ms(started),
        }

    if not text:
        session_package = safe_write_segment_session_package(
            data_root=AUDIO_DATA_DIR,
            session_id=sess,
            device_id=device_id,
            wav_bytes=wav_bytes,
            asr_text="",
            wake_status="empty",
        )
        return {
            "ok": True,
            "session_id": sess,
            "text": "",
            "wake_status": "empty",
            "command": None,
            "tts_text": "",
            "tts_audio_base64": None,
            "audio_url": audio_url,
            "session_package": session_package,
            "elapsed_ms": elapsed_ms(started),
        }

    # Wake-state gate
    wake = WAKE_STATES.decide(device_id, text)
    if not wake.should_route:
        session_package = safe_write_segment_session_package(
            data_root=AUDIO_DATA_DIR,
            session_id=sess,
            device_id=device_id,
            wav_bytes=wav_bytes,
            asr_text=text,
            wake_status=wake.status,
        )
        return {
            "ok": True,
            "session_id": sess,
            "text": text,
            "wake_status": wake.status,
            "command": None,
            "tts_text": "",
            "tts_audio_base64": None,
            "audio_url": audio_url,
            "session_package": session_package,
            "elapsed_ms": elapsed_ms(started),
        }

    # robot_sandbox /api/command
    try:
        command = _call_robot_sandbox(
            text=wake.route_text,
            device_id=device_id,
            audio_url=audio_url,
            asr_meta=asr_payload,
            wake_status=wake.status,
        )
    except Exception as exc:
        session_package = safe_write_segment_session_package(
            data_root=AUDIO_DATA_DIR,
            session_id=sess,
            device_id=device_id,
            wav_bytes=wav_bytes,
            asr_text=text,
            wake_status=wake.status,
        )
        return {
            "ok": False,
            "session_id": sess,
            "text": text,
            "wake_status": wake.status,
            "command": None,
            "tts_text": "",
            "tts_audio_base64": None,
            "audio_url": audio_url,
            "session_package": session_package,
            "message": str(exc),
            "elapsed_ms": elapsed_ms(started),
        }

    tts_text = str(command.get("tts_text") or "")

    # Fetch TTS audio for Pi local playback
    tts_audio_b64: str | None = None
    tts_audio_bytes: bytes | None = None
    if tts_text and TTS_URL:
        try:
            tts_audio_bytes = _fetch_tts_audio(tts_text)
            tts_audio_b64 = base64.b64encode(tts_audio_bytes).decode("ascii")
        except Exception as exc:
            print(f"[WARN] segment tts_failed: {exc}", flush=True)

    session_package = safe_write_segment_session_package(
        data_root=AUDIO_DATA_DIR,
        session_id=sess,
        device_id=device_id,
        wav_bytes=wav_bytes,
        asr_text=text,
        wake_status=wake.status,
        command=command,
        tts_text=tts_text,
        tts_wav_bytes=tts_audio_bytes,
    )

    return {
        "ok": True,
        "session_id": sess,
        "text": text,
        "wake_status": wake.status,
        "command": command,
        "tts_text": tts_text,
        "tts_audio_base64": tts_audio_b64,
        "audio_url": audio_url,
        "session_package": session_package,
        "elapsed_ms": elapsed_ms(started),
    }


@app.get("/dashboard", include_in_schema=False)
@app.get("/dashboard/", include_in_schema=False)
def dashboard_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard/detail", status_code=308)


@app.get("/dashboard/detail", include_in_schema=False)
def dashboard_detail_page() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "dashboard.html"))


@app.get("/dashboard/vad_asr", include_in_schema=False)
def dashboard_vad_asr_page() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "vad_asr.html"))


_GOLDEN_DIR = Path(__file__).resolve().parent / "tests" / "golden"
_GOLDEN_SESSIONS_DIR = _GOLDEN_DIR / "sessions"


def _load_golden_session_case(session_dir: Path) -> tuple[dict[str, Any], Path | None] | None:
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        turns_rel = str((manifest.get("labels") or {}).get("turns") or "labels/turns.json")
        turns_payload = json.loads((session_dir / turns_rel).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None

    session_id = str(manifest.get("session_id") or session_dir.name)
    case_id = str(manifest.get("case_id") or session_id)
    audio_meta = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    audio_file = str(audio_meta.get("file") or "audio/mic_proc_16k.wav")
    audio_path = session_dir / audio_file
    turns = list(turns_payload.get("turns") or [])
    duration_ms = int(audio_meta.get("duration_ms") or 0)
    return (
        {
            "case_id": case_id,
            "label": manifest.get("label") or manifest.get("mode") or "",
            "mode": manifest.get("mode") or "",
            "description": manifest.get("description") or "",
            "audio_file": f"sessions/{session_dir.name}/{audio_file}",
            "audio_source_session": session_id,
            "session_id": session_id,
            "day": str(manifest.get("created_at") or "")[:10],
            "capture_point": manifest.get("capture_point") or "unknown",
            "source": manifest.get("source") or "golden_session",
            "device_id": manifest.get("device_id") or "",
            "duration_ms": duration_ms,
            "created_at": manifest.get("created_at") or "",
            "utterances": turns,
            "audio_url": f"/audio_interact/api/golden/audio/{case_id}",
            "session_audio_url": f"/audio_interact/api/golden/audio/{session_id}",
        },
        audio_path if audio_path.exists() else None,
    )


def _iter_golden_session_cases() -> list[tuple[dict[str, Any], Path | None]]:
    if not _GOLDEN_SESSIONS_DIR.is_dir():
        return []
    cases: list[tuple[dict[str, Any], Path | None]] = []
    for session_dir in sorted(p for p in _GOLDEN_SESSIONS_DIR.iterdir() if p.is_dir()):
        loaded = _load_golden_session_case(session_dir)
        if loaded is not None:
            cases.append(loaded)
    return cases


def _legacy_golden_audio_path(case: dict[str, Any]) -> Path | None:
    audio_file = case.get("audio_file")
    if not audio_file:
        return None
    audio_path = _GOLDEN_DIR.parent.parent / str(audio_file)
    return audio_path if audio_path.exists() else None


@app.get("/api/golden", include_in_schema=False)
def list_golden_cases():
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case, audio_path in _iter_golden_session_cases():
        if audio_path is not None:
            case["audio_url"] = f"/audio_interact/api/golden/audio/{case['case_id']}"
        cases.append(case)
        seen.add(str(case.get("case_id") or ""))
        seen.add(str(case.get("session_id") or ""))

    if _GOLDEN_DIR.is_dir():
        for f in sorted(_GOLDEN_DIR.glob("*.json")):
            try:
                case = json.loads(f.read_text(encoding="utf-8"))
                case_id = str(case.get("case_id") or "")
                if case_id and case_id in seen:
                    continue
                # resolve audio_url: prefer bundled golden audio, fallback to session API
                if _legacy_golden_audio_path(case) is not None:
                    case["audio_url"] = f"/audio_interact/api/golden/audio/{case['case_id']}"
                elif case.get("audio_source_session"):
                    case["audio_url"] = f"/audio_interact/api/sessions/{case['audio_source_session']}/audio/mic_proc_16k.wav"
                cases.append(case)
            except Exception:
                pass
    return cases


@app.get("/api/golden/audio/{case_id}", include_in_schema=False)
def get_golden_audio(case_id: str):
    from fastapi import HTTPException
    safe_case_id = Path(case_id).name
    for case, audio_path in _iter_golden_session_cases():
        if safe_case_id in {str(case.get("case_id") or ""), str(case.get("session_id") or "")}:
            if audio_path is not None:
                return FileResponse(str(audio_path), media_type="audio/wav")
            break

    for f in _GOLDEN_DIR.glob("*.json"):
        try:
            case = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if case.get("case_id") == safe_case_id:
            audio_path = _legacy_golden_audio_path(case)
            if audio_path is not None:
                return FileResponse(str(audio_path), media_type="audio/wav")
            break
    raise HTTPException(status_code=404, detail="golden audio not found")


def _list_sessions(data_root: Path, limit: int = 100) -> list[dict[str, Any]]:
    """Scan sessions/ and recordings/ dirs; return merged list newest-first."""
    found: dict[str, dict[str, Any]] = {}
    for pkg_dir in sorted(data_root.glob("sessions/*/*/")):
        mf = pkg_dir / "manifest.json"
        if not mf.exists():
            continue
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        sid = str(manifest.get("session_id") or pkg_dir.name)
        found[sid] = {"session_id": sid, "day": pkg_dir.parent.name, "package_dir": str(pkg_dir),
                      "legacy_dir": None, "manifest": manifest, "legacy_meta": None}
    for rec_dir in sorted(data_root.glob("recordings/*/*/")):
        sf = rec_dir / "session.json"
        if not sf.exists():
            continue
        try:
            meta = json.loads(sf.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        sid = str(meta.get("session_id") or rec_dir.name)
        entry = found.setdefault(sid, {"session_id": sid, "day": rec_dir.parent.name,
                                       "package_dir": None, "manifest": None, "legacy_meta": None})
        entry["legacy_dir"] = str(rec_dir)
        entry["legacy_meta"] = meta
    results = sorted(found.values(), key=lambda e: (e["day"], e["session_id"]), reverse=True)
    return results[:limit]


def _session_created_at(entry: dict[str, Any]) -> str:
    manifest = entry.get("manifest") or {}
    legacy_meta = entry.get("legacy_meta") or {}
    created = str(manifest.get("created_at") or "").strip()
    if created:
        return created
    recorded_at = legacy_meta.get("recorded_at")
    if recorded_at:
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(float(recorded_at)))
        except (TypeError, ValueError, OSError):
            pass
    day = str(entry.get("day") or "").strip()
    return f"{day}T00:00:00" if day else ""


def _load_events_for_session(package_dir: Path) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    events: list[dict[str, Any]] = []
    for fname in ["runtime_events.jsonl", "vad_runtime.jsonl", "asr_runtime.jsonl", "tts_runtime.jsonl", "bargein_runtime.jsonl"]:
        fpath = package_dir / "events" / fname
        if not fpath.exists():
            continue
        for line in fpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            key = (int(ev.get("ts_ms", 0)), str(ev.get("type", "")), str(ev.get("segment_id", "")), str(ev.get("turn_id", "")))
            if key not in seen:
                seen.add(key)
                events.append(ev)
    events.sort(key=lambda e: (int(e.get("ts_ms", 0)), str(e.get("type", ""))))
    return events


@app.get("/api/sessions")
def list_sessions_route(limit: int = 60) -> list[dict[str, Any]]:
    entries = _list_sessions(AUDIO_DATA_DIR, limit)
    out = []
    for e in entries:
        m = e.get("manifest") or {}
        lm = e.get("legacy_meta") or {}
        utterances = lm.get("utterances") or []
        duration_ms = int(m.get("duration_ms") or round(float(lm.get("duration_seconds") or 0) * 1000))
        texts = [str(u.get("text") or "") for u in utterances if u.get("text")]
        audio_url = None
        if e.get("package_dir"):
            pkg = Path(e["package_dir"])
            for candidate in ("mic_proc_16k.wav", "mic_raw_16k.wav"):
                if (pkg / "audio" / candidate).exists():
                    audio_url = f"/audio_interact/api/sessions/{e['session_id']}/audio/{candidate}"
                    break
        elif e.get("legacy_dir"):
            if (Path(e["legacy_dir"]) / "full.wav").exists():
                audio_url = f"/audio_interact/api/sessions/{e['session_id']}/audio/full.wav"
        out.append({
            "session_id": e["session_id"],
            "day": e["day"],
            "created_at": _session_created_at(e),
            "duration_ms": duration_ms,
            "utterance_count": len(utterances) if utterances else 0,
            "source": m.get("source") or ("legacy" if e.get("legacy_dir") else "unknown"),
            "capture_point": m.get("capture_point") or "unknown",
            "device_id": m.get("device_id") or lm.get("device_id") or "",
            "texts": texts[:8],
            "has_package": bool(e.get("package_dir")),
            "audio_url": audio_url,
        })
    return out


@app.get("/api/sessions/{session_id}")
def get_session_route(session_id: str) -> dict[str, Any]:
    entries = _list_sessions(AUDIO_DATA_DIR, 500)
    match = next((e for e in entries if e["session_id"] == session_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")

    m = match.get("manifest") or {}
    lm = match.get("legacy_meta") or {}
    pkg = Path(match["package_dir"]) if match.get("package_dir") else None
    leg = Path(match["legacy_dir"]) if match.get("legacy_dir") else None

    # utterances: prefer events (richer), fallback to legacy
    # build skill_id fallback map from legacy session.json (indexed by utterance index)
    legacy_skill_map: dict[int, str] = {}
    for item in (lm.get("utterances") or []):
        idx = int(item.get("index") or 0)
        sk = str(item.get("skill_id") or "")
        if sk:
            legacy_skill_map[idx] = sk

    utterances: list[dict[str, Any]] = []
    if pkg:
        events = _load_events_for_session(pkg)
        vad_segs: dict[str, dict] = {}
        asr_finals: dict[str, dict] = {}
        robot_cmds: dict[str, dict] = {}
        for ev in events:
            etype = str(ev.get("type", ""))
            seg = str(ev.get("segment_id") or "")
            turn = str(ev.get("turn_id") or "")
            if etype == "vad.speech_start":
                vad_segs.setdefault(seg, {})["start_ms"] = int(ev.get("ts_ms", 0))
            elif etype == "vad.speech_end":
                vad_segs.setdefault(seg, {}).update({"end_ms": int(ev.get("ts_ms", 0)), "reason": ev.get("reason")})
            elif etype == "vad.segment":
                vad_segs[seg] = {"start_ms": int(ev.get("start_ms", 0)), "end_ms": int(ev.get("end_ms", 0)), "source": ev.get("source")}
            elif etype == "asr.final":
                asr_finals[seg] = {"text": ev.get("text", ""), "wake_status": ev.get("wake_status", ""),
                                    "status": ev.get("status", ""), "turn_id": turn,
                                    "skill_id": str(ev.get("skill_id") or ""),
                                    "audio_start_ms": ev.get("audio_start_ms"), "audio_end_ms": ev.get("audio_end_ms")}
            elif etype == "robot.command":
                robot_cmds[turn] = {"skill_id": (ev.get("command") or {}).get("skill_id") or ev.get("skill_id", ""),
                                     "action_task": ev.get("action_task"), "tts_text": ev.get("tts_text", ""),
                                     "action_error": ev.get("action_error", "")}
        all_segs = sorted(set(vad_segs) | set(asr_finals), key=lambda s: int(vad_segs.get(s, {}).get("start_ms") or asr_finals.get(s, {}).get("audio_start_ms") or 0))
        for i, seg in enumerate(all_segs):
            vad = vad_segs.get(seg, {})
            asr = asr_finals.get(seg, {})
            turn_id = asr.get("turn_id", "")
            cmd = robot_cmds.get(turn_id, {})
            # skill_id: robot.command event > asr.final event > legacy session.json fallback
            skill_id = cmd.get("skill_id") or asr.get("skill_id") or legacy_skill_map.get(i, "")
            utterances.append({
                "index": i,
                "segment_id": seg,
                "turn_id": turn_id,
                "vad_start_ms": vad.get("start_ms"),
                "vad_end_ms": vad.get("end_ms"),
                "vad_source": vad.get("source"),
                "text": asr.get("text", ""),
                "wake_status": asr.get("wake_status", ""),
                "status": asr.get("status", ""),
                "audio_start_ms": asr.get("audio_start_ms"),
                "audio_end_ms": asr.get("audio_end_ms"),
                "skill_id": skill_id,
                "action_task": cmd.get("action_task"),
                "tts_text": cmd.get("tts_text", ""),
                "action_error": cmd.get("action_error", ""),
            })
    else:
        for item in (lm.get("utterances") or []):
            start_s = float(item.get("vad_start_seconds") or 0)
            end_s = float(item.get("vad_end_seconds") or 0)
            utterances.append({
                "index": item.get("index", 0),
                "segment_id": f"seg_{item.get('index', 0):03d}",
                "turn_id": f"turn_{item.get('index', 0):03d}",
                "vad_start_ms": int(start_s * 1000),
                "vad_end_ms": int(end_s * 1000),
                "vad_source": "silero",
                "text": item.get("text", ""),
                "wake_status": item.get("wake_status", ""),
                "status": item.get("status", ""),
                "audio_start_ms": int(start_s * 1000),
                "audio_end_ms": int(end_s * 1000),
                "skill_id": item.get("skill_id", ""),
                "action_task": None,
                "tts_text": "",
                "action_error": "",
            })

    duration_ms = int(m.get("duration_ms") or round(float(lm.get("duration_seconds") or 0) * 1000))
    audio_url = None
    if pkg:
        for candidate in ("mic_proc_16k.wav", "mic_raw_16k.wav"):
            if (pkg / "audio" / candidate).exists():
                audio_url = f"/audio_interact/api/sessions/{session_id}/audio/{candidate}"
                break
    elif leg and (leg / "full.wav").exists():
        audio_url = f"/audio_interact/api/sessions/{session_id}/audio/full.wav"

    return {
        "session_id": session_id,
        "day": match["day"],
        "created_at": _session_created_at(match),
        "duration_ms": duration_ms,
        "source": m.get("source") or "legacy",
        "capture_point": m.get("capture_point") or "unknown",
        "device_id": m.get("device_id") or lm.get("device_id") or "",
        "proc_same_as_raw": m.get("proc_same_as_raw"),
        "audio_url": audio_url,
        "utterances": utterances,
        "manifest": m or None,
    }


@app.get("/api/sessions/{session_id}/audio/{filename}")
def get_session_audio(session_id: str, filename: str) -> FileResponse:
    safe_name = Path(filename).name
    # search both layouts
    for pattern in [f"sessions/*/{session_id}/audio/{safe_name}", f"recordings/*/{session_id}/{safe_name}",
                    f"recordings/*/{session_id}/full.wav"]:
        matches = list(AUDIO_DATA_DIR.glob(pattern))
        if matches:
            return FileResponse(str(matches[0]), media_type="audio/wav")
    raise HTTPException(status_code=404, detail="audio file not found")


@app.get("/api/tts/config")
def tts_config() -> dict[str, Any]:
    return {
        "model": TTS_MODEL,
        "default_voice": TTS_VOICE,
        "supported_voices": TTS_SUPPORTED_VOICES,
        "default_instructions": TTS_INSTRUCTIONS,
    }


@app.post("/api/tts")
def tts_speak(payload: dict[str, Any] = Body(...)) -> Response:
    """Synthesize given text to speech (same TTS params as the voice pipeline).

    Body: {"text": "...", "voice": "...", "instructions": "..."} -> audio/wav.
    """
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if not TTS_URL:
        raise HTTPException(status_code=503, detail="TTS not configured")
    voice = str(payload.get("voice") or TTS_VOICE).strip()
    if voice not in TTS_SUPPORTED_VOICES:
        raise HTTPException(status_code=400, detail=f"unsupported voice: {voice}")
    instructions = str(payload.get("instructions") or TTS_INSTRUCTIONS).strip()
    try:
        audio = _fetch_tts_audio(text, voice=voice, instructions=instructions)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts failed: {exc}")
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/api/audio/{name}")
def get_segment_audio(name: str) -> FileResponse:
    safe_name = Path(name).name
    target = (SEGMENTS_DIR / safe_name).resolve()
    if target.parent != SEGMENTS_DIR.resolve() or not target.exists():
        raise HTTPException(status_code=404, detail="audio not found")
    media_type = "audio/wav" if target.suffix.lower() == ".wav" else "application/octet-stream"
    return FileResponse(target, media_type=media_type, filename=safe_name, headers={"Cache-Control": "no-store"})


# Static web console (WonderEchoPro + browser mic/speaker + VAD_ASR).
# Mounted last so all /api/* and /ws/* routes take precedence; html=True serves
# index.html at the app root (behind the /audio_interact/ gateway prefix).
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="web")
