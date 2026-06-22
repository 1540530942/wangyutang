from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

AUDIO_RECOGNITION_URL = os.getenv("AUDIO_RECOGNITION_URL", "http://audio-recognition:8095")
COMMON_ASR_URL = os.getenv("COMMON_ASR_URL", "https://www.wangyutang.cn/common/api/asr/transcribe")
ASR_TIMEOUT = int(os.getenv("ASR_TIMEOUT", "60"))
ROUTE_TIMEOUT = int(os.getenv("ROUTE_TIMEOUT", "90"))

app = FastAPI(title="Audio Interact Service", version="0.1.0")


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
                t = frame.get("type", "")

                if t == "start":
                    session_id = str(frame.get("session_id") or session_id)
                    device_id = str(frame.get("device_id") or device_id)
                    audio_buf.clear()
                    await websocket.send_text(json.dumps({"type": "ready", "session_id": session_id}))

                elif t == "end":
                    if not audio_buf:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "no audio received",
                            "session_id": session_id,
                        }))
                        continue

                    wav_bytes = bytes(audio_buf)
                    audio_buf.clear()
                    _sid = session_id
                    _did = device_id

                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, _process, wav_bytes, _did, _sid)
                    await websocket.send_text(json.dumps(result, ensure_ascii=False))

    except WebSocketDisconnect:
        pass


def _process(wav_bytes: bytes, device_id: str, session_id: str) -> dict[str, Any]:
    t0 = time.time()

    # ── 1. ASR ──────────────────────────────────────────────────────────────
    try:
        resp = requests.post(
            COMMON_ASR_URL,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"language": "zh"},
            timeout=ASR_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
    except Exception as exc:
        return {
            "type": "error",
            "session_id": session_id,
            "stage": "asr",
            "message": str(exc),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    if not text:
        return {
            "type": "result",
            "session_id": session_id,
            "text": "",
            "skill_id": "",
            "status": "empty",
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    # ── 2. Route + dispatch ─────────────────────────────────────────────────
    try:
        resp = requests.post(
            f"{AUDIO_RECOGNITION_URL}/api/recognize-text",
            json={
                "device_id": device_id,
                "text": text,
                "source": "audio-interact",
                "route_action": True,
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
            "skill_id": "",
            "status": "route_error",
            "message": str(exc),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    return {
        "type": "result",
        "session_id": session_id,
        "text": text,
        "skill_id": route.get("skill_id", ""),
        "action_task": route.get("action_task"),
        "face_task": route.get("face_task"),
        "plan": route.get("plan"),
        "status": "ok",
        "elapsed_ms": int((time.time() - t0) * 1000),
    }
