from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from audio_recognition.skills.allowlist import ALLOWED_VOICE_SKILL_IDS
from audio_recognition.skills.catalog_loader import resolve_skill


VOICE_INTENT_ALIASES = {
    "move_forward": [
        "\u524d\u8fdb",
        "\u5411\u524d\u8d70",
        "\u5f80\u524d\u8d70",
        "\u5411\u524d\u79fb\u52a8",
        "\u5f80\u524d\u79fb\u52a8",
        "\u671d\u524d\u8d70",
        "\u5c0f\u8f66\u524d\u8fdb",
        "\u8f66\u5b50\u524d\u8fdb",
        "go forward",
        "forward",
    ],
    "move_backward": [
        "\u540e\u9000",
        "\u5411\u540e\u9000",
        "\u5f80\u540e\u9000",
        "\u5411\u540e\u8d70",
        "\u5f80\u540e\u8d70",
        "\u5012\u9000",
        "\u5c0f\u8f66\u540e\u9000",
        "\u8f66\u5b50\u540e\u9000",
        "backward",
        "back",
    ],
    "move_left": [
        "\u5de6\u79fb",
        "\u5411\u5de6\u79fb",
        "\u5f80\u5de6\u79fb",
        "\u5411\u5de6\u5e73\u79fb",
        "\u5f80\u5de6\u5e73\u79fb",
        "\u5411\u5de6\u8d70",
        "\u5f80\u5de6\u8d70",
        "\u5de6\u5e73\u79fb",
        "move left",
        "strafe left",
    ],
    "move_right": [
        "\u53f3\u79fb",
        "\u5411\u53f3\u79fb",
        "\u5f80\u53f3\u79fb",
        "\u5411\u53f3\u5e73\u79fb",
        "\u5f80\u53f3\u5e73\u79fb",
        "\u5411\u53f3\u8d70",
        "\u5f80\u53f3\u8d70",
        "\u53f3\u5e73\u79fb",
        "move right",
        "strafe right",
    ],
    "turn_left": [
        "\u5de6\u8f6c",
        "\u5411\u5de6\u8f6c",
        "\u5f80\u5de6\u8f6c",
        "\u671d\u5de6\u8f6c",
        "\u539f\u5730\u5de6\u8f6c",
        "\u5de6\u65cb\u8f6c",
        "turn left",
    ],
    "turn_right": [
        "\u53f3\u8f6c",
        "\u5411\u53f3\u8f6c",
        "\u5f80\u53f3\u8f6c",
        "\u671d\u53f3\u8f6c",
        "\u539f\u5730\u53f3\u8f6c",
        "\u53f3\u65cb\u8f6c",
        "turn right",
    ],
    "look_left": ["\u5411\u5de6\u770b", "\u5de6\u770b", "\u770b\u5de6\u8fb9", "\u6444\u50cf\u5934\u5411\u5de6", "\u955c\u5934\u5411\u5de6"],
    "look_right": ["\u5411\u53f3\u770b", "\u53f3\u770b", "\u770b\u53f3\u8fb9", "\u6444\u50cf\u5934\u5411\u53f3", "\u955c\u5934\u5411\u53f3"],
    "look_up": ["\u5411\u4e0a\u770b", "\u4e0a\u770b", "\u770b\u4e0a\u9762", "\u6444\u50cf\u5934\u5411\u4e0a", "\u955c\u5934\u5411\u4e0a"],
    "look_down": ["\u5411\u4e0b\u770b", "\u4e0b\u770b", "\u770b\u4e0b\u9762", "\u6444\u50cf\u5934\u5411\u4e0b", "\u955c\u5934\u5411\u4e0b"],
    "reset_pose": ["\u590d\u4f4d", "\u56de\u6b63", "\u91cd\u7f6e", "\u6062\u590d\u9ed8\u8ba4", "reset"],
    "emergency_stop": [
        "\u6025\u505c",
        "\u505c\u6b62",
        "\u505c\u4e0b",
        "\u505c\u8f66",
        "\u5239\u8f66",
        "\u4e0d\u8981\u52a8",
        "\u522b\u52a8",
        "stop",
        "e-stop",
    ],
    "face_neutral": ["\u6b63\u5e38\u8868\u60c5", "\u5e73\u9759\u4e00\u70b9", "\u666e\u901a\u8868\u60c5"],
    "face_happy": ["\u7b11\u4e00\u7b11", "\u5fae\u7b11", "\u5f00\u5fc3\u4e00\u70b9", "\u9ad8\u5174\u4e00\u70b9"],
    "face_joy": ["\u8d85\u5f00\u5fc3", "\u5feb\u4e50\u4e00\u70b9", "\u4e50\u4e00\u4e50", "\u661f\u661f\u773c"],
    "face_sad": ["\u96be\u8fc7\u4e00\u70b9", "\u4f24\u5fc3\u4e00\u70b9", "\u59d4\u5c48\u4e00\u4e0b"],
    "face_angry": ["\u751f\u6c14\u4e00\u70b9", "\u51f6\u4e00\u70b9", "\u53d1\u706b\u4e00\u4e0b"],
    "face_speak": ["\u8bf4\u8bdd", "\u8bf4\u53e5\u8bdd", "\u8bb2\u8bdd", "\u52a8\u52a8\u5634"],
    "face_mouth_open": ["\u5f20\u5634", "\u5f20\u5f00\u5634\u5df4", "\u5634\u5df4\u5f20\u5f00"],
    "face_blink": ["\u7728\u773c", "\u7728\u7728\u773c", "\u7728\u4e00\u4e0b\u773c\u775b"],
    "face_reset": ["\u6062\u590d\u8868\u60c5", "\u8868\u60c5\u590d\u4f4d", "\u8138\u90e8\u590d\u4f4d"],
}

