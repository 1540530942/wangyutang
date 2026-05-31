from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from face_render import FaceState as RenderFaceState
from face_render import draw_face
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

Emotion = Literal["neutral", "happy", "joy", "sad", "angry"]
FaceStyle = Literal["mochi", "bunny", "star", "panda"]
MIN_FACE_ACTION_DURATION_MS = 5000
EMOTION_RGB = {
    "neutral": ("#ffffff", 0.25),
    "happy": ("#00ff88", 0.75),
    "joy": ("#ffff00", 0.9),
    "sad": ("#0066ff", 0.5),
    "angry": ("#ff2200", 0.9),
}

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

app = FastAPI(title="Smile Face", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class FaceState(BaseModel):
    emotion: Emotion = "neutral"
    style: FaceStyle = "mochi"
    intensity: float = Field(0.65, ge=0, le=1)
    speaking_until: float = 0
    mouth_open_until: float = 0
    blink_nonce: int = 0
    message: str = ""
    rgb_mode: str = "emotion"
    rgb_color: str = "#ffffff"
    rgb_intensity: float = Field(0.25, ge=0, le=1)
    rgb_updated_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    source: str = "boot"


class EmotionRequest(BaseModel):
    emotion: Emotion
    intensity: float = Field(0.75, ge=0, le=1)
    duration_ms: int = Field(MIN_FACE_ACTION_DURATION_MS, ge=0, le=60000)
    source: str = "api"
    message: str = ""


class StyleRequest(BaseModel):
    style: FaceStyle
    source: str = "api"
    message: str = ""


class SpeakRequest(BaseModel):
    text: str = ""
    duration_ms: int = Field(0, ge=0, le=60000)
    emotion: Emotion | None = None
    source: str = "api"


class MouthRequest(BaseModel):
    open: bool = True
    duration_ms: int = Field(MIN_FACE_ACTION_DURATION_MS, ge=0, le=60000)
    source: str = "api"


state = FaceState()


def now() -> float:
    return time.time()


def apply_emotion_rgb(emotion: Emotion, current: float | None = None) -> None:
    color, intensity = EMOTION_RGB[emotion]
    state.rgb_mode = "emotion"
    state.rgb_color = color
    state.rgb_intensity = intensity
    state.rgb_updated_at = current or now()


def snapshot() -> dict[str, object]:
    current = now()
    data = state.model_dump()
    data["now"] = current
    data["speaking"] = state.speaking_until > current
    data["mouth_open"] = state.mouth_open_until > current
    return data


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, object]:
    current = now()
    return {
        "status": "ok",
        "service": "smile_face",
        "mode": "browser-canvas",
        "emotion": state.emotion,
        "style": state.style,
        "speaking": state.speaking_until > current,
        "updated_at": state.updated_at,
    }


@app.get("/api/state")
def get_state() -> dict[str, object]:
    return snapshot()


@app.get("/api/face/render.jpg")
def render_face_jpeg() -> Response:
    current = now()
    render_state = RenderFaceState(
        emotion=state.emotion,
        style=state.style,
        intensity=state.intensity,
        speaking_until=state.speaking_until,
        mouth_open_until=state.mouth_open_until,
        blink_nonce=state.blink_nonce,
        message=state.message,
        now=current,
    )
    image = draw_face(render_state, frame_started=0.0, blink=0.0)
    stream = io.BytesIO()
    image.save(stream, "JPEG", quality=88)
    return Response(
        content=stream.getvalue(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/face/emotion")
def set_emotion(payload: EmotionRequest) -> dict[str, object]:
    current = now()
    state.emotion = payload.emotion
    state.intensity = payload.intensity
    state.message = payload.message
    state.source = payload.source
    apply_emotion_rgb(payload.emotion, current)
    state.updated_at = current
    if payload.duration_ms:
        state.mouth_open_until = max(state.mouth_open_until, current + payload.duration_ms / 1000)
    return {"ok": True, "state": snapshot()}


@app.post("/api/face/style")
def set_style(payload: StyleRequest) -> dict[str, object]:
    state.style = payload.style
    state.message = payload.message
    state.source = payload.source
    state.updated_at = now()
    return {"ok": True, "state": snapshot()}


@app.post("/api/face/speak")
def speak(payload: SpeakRequest) -> dict[str, object]:
    current = now()
    duration_ms = max(MIN_FACE_ACTION_DURATION_MS, payload.duration_ms or min(8000, len(payload.text) * 180))
    if payload.emotion is not None:
        state.emotion = payload.emotion
        state.intensity = max(state.intensity, 0.8)
        apply_emotion_rgb(payload.emotion, current)
    state.speaking_until = current + duration_ms / 1000
    state.mouth_open_until = state.speaking_until
    state.message = payload.text
    state.source = payload.source
    state.updated_at = current
    return {"ok": True, "state": snapshot()}


@app.post("/api/face/mouth")
def mouth(payload: MouthRequest) -> dict[str, object]:
    current = now()
    state.mouth_open_until = current + payload.duration_ms / 1000 if payload.open else 0
    state.source = payload.source
    state.updated_at = current
    return {"ok": True, "state": snapshot()}


@app.post("/api/face/blink")
def blink() -> dict[str, object]:
    state.blink_nonce += 1
    state.updated_at = now()
    state.source = "api"
    return {"ok": True, "state": snapshot()}


@app.post("/api/face/reset")
def reset() -> dict[str, object]:
    global state
    current = now()
    state = FaceState(source="api", updated_at=current, rgb_updated_at=current)
    return {"ok": True, "state": snapshot()}


@app.post("/api/face/style/{style}")
def set_style_path(style: str) -> dict[str, object]:
    if style not in {"mochi", "bunny", "star", "panda"}:
        raise HTTPException(status_code=404, detail="unknown style")
    return set_style(StyleRequest(style=style))


@app.post("/api/face/{emotion}")
def set_emotion_path(emotion: str) -> dict[str, object]:
    if emotion not in {"neutral", "happy", "joy", "sad", "angry"}:
        raise HTTPException(status_code=404, detail="unknown emotion")
    return set_emotion(EmotionRequest(emotion=emotion))
