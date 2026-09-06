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
from typing import Annotated, Any, Awaitable, Callable

import requests
from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from runtime import metrics
from runtime.event_logger import EventLogger
from runtime.id_generator import ordinal_id, safe_id
from runtime.session_writer import (
    SessionWriter,
    classify_retention,
    emit_bargein_commit,
    emit_tts_cancel,
    emit_turn_result,
    emit_vad_end,
    emit_vad_start,
    finalize_streaming_session,
    open_streaming_journal,
    seconds_to_ms,
    write_segment_session_package as _write_segment_session_package,
    write_streaming_session_package as _write_streaming_session_package,
)
from settings import load_settings, save_settings
from wake_state import WakeDecision, WakeStateStore

# Observation queries that bypass the wake-word gate (they don't move the robot).
# Mirrors the aliases for inspect_scene / front_distance / camera_snapshot in registry.yaml.
_OBSERVATION_BYPASS_PHRASES: frozenset[str] = frozenset({
    # inspect_scene — 标准
    "前面有什么", "前方有什么", "前面是什么", "帮我看看前面",
    "前面有没有人", "有没有人", "前面有人吗", "前方有人吗",
    "看看前面有没有人", "前面有障碍物吗", "前面有什么障碍", "分析一下前面",
    # inspect_scene — 口语变体
    "前面有什么呀", "前面有什么啊", "前面有什么呢",
    "前面都有什么", "前面有些什么", "前方都有什么",
    "前面是什么呀", "前面是什么啊", "前方是什么",
    "帮我看一下前面", "看看前面有什么", "看一下前面有什么",
    "你看前面有什么", "前面有什么东西", "前方有什么东西",
    "前面情况怎么样", "前面怎么样",
    # camera_snapshot
    "看一下前面", "看看前面", "看一下前方", "看看前方", "拍照", "拍一张", "拍一下",
    # front_distance
    "前方距离", "前面距离", "测距", "看看距离", "前面有多远",
})

def _is_observation_bypass(text: str) -> bool:
    """Return True if the text is a pure observation query that bypasses the wake gate."""
    import re
    normalized = re.sub(r"[\s,，.。!！?？:：;；、_()（）【】-]+", "", text.lower().strip())
    return normalized in _OBSERVATION_BYPASS_PHRASES

_STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


# robot_sandbox was formerly named audio_recognition; accept the old env var as a fallback.
ROBOT_SANDBOX_URL = os.getenv("ROBOT_SANDBOX_URL") or os.getenv("AUDIO_RECOGNITION_URL", "http://robot-sandbox:8095")
COMMON_ASR_URL = os.getenv("COMMON_ASR_URL", "https://www.wangyutang.cn/common/api/asr/transcribe")
TTS_URL = os.getenv("AUDIO_TTS_URL", "https://www.wangyutang.cn/common/api/tts/speech")
TTS_STREAM_URL = os.getenv("AUDIO_TTS_STREAM_URL", "")  # 留空则自动派生为 TTS_URL + "/stream"
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
# Barge-in profile: while TTS is playing on the Pi, mic picks up residual echo,
# so require a higher speech probability + longer onset before firing speech_start.
SILERO_BARGEIN_THRESHOLD = float(os.getenv("SILERO_BARGEIN_THRESHOLD", "0.70"))
SILERO_BARGEIN_START_FRAMES = int(os.getenv("SILERO_BARGEIN_START_FRAMES", "5"))

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


# keyed by f"{session_id}:{turn_idx}" → tts_elapsed_ms
_tts_timing_store: dict[str, int] = {}


@dataclass
class _BroadcastHandle:
    """Registered by an active /ws/audio connection so HTTP handlers can push
    a cloud-triggered broadcast down that device's persistent socket, outside
    the normal ASR-turn pipeline (see /api/device/{device_id}/broadcast)."""
    speak: Callable[[str], Awaitable[None]]
    stop: Callable[[], None]
    is_busy: Callable[[], bool]


# keyed by device_id → handle for the currently-connected /ws/audio session.
DEVICE_BROADCAST_HANDLES: dict[str, _BroadcastHandle] = {}


def safe_write_segment_session_package(**kwargs: Any) -> str:
    try:
        return _write_segment_session_package(**kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] segment_session_package_failed: {exc}", flush=True)
        return ""


