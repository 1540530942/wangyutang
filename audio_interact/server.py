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
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from settings import load_settings, save_settings
from wake_state import WakeDecision, WakeStateStore


AUDIO_RECOGNITION_URL = os.getenv("AUDIO_RECOGNITION_URL", "http://audio-recognition:8095")
COMMON_ASR_URL = os.getenv("COMMON_ASR_URL", "https://www.wangyutang.cn/common/api/asr/transcribe")
TTS_URL = os.getenv("AUDIO_TTS_URL", "https://www.wangyutang.cn/common/api/tts/speech")
TTS_MODEL = os.getenv("AUDIO_TTS_MODEL", "qwen3-tts-12hz-1.7b-customvoice")
TTS_VOICE = os.getenv("AUDIO_TTS_VOICE", "vivian")
TTS_INSTRUCTIONS = os.getenv("AUDIO_TTS_INSTRUCTIONS", "用清新自然、甜美温柔的语气说，声音明亮亲切，语调轻快柔和")
TTS_TIMEOUT = int(os.getenv("AUDIO_TTS_TIMEOUT", "30"))
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

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            if msg.get("bytes"):
                if stream_vad is not None:
                    events = stream_vad.feed(msg["bytes"])
                    for event in events:
                        if event.get("type") == "speech_end" and event.get("wav_bytes"):
                            wav_bytes = event.pop("wav_bytes")
                            await websocket.send_text(json.dumps(event, ensure_ascii=False))
                            await websocket.send_text(
                                json.dumps(
                                    {"type": "asr_started", "session_id": session_id, "device_id": device_id},
                                    ensure_ascii=False,
                                )
                            )
                            loop = asyncio.get_event_loop()
                            result = await loop.run_in_executor(None, _process, wav_bytes, device_id, session_id)
                            result["streaming_vad"] = "silero"
                            await websocket.send_text(json.dumps(result, ensure_ascii=False))
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
                    result = await loop.run_in_executor(None, _process, wav_bytes, device_id, session_id)
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
                            result = await loop.run_in_executor(None, _process, final_wav, device_id, session_id)
                            result["streaming_vad"] = "silero"
                            await websocket.send_text(json.dumps(result, ensure_ascii=False))
                            tts_text = result.get("tts_text", "")
                            if tts_text and TTS_URL:
                                asyncio.create_task(_push_tts(websocket, tts_text))
                    await websocket.send_text(json.dumps({"type": "stream_stopped", "session_id": session_id}))

    except WebSocketDisconnect:
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


def _fetch_tts_audio(text: str) -> bytes:
    payload = {
        "model": TTS_MODEL,
        "input": text,
        "voice": TTS_VOICE,
        "language": "chinese",
        "instructions": TTS_INSTRUCTIONS,
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


def _process(wav_bytes: bytes, device_id: str, session_id: str) -> dict[str, Any]:
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
    if not wake.should_route:
        return wake_only_result(session_id=session_id, text=text, wake=wake, started=started)

    try:
        resp = requests.post(
            f"{AUDIO_RECOGNITION_URL}/api/recognize-text",
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
        f"{AUDIO_RECOGNITION_URL}/api/command",
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
        return {
            "ok": False,
            "session_id": sess,
            "stage": "asr",
            "message": str(exc),
            "elapsed_ms": elapsed_ms(started),
        }

    if not text:
        return {
            "ok": True,
            "session_id": sess,
            "text": "",
            "wake_status": "empty",
            "command": None,
            "tts_text": "",
            "tts_audio_base64": None,
            "audio_url": audio_url,
            "elapsed_ms": elapsed_ms(started),
        }

    # Wake-state gate
    wake = WAKE_STATES.decide(device_id, text)
    if not wake.should_route:
        return {
            "ok": True,
            "session_id": sess,
            "text": text,
            "wake_status": wake.status,
            "command": None,
            "tts_text": "",
            "tts_audio_base64": None,
            "audio_url": audio_url,
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
        return {
            "ok": False,
            "session_id": sess,
            "text": text,
            "wake_status": wake.status,
            "command": None,
            "tts_text": "",
            "tts_audio_base64": None,
            "audio_url": audio_url,
            "message": str(exc),
            "elapsed_ms": elapsed_ms(started),
        }

    tts_text = str(command.get("tts_text") or "")

    # Fetch TTS audio for Pi local playback
    tts_audio_b64: str | None = None
    if tts_text and TTS_URL:
        try:
            audio_bytes = _fetch_tts_audio(tts_text)
            tts_audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        except Exception as exc:
            print(f"[WARN] segment tts_failed: {exc}", flush=True)

    return {
        "ok": True,
        "session_id": sess,
        "text": text,
        "wake_status": wake.status,
        "command": command,
        "tts_text": tts_text,
        "tts_audio_base64": tts_audio_b64,
        "audio_url": audio_url,
        "elapsed_ms": elapsed_ms(started),
    }


@app.get("/api/audio/{name}")
def get_segment_audio(name: str) -> FileResponse:
    safe_name = Path(name).name
    target = (SEGMENTS_DIR / safe_name).resolve()
    if target.parent != SEGMENTS_DIR.resolve() or not target.exists():
        raise HTTPException(status_code=404, detail="audio not found")
    media_type = "audio/wav" if target.suffix.lower() == ".wav" else "application/octet-stream"
    return FileResponse(target, media_type=media_type, filename=safe_name, headers={"Cache-Control": "no-store"})
