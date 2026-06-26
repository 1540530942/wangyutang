from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from wake_state import WakeDecision, WakeStateStore


AUDIO_RECOGNITION_URL = os.getenv("AUDIO_RECOGNITION_URL", "http://audio-recognition:8095")
COMMON_ASR_URL = os.getenv("COMMON_ASR_URL", "https://www.wangyutang.cn/common/api/asr/transcribe")
ASR_TIMEOUT = int(os.getenv("ASR_TIMEOUT", "60"))
ROUTE_TIMEOUT = int(os.getenv("ROUTE_TIMEOUT", "90"))

app = FastAPI(title="Audio Interact Service", version="0.2.0")
WAKE_STATES = WakeStateStore()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "audio-interact", "ts": time.time()}


@app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = str(uuid.uuid4())[:12]
    device_id = "turbopi-01"
    audio_buf = bytearray()

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            if msg.get("bytes"):
                audio_buf.extend(msg["bytes"])

            elif msg.get("text"):
                frame = json.loads(msg["text"])
                frame_type = frame.get("type", "")

                if frame_type == "start":
                    session_id = str(frame.get("session_id") or session_id)
                    device_id = str(frame.get("device_id") or device_id)
                    audio_buf.clear()
                    await websocket.send_text(json.dumps({"type": "ready", "session_id": session_id}))

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

    except WebSocketDisconnect:
        pass


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
