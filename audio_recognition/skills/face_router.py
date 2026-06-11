from __future__ import annotations

import json
import urllib.request
from typing import Any

from audio_recognition.skills.catalog_loader import is_loopback_url


FACE_EMOTIONS = {
    "face_neutral": "neutral",
    "face_happy": "happy",
    "face_joy": "joy",
    "face_sad": "sad",
    "face_angry": "angry",
}
FACE_SKILL_IDS = {
    *FACE_EMOTIONS,
    "face_speak",
    "face_mouth_open",
    "face_blink",
    "face_reset",
}
MIN_FACE_ACTION_DURATION_MS = 5000


def is_face_skill(skill_id: str) -> bool:
    return skill_id in FACE_SKILL_IDS


def create_face_task(face_server: str, skill_id: str, text: str = "", source: str = "audio_recognition") -> dict[str, Any]:
    if not face_server:
        raise RuntimeError("face_server is required")
    if skill_id in FACE_EMOTIONS:
        return post_face(face_server, "/api/face/emotion", {
            "emotion": FACE_EMOTIONS[skill_id],
            "intensity": 0.9,
            "duration_ms": MIN_FACE_ACTION_DURATION_MS,
            "source": source,
            "message": text,
        })
    if skill_id == "face_speak":
        return post_face(face_server, "/api/face/speak", {"text": text, "duration_ms": MIN_FACE_ACTION_DURATION_MS, "source": source})
    if skill_id == "face_mouth_open":
        return post_face(face_server, "/api/face/mouth", {"open": True, "duration_ms": MIN_FACE_ACTION_DURATION_MS, "source": source})
    if skill_id == "face_blink":
        return post_face(face_server, "/api/face/blink", {})
    if skill_id == "face_reset":
        return post_face(face_server, "/api/face/reset", {})
    raise RuntimeError(f"unknown face skill: {skill_id}")


def post_face(face_server: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{face_server.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if is_loopback_url(face_server):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        open_url = opener.open
    else:
        open_url = urllib.request.urlopen
    with open_url(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))