def _wav_to_pcm16(wav_bytes: bytes) -> bytes:
    """Strip the WAV container and return raw little-endian PCM16 frames."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            return wav_file.readframes(wav_file.getnframes())
    except (wave.Error, EOFError, struct.error):
        return b""


def safe_finalize_streaming_session(
    journal: SessionWriter, *, full_pcm: bytes, tts_pcm: bytes | None,
    extra_manifest: dict[str, Any] | None = None,
) -> str | None:
    try:
        return finalize_streaming_session(journal, full_pcm=full_pcm, tts_pcm=tts_pcm,
                                          extra_manifest=extra_manifest)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] journal_finalize_failed: {exc}", flush=True)
        return None


def save_session_recording(
    session_id: str,
    device_id: str,
    sample_rate: int,
    full_pcm: bytes,
    utterances: list[dict[str, Any]],
    *,
    journal: SessionWriter | None = None,
    tts_pcm: bytes = b"",
    extra_manifest: dict[str, Any] | None = None,
) -> str | None:
    """Persist a full streaming session in legacy and standard layouts.

    Legacy layout:
      <AUDIO_DATA_DIR>/recordings/<YYYY-MM-DD>/<session_id>/full.wav
      <AUDIO_DATA_DIR>/recordings/<YYYY-MM-DD>/<session_id>/session.json

    Standard replay/eval layout:
      <AUDIO_DATA_DIR>/sessions/<YYYY-MM-DD>/<session_id>/{manifest,audio,events,labels,replay,reports}

    When a live ``journal`` is supplied, its events were already appended in real
    time, so the standard package is completed by laying down the audio + manifest
    instead of rebuilding the event stream (which would duplicate every line).
    """
    if journal is not None:
        # Finalize the live journal even for an empty capture so the on-disk
        # session stays internally consistent (events + a valid, possibly-empty wav).
        standard_rel = safe_finalize_streaming_session(
            journal, full_pcm=full_pcm, tts_pcm=tts_pcm or None, extra_manifest=extra_manifest)
        if not full_pcm:
            return standard_rel
    elif not full_pcm:
        return None
    else:
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
metrics.init_tracing("audio-interact")
WAKE_STATES = WakeStateStore()


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    payload, content_type = metrics.metrics_payload()
    return Response(content=payload, media_type=content_type)
SILERO_VAD_MODEL: Any | None = None
SILERO_TORCH: Any | None = None

_sse_queues: list[asyncio.Queue] = []


def _sse_broadcast(payload: str) -> None:
    dead = []
    for q in _sse_queues:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _sse_queues.remove(q)


@app.get("/api/live-results")
async def live_results(request: Request):
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=20)
    _sse_queues.append(q)

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if q in _sse_queues:
                _sse_queues.remove(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "audio-interact",
        "vad": "silero_streaming",
        "sample_rate": STREAM_SAMPLE_RATE,
        "ts": time.time(),
    }


@dataclass
class _TurnState:
    """One queued utterance flowing through ASR -> route -> TTS off the recv loop.

    `cancelled` is flipped by a barge-in; the TTS stage checks it before every
    send so an interrupted turn stops emitting audio without unwinding actions
    that were already dispatched during routing.
    """
    gen: int
    wav_bytes: bytes
    vad_start: float | None = None
    vad_end: float | None = None
    reason: str | None = None
    cancelled: bool = False
    # Stable ledger IDs assigned at enqueue time so VAD edges, ASR, TTS and
    # barge-in events for this utterance all cross-reference the same turn.
    turn_idx: int = 0
    segment_id: str = "seg_001"
    # Set once routing returns, so a later barge-in knows whether this turn had
    # already dispatched a physical action that needs compensating (G9).
    envelope_id: str = ""
    action_dispatched: bool = False


@app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = str(uuid.uuid4())[:12]
    device_id = "turbopi-01"
    audio_buf = bytearray()
    stream_vad: StreamingSileroVad | None = None
    # Full un-truncated streaming audio + per-utterance VAD/ASR markers.
    session_full = bytearray()
    session_tts_pcm = bytearray()   # concatenated TTS PCM actually delivered → tts_ref
    session_utterances: list[dict[str, Any]] = []
    pending_start: float | None = None
    # Live interaction event journal (account one). Created on start_stream so
    # every VAD edge / ASR final / barge-in / TTS event is durable the instant it
    # happens, not rebuilt from memory at session end (crash-safe).
    journal: SessionWriter | None = None
    seg_seq = 0                       # next VAD segment / turn index
    last_speech_start_evt: str | None = None  # event_id of the most recent speech_start (barge-in cause)
    clock_sync: dict[str, Any] | None = None  # edge↔session offset from clock_probe (G8)
    route_enabled = True  # web模式 dispatches actions; VAD_ASR_TTS debug does ASR-only
    proto = 1             # start_stream may bump to 2 (streaming TTS + tts_state)

    # --- full-duplex turn pipeline (P0-A) -------------------------------------
    # The recv loop only feeds VAD and emits events; ASR/route/TTS for each
    # utterance run in a background worker so a multi-second turn never stalls
    # ingestion — the pre-condition for sub-second barge-in.
    send_lock = asyncio.Lock()
    turn_queue: asyncio.Queue[_TurnState] = asyncio.Queue()
    worker_task: asyncio.Task[None] | None = None
    turn_gen = 0
    current_turn: _TurnState | None = None
    server_tts_sending = False   # server is streaming TTS bytes right now
    pi_tts_playing = False       # Pi reports its speaker is active (covers playback tail)

    async def _send_text(payload: str) -> None:
        async with send_lock:
            await websocket.send_text(payload)

    async def _send_bytes(payload: bytes) -> None:
        async with send_lock:
            await websocket.send_bytes(payload)

    def _refresh_tts_active() -> None:
        # VAD switches to the echo-resistant profile whenever audio is on the wire
        # or still coming out of the Pi speaker.
        if stream_vad is not None:
            stream_vad.tts_active = server_tts_sending or pi_tts_playing

    def _tts_playing() -> bool:
        return server_tts_sending or pi_tts_playing

    def flush_session() -> str | None:
        nonlocal session_full, session_tts_pcm, session_utterances, pending_start, journal
        utts = list(session_utterances)
        for i, utt in enumerate(utts):
            tts_ms = _tts_timing_store.pop(f"{session_id}:{i}", None)
            if tts_ms is not None:
                utt["tts_elapsed_ms"] = tts_ms
        extra: dict[str, Any] = dict(classify_retention(utts, session_id))
        if clock_sync is not None:
            extra["clock"] = clock_sync
        metrics.record_session_flush(extra.get("retention_tier", ""))
        rel = save_session_recording(
            session_id, device_id, STREAM_SAMPLE_RATE, bytes(session_full), utts,
            journal=journal, tts_pcm=bytes(session_tts_pcm), extra_manifest=extra,
        )
        session_full = bytearray()
        session_tts_pcm = bytearray()
        session_utterances = []
        pending_start = None
        journal = None
        return rel

    def _broadcast_result(result: dict[str, Any]) -> None:
        _sse_broadcast(json.dumps({
            "type": "result",
            "text": result.get("text", ""),
            "wake_status": result.get("wake_status", ""),
            "skill_id": result.get("skill_id", ""),
            "tts_text": result.get("tts_text", ""),
            "status": result.get("status", ""),
        }, ensure_ascii=False))

    async def _stream_tts(text: str, ts: _TurnState, turn_idx: int) -> None:
        """proto>=2: synth sentence-by-sentence and push each WAV chunk immediately.

        Stops as soon as ts.cancelled flips (barge-in). The upstream synth thread
        finishes on its own in the background; we simply stop reading/forwarding.
        """
        nonlocal server_tts_sending
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()

        def _produce() -> None:
            try:
                for chunk in _iter_tts_stream(text):
                    loop.call_soon_threadsafe(q.put_nowait, chunk)
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        turn_id = ordinal_id("turn", turn_idx)
        tts_id = ordinal_id("tts", turn_idx)
        if journal is not None:
            journal.emit("tts", type="tts.request", tts_id=tts_id, turn_id=turn_id, text=text)
        await _send_text(json.dumps(
            {"type": "tts_begin", "turn": ts.gen, "turn_id": turn_id, "tts_id": tts_id, "session_id": session_id},
            ensure_ascii=False,
        ))
        server_tts_sending = True
        _refresh_tts_active()
        if journal is not None:
            journal.emit("tts", type="tts.begin", tts_id=tts_id, turn_id=turn_id)
        tts_started = time.time()
        first_chunk = True
        asyncio.ensure_future(loop.run_in_executor(None, _produce))
        try:
            while True:
                if ts.cancelled:
                    break
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    print(f"[WARN] ws_tts_stream_failed: {item}", flush=True)
                    break
                if ts.cancelled:
                    break
                if first_chunk:
                    first_chunk = False
                    first_ms = int((time.time() - tts_started) * 1000)
                    _tts_timing_store[f"{session_id}:{turn_idx}"] = first_ms
                    if journal is not None:
                        journal.emit("tts", type="tts.first_chunk", tts_id=tts_id, turn_id=turn_id,
                                     tts_first_ms=first_ms)
                session_tts_pcm.extend(_wav_to_pcm16(item))  # tee for tts_ref evidence
                await _send_bytes(item)
            if not ts.cancelled:
                await _send_bytes(b"")  # stream-end sentinel
                if journal is not None:
                    journal.emit("tts", type="tts.done", tts_id=tts_id, turn_id=turn_id,
                                 tts_elapsed_ms=int((time.time() - tts_started) * 1000))
        finally:
            server_tts_sending = False
            _refresh_tts_active()

    # --- cloud-triggered broadcast (outside the ASR-turn pipeline) -----------
    # Lets /api/device/{device_id}/broadcast push a TTS announcement down this
    # device's already-open socket, reusing the exact tts_begin/binary/sentinel
    # framing _stream_tts uses for real turns (the Pi-side player doesn't know
    # the difference), and the same tts_cancel message barge-in already uses
    # to kill in-flight playback (see wonderecho_listener.py's tts_cancel handler).
    broadcast_turn: _TurnState | None = None

    async def _broadcast_speak(text: str) -> None:
        nonlocal broadcast_turn
        turn = _TurnState(gen=0, wav_bytes=b"", turn_idx=-1, segment_id="broadcast", reason="broadcast")
        broadcast_turn = turn
        try:
            await _stream_tts(text, turn, 0)
        finally:
            if broadcast_turn is turn:
                broadcast_turn = None

    def _broadcast_stop() -> None:
        if broadcast_turn is not None:
            broadcast_turn.cancelled = True
        asyncio.ensure_future(_send_text(json.dumps({"type": "tts_cancel", "session_id": session_id})))

    DEVICE_BROADCAST_HANDLES[device_id] = _BroadcastHandle(
        speak=_broadcast_speak, stop=_broadcast_stop, is_busy=_tts_playing,
    )

    async def _run_turn(ts: _TurnState) -> None:
        nonlocal current_turn
        current_turn = ts
        try:
            turn_idx = ts.turn_idx
            turn_id = ordinal_id("turn", turn_idx)
            tts_id = ordinal_id("tts", turn_idx)
            await _send_text(json.dumps(
                {"type": "asr_started", "session_id": session_id, "device_id": device_id,
                 "turn_id": turn_id, "segment_id": ts.segment_id},
                ensure_ascii=False,
            ))
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _process, ts.wav_bytes, device_id, session_id, route_enabled)
            result["streaming_vad"] = "silero"
            tts_text = result.get("tts_text", "")
            ts.envelope_id = str(result.get("envelope_id") or "")
            ts.action_dispatched = bool(result.get("action_task"))

            # Journal the ASR final + routed command the moment routing returns,
            # so the fact is durable before any (cancellable) TTS is attempted.
            if journal is not None:
                emit_turn_result(
                    journal,
                    {
                        "vad_start_seconds": ts.vad_start,
                        "vad_end_seconds": ts.vad_end,
                        "reason": ts.reason,
                        "text": result.get("text", ""),
                        "wake_status": result.get("wake_status", ""),
                        "skill_id": result.get("skill_id", ""),
                        "status": result.get("status", ""),
                        "action_task": result.get("action_task"),
                        "tts_text": tts_text,
                        "action_error": result.get("action_error", ""),
                        "envelope_id": result.get("envelope_id", ""),
                        "asr_elapsed_ms": result.get("asr_elapsed_ms"),
                        "route_elapsed_ms": result.get("route_elapsed_ms"),
                    },
                    segment_id=ts.segment_id,
                    turn_id=turn_id,
                    emit_tts=False,
                )

            if proto < 2:
                # Legacy path: full synth, base64 in result + one binary frame.
                tts_bytes_ready: bytes | None = None
                if tts_text and TTS_URL:
                    try:
                        if journal is not None:
                            journal.emit("tts", type="tts.request", tts_id=tts_id, turn_id=turn_id, text=tts_text)
                        tts_started = time.time()
                        tts_bytes_ready = await loop.run_in_executor(None, _fetch_tts_audio, tts_text)
                        result["tts_audio_base64"] = base64.b64encode(tts_bytes_ready).decode("ascii")
                        if journal is not None:
                            journal.emit("tts", type="tts.audio_ready", tts_id=tts_id, turn_id=turn_id,
                                         tts_elapsed_ms=int((time.time() - tts_started) * 1000))
                    except Exception as exc:  # noqa: BLE001
                        print(f"[WARN] ws_tts_failed: {exc}", flush=True)
                await _send_text(json.dumps(result, ensure_ascii=False))
                _broadcast_result(result)
                if tts_bytes_ready and not ts.cancelled:
                    session_tts_pcm.extend(_wav_to_pcm16(tts_bytes_ready))
                    try:
                        await _send_bytes(tts_bytes_ready)
                        await _send_bytes(b"")
                    except Exception:
                        pass
            else:
                # Streaming path: result carries no audio; TTS follows as binary frames.
                await _send_text(json.dumps(result, ensure_ascii=False))
                _broadcast_result(result)
                if tts_text and TTS_URL and not ts.cancelled:
                    await _stream_tts(tts_text, ts, turn_idx)

            metrics.record_turn(result)
            first_ms = _tts_timing_store.get(f"{session_id}:{turn_idx}")
            if first_ms is not None:
                e2e = first_ms + int(result.get("asr_elapsed_ms") or 0) + int(result.get("route_elapsed_ms") or 0)
                metrics.record_tts_first_chunk(first_ms, e2e)
            session_utterances.append({
                "index": turn_idx,
                "vad_start_seconds": ts.vad_start,
                "vad_end_seconds": ts.vad_end,
                "reason": ts.reason,
                "text": result.get("text", ""),
                "wake_status": result.get("wake_status", ""),
                "skill_id": result.get("skill_id", ""),
                "status": result.get("status", ""),
                "action_task": result.get("action_task"),
                "tts_text": result.get("tts_text", ""),
                "action_error": result.get("action_error", ""),
                "envelope_id": result.get("envelope_id", ""),
                "asr_elapsed_ms": result.get("asr_elapsed_ms"),
                "route_elapsed_ms": result.get("route_elapsed_ms"),
                "barged_in": ts.cancelled,
            })
        finally:
            current_turn = None

    async def _turn_worker() -> None:
        while True:
            ts = await turn_queue.get()
            try:
                await _run_turn(ts)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] turn_worker_failed: {exc}", flush=True)
            finally:
                turn_queue.task_done()

    def _enqueue_turn(wav_bytes: bytes, vad_end: float | None, reason: str | None) -> None:
        nonlocal turn_gen, pending_start, seg_seq
        turn_gen += 1
        turn_queue.put_nowait(_TurnState(
            gen=turn_gen,
            wav_bytes=wav_bytes,
            vad_start=pending_start,
            vad_end=vad_end,
            reason=reason,
            turn_idx=seg_seq,
            segment_id=ordinal_id("seg", seg_seq),
        ))
        seg_seq += 1
        pending_start = None

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
                        etype = event.get("type")
                        if etype == "speech_start":
                            pending_start = event.get("offset_seconds")
                            seg_id = ordinal_id("seg", seg_seq)
                            during_tts = _tts_playing()
                            if journal is not None:
                                ss = emit_vad_start(
                                    journal,
                                    segment_id=seg_id,
                                    start_ms=seconds_to_ms(pending_start),
                                    confidence=event.get("probability"),
                                    during_tts=during_tts or None,
                                )
                                last_speech_start_evt = ss.get("event_id")
                            event["segment_id"] = seg_id
                            await _send_text(json.dumps(event, ensure_ascii=False))
                            # Barge-in: only fire while TTS is actually playing. Speech
                            # during ASR/route is a follow-up, not an interruption.
                            if current_turn is not None and during_tts:
                                current_turn.cancelled = True
                                cancel_turn_id = ordinal_id("turn", current_turn.turn_idx)
                                cancel_tts_id = ordinal_id("tts", current_turn.turn_idx)
                                # Ledger before side effect: the interruption is proven
                                # even if the cancel frame never reaches the Pi.
                                if journal is not None:
                                    now_ms = journal.now_ms()
                                    commit_ev = emit_bargein_commit(
                                        journal, ts_ms=now_ms, segment_id=seg_id,
                                        turn_id=ordinal_id("turn", seg_seq),
                                        tts_id=cancel_tts_id, cause=last_speech_start_evt,
                                    )
                                    emit_tts_cancel(
                                        journal, ts_ms=now_ms, tts_id=cancel_tts_id,
                                        turn_id=cancel_turn_id, reason="barge_in",
                                        cause=last_speech_start_evt,
                                    )
                                    metrics.record_bargein_commit(now_ms - seconds_to_ms(pending_start))
                                    # G9: interrupted motion gets an always-safe stop,
                                    # journaled as cancel_requested → compensated so the
                                    # physical outcome of the interruption is auditable.
                                    if current_turn.action_dispatched:
                                        req_ev = journal.emit(
                                            "runtime", type="task.cancel_requested",
                                            turn_id=cancel_turn_id,
                                            envelope_id=current_turn.envelope_id,
                                            cause=commit_ev.get("event_id"),
                                            compensation="stop",
                                        )
                                        asyncio.ensure_future(_compensate_cancelled_turn(
                                            journal, req_ev.get("event_id", ""),
                                            current_turn.envelope_id, device_id,
                                        ))
                                await _send_text(json.dumps(
                                    {"type": "tts_cancel", "session_id": session_id,
                                     "turn": current_turn.gen, "turn_id": cancel_turn_id,
                                     "tts_id": cancel_tts_id},
                                    ensure_ascii=False,
                                ))
                        elif etype == "speech_end" and event.get("wav_bytes"):
                            wav_bytes = event.pop("wav_bytes")
                            seg_id = ordinal_id("seg", seg_seq)
                            if journal is not None:
                                emit_vad_end(
                                    journal,
                                    segment_id=seg_id,
                                    start_ms=seconds_to_ms(pending_start),
                                    end_ms=seconds_to_ms(event.get("offset_seconds")),
                                    reason=event.get("reason"),
                                )
                            event["segment_id"] = seg_id
                            await _send_text(json.dumps(event, ensure_ascii=False))
                            _enqueue_turn(wav_bytes, event.get("offset_seconds"), event.get("reason"))
                        else:
                            await _send_text(json.dumps(event, ensure_ascii=False))
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
                    await _send_text(json.dumps({"type": "ready", "session_id": session_id}))

                elif frame_type == "start_stream":
                    session_id = str(frame.get("session_id") or session_id)
                    device_id = str(frame.get("device_id") or device_id)
                    route_enabled = bool(frame.get("route", True))
                    proto = int(frame.get("proto") or 1)
                    sample_rate = int(frame.get("sample_rate") or STREAM_SAMPLE_RATE)
                    if sample_rate != STREAM_SAMPLE_RATE:
                        await _send_text(json.dumps(
                            {
                                "type": "error",
                                "stage": "vad",
                                "message": f"stream sample_rate must be {STREAM_SAMPLE_RATE}",
                                "session_id": session_id,
                            },
                            ensure_ascii=False,
                        ))
                        continue
                    try:
                        stream_vad = StreamingSileroVad(session_id=session_id, device_id=device_id)
                    except Exception as exc:
                        await _send_text(json.dumps(
                            {
                                "type": "error",
                                "stage": "vad",
                                "message": str(exc),
                                "session_id": session_id,
                            },
                            ensure_ascii=False,
                        ))
                        continue
                    audio_buf.clear()
                    seg_seq = 0
                    # Open the live event journal now: the session dir + session.start
                    # exist before any audio, so a crash mid-session still leaves a
                    # complete, replayable ledger of everything up to the last event.
                    try:
                        journal = open_streaming_journal(
                            AUDIO_DATA_DIR,
                            session_id=session_id,
                            device_id=device_id,
                            sample_rate=STREAM_SAMPLE_RATE,
                            chunk_ms=int(1000 * 512 / STREAM_SAMPLE_RATE),
                        )
                    except Exception as exc:  # noqa: BLE001
                        journal = None
                        print(f"[WARN] journal_open_failed: {exc}", flush=True)
                    if worker_task is None or worker_task.done():
                        worker_task = asyncio.create_task(_turn_worker())
                    await _send_text(json.dumps(
                        {
                            "type": "stream_ready",
                            "session_id": session_id,
                            "device_id": device_id,
                            "vad": "silero",
                            "sample_rate": STREAM_SAMPLE_RATE,
                            "proto": proto,
                        },
                        ensure_ascii=False,
                    ))

                elif frame_type == "tts_state":
                    pi_tts_playing = bool(frame.get("playing"))
                    _refresh_tts_active()

                elif frame_type == "clock_probe":
                    # NTP-style probe (G8): echo t0, attach our session-clock t1.
                    # Answered inline in the recv loop so queueing skew stays minimal.
                    t1 = journal.now_ms() if journal is not None else 0
                    await _send_text(json.dumps(
                        {"type": "clock_probe_ack", "t0": frame.get("t0"), "t1": t1}
                    ))

                elif frame_type == "clock_sync":
                    clock_sync = {
                        "offset_ms": int(frame.get("offset_ms") or 0),
                        "rtt_ms": int(frame.get("rtt_ms") or 0),
                        "probes": int(frame.get("probes") or 0),
                        "method": "ws_probe_median",
                    }
                    if journal is not None:
                        journal.emit("runtime", type="clock.sync", **clock_sync)

                elif frame_type == "end":
                    if not audio_buf:
                        await _send_text(json.dumps(
                            {
                                "type": "error",
                                "message": "no audio received",
                                "session_id": session_id,
                            }
                        ))
                        continue

                    wav_bytes = bytes(audio_buf)
                    audio_buf.clear()
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, _process, wav_bytes, device_id, session_id, route_enabled)
                    tts_text = result.get("tts_text", "")
                    tts_bytes_ready = None
                    if tts_text and TTS_URL:
                        try:
                            tts_bytes_ready = await loop.run_in_executor(None, _fetch_tts_audio, tts_text)
                            result["tts_audio_base64"] = base64.b64encode(tts_bytes_ready).decode("ascii")
                        except Exception as exc:
                            print(f"[WARN] ws_tts_failed: {exc}", flush=True)
                    await _send_text(json.dumps(result, ensure_ascii=False))
                    _broadcast_result(result)
                    if tts_bytes_ready:
                        try:
                            await _send_bytes(tts_bytes_ready)
                            await _send_bytes(b"")
                        except Exception:
                            pass

                elif frame_type in {"stop_stream", "end_stream"}:
                    if stream_vad is not None:
                        final_wav = stream_vad.finish()
                        stream_vad = None
                        if final_wav:
                            # Leftover speech flushed at stop has no speech_end edge;
                            # record a segment so its turn still has a VAD anchor.
                            if journal is not None:
                                emit_vad_end(
                                    journal, segment_id=ordinal_id("seg", seg_seq),
                                    start_ms=seconds_to_ms(pending_start),
                                    end_ms=journal.now_ms(), reason="stop_stream",
                                )
                            _enqueue_turn(final_wav, None, "stop_stream")
                    # Drain all queued turns so their results land before stream_stopped.
                    await turn_queue.join()
                    saved = flush_session()
                    await _send_text(json.dumps({"type": "stream_stopped", "session_id": session_id, "recording": saved}))

    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if DEVICE_BROADCAST_HANDLES.get(device_id) is not None:
            handle = DEVICE_BROADCAST_HANDLES.get(device_id)
            if handle is not None and handle.speak is _broadcast_speak:
                DEVICE_BROADCAST_HANDLES.pop(device_id, None)
        if worker_task is not None:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass
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
    # Set True by the connection while TTS is being sent / played back on the Pi,
    # so onset detection switches to the echo-resistant barge-in profile.
    tts_active: bool = False

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
        # Echo-resistant profile while TTS plays; normal profile when idle.
        threshold = SILERO_BARGEIN_THRESHOLD if self.tts_active else SILERO_THRESHOLD
        start_frames = SILERO_BARGEIN_START_FRAMES if self.tts_active else SILERO_START_FRAMES
        is_speech = probability >= threshold
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
            if self.speech_count >= start_frames:
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
                        "offset_seconds": round(max(0.0, now_offset - start_frames * self.frame_samples / self.sample_rate), 3),
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


def _iter_tts_stream(text: str, *, voice: str | None = None, instructions: str | None = None):
    """从流式 TTS 端点逐句读取 [4B 长度][WAV] 分块，yield 每句 WAV bytes。"""
    stream_url = TTS_STREAM_URL or (TTS_URL.rstrip("/") + "/stream")
    payload = {
        "model": TTS_MODEL,
        "input": text,
        "voice": voice or TTS_VOICE,
        "language": "chinese",
        "instructions": instructions or TTS_INSTRUCTIONS,
        "response_format": "wav",
    }
    with requests.post(stream_url, json=payload, timeout=TTS_TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        buf = b""
        for raw in resp.iter_content(chunk_size=8192):
            buf += raw
            while len(buf) >= 4:
                length = struct.unpack(">I", buf[:4])[0]
                if length == 0:
                    return  # EOF sentinel
                if len(buf) < 4 + length:
                    break
                yield buf[4 : 4 + length]
                buf = buf[4 + length :]


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

    asr_elapsed = int((asr_done_at - started) * 1000)

    if not text:
        return {
            "type": "result",
            "session_id": session_id,
            "text": "",
            "route_text": "",
            "wake_status": "empty",
            "skill_id": "",
            "status": "empty",
            "asr_elapsed_ms": asr_elapsed,
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
            "asr_elapsed_ms": asr_elapsed,
            "elapsed_ms": elapsed_ms(started),
        }
    if not wake.should_route:
        if not _is_observation_bypass(text):
            return wake_only_result(session_id=session_id, text=text, wake=wake, started=started,
                                    asr_elapsed_ms=asr_elapsed)
        # Observation query: route without waking, keep device sleep state unchanged
        route_text = text.strip()
    else:
        route_text = wake.route_text

    try:
        resp = requests.post(
            f"{ROBOT_SANDBOX_URL}/api/recognize-text",
            json={
                "device_id": device_id,
                "text": route_text,
                "source": "audio-interact",
                "route_action": True,
                "raw": {
                    "asr": asr_payload,
                    "asr_text": text,
                    "wake_status": wake.status,
                    "wake_message": wake.message,
                    "capture_at": started,
                    "asr_done_at": asr_done_at,
                    "asr_elapsed_ms": asr_elapsed,
                },
            },
            timeout=ROUTE_TIMEOUT,
        )
        resp.raise_for_status()
        route_done_at = time.time()
        route = resp.json()
    except Exception as exc:
        return {
            "type": "result",
            "session_id": session_id,
            "text": text,
            "route_text": route_text,
            "wake_status": wake.status,
            "skill_id": "",
            "status": "route_error",
            "message": str(exc),
            "asr_elapsed_ms": asr_elapsed,
            "elapsed_ms": elapsed_ms(started),
        }

    return {
        "type": "result",
        "session_id": session_id,
        "text": text,
        "route_text": route_text,
        "wake_status": wake.status,
        "skill_id": route.get("skill_id", ""),
        "action_task": route.get("action_task"),
        "face_task": route.get("face_task"),
        "plan": route.get("plan"),
        "tts_text": str(route.get("tts_text") or ""),
        "envelope_id": str(route.get("envelope_id") or ""),
        "status": "ok",
        "asr_elapsed_ms": asr_elapsed,
        "route_elapsed_ms": int((route_done_at - asr_done_at) * 1000),
        "elapsed_ms": elapsed_ms(started),
    }


def _dispatch_bargein_compensation(device_id: str, cancelled_envelope_id: str) -> dict[str, Any]:
    """G9 safety compensation: a barged-in turn had already dispatched motion.

    `stop` is the only always-safe compensation for interrupted motion, so it is
    dispatched unconditionally. The user's interrupting utterance may itself
    route to stop moments later — stop is idempotent, doubling is harmless.
    """
    resp = requests.post(
        f"{ROBOT_SANDBOX_URL}/api/recognize-text",
        json={
            "device_id": device_id,
            "text": "停下",
            "source": "bargein_compensation",
            "route_action": True,
            "raw": {"compensates_envelope": cancelled_envelope_id},
        },
        timeout=ROUTE_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


async def _compensate_cancelled_turn(
    journal: SessionWriter, cancel_req_event_id: str, envelope_id: str, device_id: str,
) -> None:
    loop = asyncio.get_running_loop()
    try:
        comp = await loop.run_in_executor(None, _dispatch_bargein_compensation, device_id, envelope_id)
        journal.emit(
            "runtime", type="task.compensated", cause=cancel_req_event_id,
            envelope_id=envelope_id,
            compensation_envelope_id=str(comp.get("envelope_id") or ""),
            skill_id=str(comp.get("skill_id") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        journal.emit(
            "runtime", type="task.compensate_failed", cause=cancel_req_event_id,
            envelope_id=envelope_id, error=str(exc)[:200],
        )


def wake_only_result(*, session_id: str, text: str, wake: WakeDecision, started: float,
                     asr_elapsed_ms: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "result",
        "session_id": session_id,
        "text": text,
        "route_text": wake.route_text,
        "wake_status": wake.status,
        "skill_id": "",
        "status": wake.message or wake.status,
        "elapsed_ms": elapsed_ms(started),
    }
    if asr_elapsed_ms is not None:
        result["asr_elapsed_ms"] = asr_elapsed_ms
    return result


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


@app.post("/api/device/{device_id}/wake")
def wake_device_route(device_id: str, x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Activate a device's wake state from an external hardware signal (e.g., WonderEcho Pro UART).

    The Pi calls this endpoint when the WonderEcho Pro CL1302 DSP fires its wake word
    packet (aa 55 03 00 fb on /dev/ttyUSB0). This bypasses ASR-based wake word detection
    and uses the hardware's more reliable onboard DSP instead.
    """
    _require_token(x_audio_token)
    WAKE_STATES.activate(device_id)
    return {"ok": True, "device_id": device_id, "wake_status": "awake"}