IGNORED_TRANSCRIPT_ALIASES = [
    "\u60a8\u7684\u6307\u4ee4\u5df2\u7ecf\u5b8c\u6210\u4e86",
    "\u60a8\u7684\u6307\u4ee4\u5df2\u4e3a\u60a8\u5b8c\u6210",
    "\u6307\u4ee4\u5df2\u7ecf\u5b8c\u6210",
    "\u5df2\u7ecf\u5b8c\u6210\u4e86",
]


def normalize_voice_text(text: str) -> str:
    lowered = text.strip().lower()
    return re.sub("[\\s,\\uFF0C\\u3002.!?\\uFF01\\uFF1F;\\uFF1B:\\uFF1A\"'\\u201c\\u201d\\u2018\\u2019\\u3001]+", "", lowered)


def resolve_voice_intent(text: str, catalog_path: str | Path | None = None) -> dict[str, Any] | None:
    normalized = normalize_voice_text(text)
    if not normalized:
        return None
    if any(normalize_voice_text(alias) in normalized for alias in IGNORED_TRANSCRIPT_ALIASES):
        return None

    best_match: dict[str, Any] | None = None
    best_score = (-1, -1)
    for skill_id, aliases in VOICE_INTENT_ALIASES.items():
        normalized_skill_id = normalize_voice_text(skill_id)
        if normalized_skill_id in normalized:
            exact = 1 if normalized_skill_id == normalized else 0
            score = (exact, len(normalized_skill_id))
            if score > best_score:
                best_score = score
                best_match = {"id": skill_id, "name_zh": aliases[0], "aliases": aliases}
        for alias in aliases:
            normalized_alias = normalize_voice_text(alias)
            if normalized_alias in normalized:
                exact = 1 if normalized_alias == normalized else 0
                score = (exact, len(normalized_alias))
                if score > best_score:
                    best_score = score
                    best_match = {"id": skill_id, "name_zh": aliases[0], "aliases": aliases}
    if best_match:
        return best_match

    if not catalog_path:
        return None
    try:
        skill = resolve_skill(text, catalog_path)
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    if skill and skill.get("id") in ALLOWED_VOICE_SKILL_IDS:
        return skill
    return None
