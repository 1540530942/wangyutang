"""Replay helpers for golden audio sessions."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from ci_tests.golden_sessions import GoldenSession, read_pcm16_wav


WS_PATH = "/audio_interact/ws/audio"
FRAME_SAMPLES = 512
SILENCE_PAD_FRAMES = 25


def _silence_frame() -> bytes:
    return b"\x00" * (FRAME_SAMPLES * 2)


def _offset_ms(msg: dict[str, Any]) -> int | None:
    offset = msg.get("offset_seconds")
    if offset is None:
        return None
    return int(float(offset) * 1000)


async def replay_session_audio(
    base_ws_url: str,
    session: GoldenSession,
    *,
    route: bool = False,
    device_id: str | None = None,
    session_id_prefix: str = "ci",
) -> list[dict[str, Any]]:
    """Stream one full session audio file and collect per-utterance results.

    Keep route=False for CI unless the target robot sandbox is explicitly
    configured to dry-run; audio_interact route=True can create real action
    tasks in production.
    """
    import websockets

    pcm = read_pcm16_wav(session.audio_path, sample_rate=session.sample_rate)
    full_pcm = pcm + (_silence_frame() * SILENCE_PAD_FRAMES)
    frame_size = FRAME_SAMPLES * 2
    ws_url = base_ws_url + WS_PATH
    run_id = uuid.uuid4().hex[:8]
    replay_session_id = f"{session_id_prefix}-{session.session_id}-{run_id}"
    # Unique device_id per run so WakeStateStore starts fresh (avoids stale
    # awake state from a previous run bleeding into this one).
    replay_device_id = device_id or f"{session_id_prefix}-test-{run_id}"

    utterances: list[dict[str, Any]] = []
    pending_start_ms: int | None = None

    async with websockets.connect(ws_url, ping_interval=None, open_timeout=20) as ws:
        await ws.send(json.dumps({
            "type": "start_stream",
            "session_id": replay_session_id,
            "device_id": replay_device_id,
            "sample_rate": session.sample_rate,
            "route": route,
        }))

        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        assert ready.get("type") in {"ready", "stream_ready"}, f"expected ready, got {ready}"

        for i in range(0, len(full_pcm), frame_size):
            frame = full_pcm[i: i + frame_size]
            if len(frame) < frame_size:
                frame = frame.ljust(frame_size, b"\x00")
            await ws.send(frame)

        await ws.send(json.dumps({"type": "stop_stream"}))

        deadline = asyncio.get_event_loop().time() + 60
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
                pending_start_ms = _offset_ms(msg)
            elif msg_type == "stream_stopped":
                break
            elif "streaming_vad" in msg or ("text" in msg and "wake_status" in msg):
                utterances.append({
                    "vad_start_ms": pending_start_ms,
                    "vad_end_ms": _offset_ms(msg),
                    "text": msg.get("text", ""),
                    "route_text": msg.get("route_text", ""),
                    "wake_status": msg.get("wake_status", ""),
                    "status": msg.get("status", ""),
                    "skill_id": msg.get("skill_id", ""),
                    "action_task": msg.get("action_task"),
                    "action_error": msg.get("action_error", ""),
                    "face_task": msg.get("face_task"),
                    "plan": msg.get("plan"),
                    "raw": msg,
                })
                pending_start_ms = None

    return utterances