@app.post("/api/device/{device_id}/broadcast")
async def broadcast_device_route(
    device_id: str, payload: dict[str, Any] = Body(...), x_audio_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Push a cloud-triggered TTS announcement to a device's speaker right now,
    outside the normal ASR-turn pipeline (e.g. WonderEcho Pro / turbopi-01).

    Requires the device's /ws/audio connection to be live — unlike the ESP32
    MQTT command queue, there is no offline buffering here (see
    docs/device-hub-command-timing.md for the ESP32 comparison). Body:
    {"text": "..."}.
    """
    _require_token(x_audio_token)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    handle = DEVICE_BROADCAST_HANDLES.get(device_id)
    if handle is None:
        raise HTTPException(status_code=503, detail=f"device {device_id} has no active /ws/audio connection")
    if handle.is_busy():
        raise HTTPException(status_code=409, detail="device is currently speaking; call broadcast/stop first")
    asyncio.create_task(handle.speak(text))
    return {"ok": True, "device_id": device_id, "status": "speaking"}


@app.post("/api/device/{device_id}/broadcast/stop")
async def broadcast_stop_route(device_id: str, x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Cancel an in-flight broadcast (or any TTS playback) on this device, the
    same way barge-in already does — sends tts_cancel down the socket, which
    wonderecho_listener.py's handler already kills the current pw-play for."""
    _require_token(x_audio_token)
    handle = DEVICE_BROADCAST_HANDLES.get(device_id)
    if handle is None:
        raise HTTPException(status_code=503, detail=f"device {device_id} has no active /ws/audio connection")
    handle.stop()
    return {"ok": True, "device_id": device_id, "status": "stopped"}


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
        if not _is_observation_bypass(text):
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
        seg_route_text = text.strip()
    else:
        seg_route_text = wake.route_text

    # robot_sandbox /api/command
    try:
        command = _call_robot_sandbox(
            text=seg_route_text,
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


@app.api_route("/dashboard", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/dashboard/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/dashboard/detail", methods=["GET", "HEAD"], include_in_schema=False)
def dashboard_sessions_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard/sessions", status_code=308)


@app.api_route("/dashboard/sessions", methods=["GET", "HEAD"], include_in_schema=False)
def dashboard_sessions_page() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "sessions.html"))


@app.api_route("/dashboard/vad_asr", methods=["GET", "HEAD"], include_in_schema=False)
def dashboard_golden_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard/golden", status_code=308)


@app.api_route("/dashboard/golden", methods=["GET", "HEAD"], include_in_schema=False)
def dashboard_golden_page() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "golden.html"))


