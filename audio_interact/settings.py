from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


DATA_DIR = Path(os.getenv("AUDIO_INTERACT_DATA_DIR", "/app/data"))
SETTINGS_FILE = DATA_DIR / "settings.json"
_LOCK = threading.Lock()


class AudioSettings(BaseModel):
    input_mode: str = Field("wonderechopro", pattern="^(web_input|wonderechopro|vad_asr)$")
    manual_recording_enabled: bool = False
    pi_speaker_volume: int = Field(80, ge=0, le=100)


DEFAULT_SETTINGS: dict[str, Any] = {
    "input_mode": "wonderechopro",
    "manual_recording_enabled": False,
    "pi_speaker_volume": 80,
}


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict[str, Any]:
    _ensure_data_dir()
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)
    merged = {**DEFAULT_SETTINGS, **data}
    return AudioSettings(**merged).model_dump()


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    _ensure_data_dir()
    with _LOCK:
        current = load_settings()
        normalized = AudioSettings(**{**current, **updates}).model_dump()
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)
    return normalized