@app.api_route("/dashboard/query", methods=["GET", "HEAD"], include_in_schema=False)
def dashboard_query_page() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "query.html"))


from golden import (
    iter_golden_session_cases as _iter_golden_session_cases,
    list_golden_cases,
    resolve_golden_audio_path as _resolve_golden_audio_path,
)


@app.get("/api/golden", include_in_schema=False)
def _get_golden_cases():
    return list_golden_cases()


@app.get("/api/golden/audio/{case_id}", include_in_schema=False)
def get_golden_audio(case_id: str):
    audio_path = _resolve_golden_audio_path(case_id)
    if audio_path is not None:
        return FileResponse(str(audio_path), media_type="audio/wav")
    raise HTTPException(status_code=404, detail="golden audio not found")


@app.post("/api/golden/{case_id}/execute", include_in_schema=False)
async def execute_golden_case(case_id: str):
    """Replay golden session audio with route=True via self WebSocket; returns per-turn results."""
    import wave as _wave

    import websockets

    safe_case_id = Path(case_id).name

    # Find the session directory via golden case index
    session_dir: Path | None = None
    for _case, _audio_path in _iter_golden_session_cases():
        if safe_case_id in {str(_case.get("case_id") or ""), str(_case.get("session_id") or "")}:
            if _audio_path is not None:
                session_dir = _audio_path.parent.parent
            break

    if session_dir is None:
        raise HTTPException(status_code=404, detail=f"golden case {safe_case_id!r} not found")

    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    audio_meta = manifest.get("audio") or {}
    audio_file = str(audio_meta.get("file") or "audio/mic_proc_16k.wav")
    audio_path = session_dir / audio_file
    sample_rate = int(audio_meta.get("sample_rate") or 16000)

    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="golden audio file not found")

    with _wave.open(str(audio_path), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())

    frame_samples = 512
    frame_bytes = frame_samples * 2
    silence_pad = b"\x00" * frame_bytes * 25
    full_pcm = pcm + silence_pad

    run_id = uuid.uuid4().hex[:8]
    replay_session_id = f"golden-exec-{safe_case_id}-{run_id}"
    replay_device_id = f"golden-exec-{run_id}"

    utterances: list[dict[str, Any]] = []
    pending_start_ms: int | None = None

    async with websockets.connect("ws://127.0.0.1:8097/ws/audio", ping_interval=None, open_timeout=20) as ws:
        await ws.send(json.dumps({
            "type": "start_stream",
            "session_id": replay_session_id,
            "device_id": replay_device_id,
            "sample_rate": sample_rate,
            "route": True,
        }))

        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if ready.get("type") not in {"ready", "stream_ready"}:
            raise HTTPException(status_code=502, detail=f"unexpected ws handshake: {ready}")

        for i in range(0, len(full_pcm), frame_bytes):
            frame = full_pcm[i: i + frame_bytes]
            if len(frame) < frame_bytes:
                frame = frame.ljust(frame_bytes, b"\x00")
            await ws.send(frame)

        await ws.send(json.dumps({"type": "stop_stream"}))

        deadline = asyncio.get_event_loop().time() + 90
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            msg_type = str(msg.get("type") or "")
            if msg_type == "speech_start":
                offset = msg.get("offset_seconds")
                pending_start_ms = int(float(offset) * 1000) if offset is not None else None
            elif msg_type == "stream_stopped":
                break
            elif "streaming_vad" in msg or ("text" in msg and "wake_status" in msg):
                offset = msg.get("offset_seconds")
                utterances.append({
                    "vad_start_ms": pending_start_ms,
                    "vad_end_ms": int(float(offset) * 1000) if offset is not None else None,
                    "text": msg.get("text", ""),
                    "wake_status": msg.get("wake_status", ""),
                    "status": msg.get("status", ""),
                    "skill_id": msg.get("skill_id", ""),
                    "action_task": msg.get("action_task"),
                    "tts_text": msg.get("tts_text", ""),
                })
                pending_start_ms = None

    return {
        "case_id": safe_case_id,
        "session_id": str(manifest.get("session_id") or ""),
        "device_id": str(manifest.get("device_id") or ""),
        "run_id": run_id,
        "turns": utterances,
    }


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
    def _sort_key(e: dict[str, Any]) -> str:
        created = str((e.get("manifest") or {}).get("created_at") or "").strip()
        if created:
            return created
        return f"{e.get('day', '')}|{e.get('session_id', '')}"

    results = sorted(found.values(), key=_sort_key, reverse=True)
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


def _extract_bargein_cases(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    """Reconstruct barge-in cases from the journal, decomposed by clock.

    Server clock: vad.speech_start → bargein.commit → tts.cancel.
    Edge clock (only if the Pi uploaded telemetry): bargein.tts_cancel_recv →
    playback.play_stop(killed) = post_cancel_tail. When a clock.sync event is
    present (G8 ws probes), edge timestamps are mapped onto the session clock
    (ts_session ≈ ts_edge + offset) to compute the true end-to-end cancel_delay;
    without it each single-clock half is still reported honestly.
    """
    ss_by_evt: dict[str, dict[str, Any]] = {}
    cancel_by_tts: dict[str, dict[str, Any]] = {}
    commits: list[dict[str, Any]] = []
    edge_recv: list[dict[str, Any]] = []
    edge_stop: list[dict[str, Any]] = []
    edge_duck: list[dict[str, Any]] = []
    edge_local_kill: list[dict[str, Any]] = []
    comp_events: list[dict[str, Any]] = []
    clock_offset: int | None = None
    for e in events:
        et = str(e.get("type", ""))
        if et == "vad.speech_start" and e.get("event_id"):
            ss_by_evt[str(e["event_id"])] = e
        elif et == "bargein.commit":
            commits.append(e)
        elif et == "tts.cancel":
            cancel_by_tts[str(e.get("tts_id") or "")] = e
        elif et == "bargein.tts_cancel_recv":
            edge_recv.append(e)
        elif et == "playback.play_stop":
            edge_stop.append(e)
        elif et == "bargein.local_duck":
            edge_duck.append(e)
        elif et == "bargein.playback_killed":
            edge_local_kill.append(e)
        elif et == "clock.sync" and e.get("offset_ms") is not None:
            clock_offset = int(e["offset_ms"])
        elif et in ("task.cancel_requested", "task.compensated", "task.compensate_failed"):
            comp_events.append(e)

    cancelled_turns: set[str] = set()
    cases: list[dict[str, Any]] = []
    for c in commits:
        tts_id = str(c.get("tts_id") or "")
        commit_ms = int(c.get("ts_ms", 0))
        ss = ss_by_evt.get(str(c.get("cause") or ""))
        speech_ms = int(ss.get("ts_ms", 0)) if ss else None
        cancel = cancel_by_tts.get(tts_id)
        if cancel and cancel.get("turn_id"):
            cancelled_turns.add(str(cancel["turn_id"]))
        recv = next((e for e in edge_recv if str(e.get("tts_id") or "") == tts_id), None)
        recv_ms = int(recv.get("ts_ms", 0)) if recv else None
        # The killed play_stop may precede tts_cancel_recv: on real hardware the
        # local energy path often wins the race and silences the speaker before
        # the server's cancel frame lands. Anchor the search on whichever edge
        # signal exists (local kill or cancel receipt) and take the nearest
        # killed stop in a ±10s window.
        anchors = [int(e.get("ts_ms", 0)) for e in edge_local_kill]
        if recv_ms is not None:
            anchors.append(recv_ms)
        stop = None
        if anchors:
            lo, hi = min(anchors) - 10_000, max(anchors) + 10_000
            stop = next((e for e in edge_stop
                         if e.get("reason") == "killed" and lo <= int(e.get("ts_ms", 0)) <= hi), None)
        stop_ms = int(stop.get("ts_ms", 0)) if stop else None
        local_kill = next((e for e in edge_local_kill
                           if stop_ms is not None and abs(int(e.get("ts_ms", 0)) - stop_ms) <= 100), None)
        stop_source = "local_energy" if local_kill else ("server_cancel" if stop else None)
        duck = max((e for e in edge_duck
                    if stop_ms is not None and int(e.get("ts_ms", 0)) <= stop_ms),
                   key=lambda e: int(e.get("ts_ms", 0)), default=None)
        # Tail: audible playback after the cancel frame arrived. If the local
        # path already silenced the speaker, the tail is 0 by definition.
        if recv_ms is not None and stop_ms is not None:
            tail = max(0, stop_ms - recv_ms)
        else:
            tail = None
        # Local reaction: user speech energy onset → speaker silent (edge clock).
        local_react = (stop_ms - int(duck.get("ts_ms", 0))) if (duck and stop_ms is not None) else None
        # G8 cross-clock: user-perceived speech→silence on the session timeline.
        # Negative means the edge killed playback before the server's (backdated)
        # VAD onset marker — report the local reaction time instead in that case.
        cancel_delay = None
        if clock_offset is not None and stop_ms is not None and speech_ms is not None:
            mapped = (stop_ms + clock_offset) - speech_ms
            cancel_delay = mapped if mapped >= 0 else local_react
        cases.append({
            "tts_id": tts_id,
            "turn_id": str(c.get("turn_id") or ""),
            "cancelled_turn_id": str(cancel.get("turn_id") or "") if cancel else "",
            "segment_id": str(c.get("segment_id") or ""),
            "speech_start_ms": speech_ms,
            "commit_ms": commit_ms,
            "cancel_ms": int(cancel.get("ts_ms", 0)) if cancel else None,
            # server clock
            "commit_delay_ms": (commit_ms - speech_ms) if speech_ms is not None else None,
            # edge clock
            "edge_cancel_recv_ms": recv_ms,
            "edge_play_stop_ms": stop_ms,
            "stop_source": stop_source,
            "post_cancel_tail_ms": tail,
            "local_react_ms": local_react,
            # cross-clock (needs clock.sync)
            "clock_offset_ms": clock_offset,
            "cancel_delay_ms": cancel_delay,
            "has_edge_telemetry": recv is not None or stop is not None,
            # G9: compensation chain for a cancelled turn that had dispatched motion
            "compensation": _compensation_for(comp_events, commit_evt=str(c.get("event_id") or "")),
        })
    return cases, cancelled_turns


def _compensation_for(comp_events: list[dict[str, Any]], *, commit_evt: str) -> dict[str, Any] | None:
    req = next((e for e in comp_events
                if e.get("type") == "task.cancel_requested" and str(e.get("cause") or "") == commit_evt), None)
    if req is None:
        return None
    req_id = str(req.get("event_id") or "")
    done = next((e for e in comp_events
                 if e.get("type") in ("task.compensated", "task.compensate_failed")
                 and str(e.get("cause") or "") == req_id), None)
    status = "pending"
    if done is not None:
        status = "compensated" if done.get("type") == "task.compensated" else "failed"
    return {
        "status": status,
        "envelope_id": str(req.get("envelope_id") or ""),
        "compensation": str(req.get("compensation") or "stop"),
        "compensation_envelope_id": str((done or {}).get("compensation_envelope_id") or ""),
        "error": str((done or {}).get("error") or ""),
    }


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


def _resolve_session_package_dir(session_id: str) -> Path | None:
    """Locate a session's standard package dir by id (dirs use safe_id names)."""
    safe = safe_id(session_id)
    matches = sorted(AUDIO_DATA_DIR.glob(f"sessions/*/{safe}"))
    for pkg in matches:
        if (pkg / "events").is_dir():
            return pkg
    return matches[0] if matches else None


# type prefix → journal file the edge event is merged into (mirrors SessionWriter).
_EDGE_EVENT_FILES = {
    "bargein": "bargein_runtime.jsonl",
    "tts": "tts_runtime.jsonl",
    "vad": "vad_runtime.jsonl",
    "capture": "runtime_events.jsonl",
    "playback": "runtime_events.jsonl",
}


@app.post("/api/sessions/{session_id}/edge-events")
def post_edge_events(
    session_id: str,
    payload: Annotated[Any, Body()],
    x_audio_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Merge Pi-side telemetry (play_start/stop/kill/duck/capture) into the ledger.

    The edge records only facts it alone knows, on its own monotonic clock, and
    uploads them on session end / reconnect. Merge is idempotent by ``event_id``
    so retries after a flaky uplink never double-count — the edge journal is the
    authoritative source for cancel_delay / post_cancel_tail metrics.
    """
    _require_token(x_audio_token)
    pkg = _resolve_session_package_dir(session_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")

    events = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        raise HTTPException(status_code=400, detail="expected {'events': [...]} or a JSON array")

    events_dir = pkg / "events"
    runtime_log = EventLogger(events_dir / "runtime_events.jsonl")
    known = {str(e.get("event_id")) for e in runtime_log.read_all() if e.get("event_id")}

    merged = skipped = 0
    for raw in events:
        if not isinstance(raw, dict) or "type" not in raw:
            skipped += 1
            continue
        eid = str(raw.get("event_id") or "")
        if not eid or eid in known:
            skipped += 1
            continue
        etype = str(raw["type"])
        category_file = _EDGE_EVENT_FILES.get(etype.split(".", 1)[0], "runtime_events.jsonl")
        event = {**raw, "session_id": session_id, "source": raw.get("source") or "edge"}
        if category_file != "runtime_events.jsonl":
            EventLogger(events_dir / category_file).emit(**event)
        runtime_log.emit(**event)
        known.add(eid)
        merged += 1

    if merged:
        # Edge telemetry completes the cross-clock picture — record the SLO
        # metrics once per successful merge (retries merge 0 and record nothing).
        try:
            cases, _ = _extract_bargein_cases(_load_events_for_session(pkg))
            for case in cases:
                if case.get("has_edge_telemetry"):
                    metrics.record_bargein_case(case)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] bargein_metrics_failed: {exc}", flush=True)

    return {"session_id": session_id, "merged": merged, "skipped": skipped}


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
    bargein_cases: list[dict[str, Any]] = []
    cancelled_turns: set[str] = set()
    if pkg:
        events = _load_events_for_session(pkg)
        bargein_cases, cancelled_turns = _extract_bargein_cases(events)
        vad_segs: dict[str, dict] = {}
        asr_finals: dict[str, dict] = {}
        robot_cmds: dict[str, dict] = {}
        tts_ready: dict[str, int | None] = {}
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
                                    "audio_start_ms": ev.get("audio_start_ms"), "audio_end_ms": ev.get("audio_end_ms"),
                                    "asr_elapsed_ms": ev.get("asr_elapsed_ms")}
            elif etype == "robot.command":
                robot_cmds[turn] = {"skill_id": (ev.get("command") or {}).get("skill_id") or ev.get("skill_id", ""),
                                     "action_task": ev.get("action_task"), "tts_text": ev.get("tts_text", ""),
                                     "action_error": ev.get("action_error", ""),
                                     "envelope_id": ev.get("envelope_id", ""),
                                     "route_elapsed_ms": ev.get("route_elapsed_ms")}
            elif etype in ("tts.audio_ready", "tts.done"):
                # audio_ready = proto<2 whole-synth; done = streaming path end.
                tts_ready[turn] = ev.get("tts_elapsed_ms")
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
                "envelope_id": cmd.get("envelope_id", ""),
                "asr_elapsed_ms": asr.get("asr_elapsed_ms"),
                "route_elapsed_ms": cmd.get("route_elapsed_ms"),
                "tts_elapsed_ms": tts_ready.get(turn_id),
                "barged_in": turn_id in cancelled_turns,
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
        "bargein": bargein_cases,
        "manifest": m or None,
    }


def _parse_iso_epoch(value: str) -> float:
    try:
        from datetime import datetime
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except Exception:
        return 0.0


def _fetch_envelope_detail(envelope_id: str) -> dict[str, Any] | None:
    try:
        payload = requests.get(f"{ROBOT_SANDBOX_URL}/api/envelopes/{envelope_id}", timeout=3).json()
        detail = payload.get("envelope") or payload
        return detail if isinstance(detail, dict) else None
    except Exception:
        return None


def _match_envelopes_for_trace(session_epoch: float, duration_ms: int, utterances: list[dict[str, Any]]) -> dict[str, Any]:
    """Join robot_sandbox decision envelopes onto session turns.

    Preferred path: the exact envelope_id recorded on the turn at route time.
    Fallback for older sessions without envelope_id: match by identical
    transcript within the session's wall-clock window.
    """
    matched: dict[str, Any] = {}
    for u in utterances:
        envelope_id = str(u.get("envelope_id") or "")
        turn_id = str(u.get("turn_id") or "")
        if envelope_id and turn_id:
            detail = _fetch_envelope_detail(envelope_id)
            if detail:
                matched[turn_id] = detail
    wanted: dict[str, list[str]] = {}
    for u in utterances:
        text = str(u.get("text") or "").strip()
        turn_id = str(u.get("turn_id") or "")
        if turn_id in matched:
            continue
        if text and (u.get("skill_id") or u.get("status") == "ok"):
            wanted.setdefault(text, []).append(turn_id)
    if not wanted or session_epoch <= 0:
        return matched
    lo = session_epoch - 30
    hi = session_epoch + duration_ms / 1000 + 300
    try:
        rows = requests.get(f"{ROBOT_SANDBOX_URL}/api/envelopes", params={"limit": 200}, timeout=3).json().get("envelopes") or []
        for row in rows:
            transcript = str(row.get("transcript") or "").strip()
            t_created = float(row.get("t_created") or 0)
            turn_ids = wanted.get(transcript)
            if not turn_ids or not (lo <= t_created <= hi):
                continue
            detail = _fetch_envelope_detail(str(row.get("envelope_id") or "")) or row
            # oldest unclaimed turn first so repeated transcripts pair up in order
            for turn_id in turn_ids:
                if turn_id and turn_id not in matched:
                    matched[turn_id] = detail
                    break
    except Exception:
        return matched
    return matched


@app.get("/api/sessions/{session_id}/trace")
def get_session_trace_route(session_id: str) -> dict[str, Any]:
    """Full timeline for one session: raw ordered events, per-turn pipeline
    stages, and joined robot_sandbox decision envelopes."""
    detail = get_session_route(session_id)
    events: list[dict[str, Any]] = []
    entries = _list_sessions(AUDIO_DATA_DIR, 500)
    match = next((e for e in entries if e["session_id"] == session_id), None)
    if match and match.get("package_dir"):
        events = _load_events_for_session(Path(match["package_dir"]))
        events.sort(key=lambda ev: (float(ev.get("ts_ms") or 0), str(ev.get("type") or "")))
    session_epoch = _parse_iso_epoch(str(detail.get("created_at") or ""))
    envelopes = _match_envelopes_for_trace(session_epoch, int(detail.get("duration_ms") or 0), detail.get("utterances") or [])
    return {**detail, "events": events, "envelopes": envelopes}


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
