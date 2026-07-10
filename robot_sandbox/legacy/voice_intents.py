from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from robot_sandbox.skills.allowlist import ALLOWED_VOICE_SKILL_IDS
from robot_sandbox.skills.catalog_loader import resolve_skill


VOICE_INTENT_ALIASES = {
    "move_forward": [
        "前进",
        "向前走",
        "往前走",
        "向前移动",
        "往前移动",
        "朝前走",
        "小车前进",
        "车子前进",
        "go forward",
        "forward",
    ],
    "move_backward": [
        "后退",
        "向后退",
        "往后退",
        "向后走",
        "往后走",
        "倒退",
        "小车后退",
        "车子后退",
        "backward",
        "back",
    ],
    "move_left": [
        "左移",
        "向左移",
        "往左移",
        "向左平移",
        "往左平移",
        "向左走",
        "往左走",
        "左平移",
        "move left",
        "strafe left",
    ],
    "move_right": [
        "右移",
        "向右移",
        "往右移",
        "向右平移",
        "往右平移",
        "向右走",
        "往右走",
        "右平移",
        "move right",
        "strafe right",
    ],
    "turn_left": [
        "左转",
        "向左转",
        "往左转",
        "朝左转",
        "原地左转",
        "左旋转",
        "turn left",
    ],
    "turn_right": [
        "右转",
        "向右转",
        "往右转",
        "朝右转",
        "原地右转",
        "右旋转",
        "turn right",
    ],
    "look_left": ["向左看", "左看", "看左边", "摄像头向左", "镜头向左"],
    "look_right": ["向右看", "右看", "看右边", "摄像头向右", "镜头向右"],
    "look_up": ["向上看", "上看", "看上面", "摄像头向上", "镜头向上"],
    "look_down": ["向下看", "下看", "看下面", "摄像头向下", "镜头向下"],
    "reset_pose": ["复位", "回正", "重置", "恢复默认", "reset"],
    "emergency_stop": [
        "急停",
        "停止",
        "停下",
        "停车",
        "刹车",
        "不要动",
        "别动",
        "stop",
        "e-stop",
    ],
    "face_neutral": ["正常表情", "平静一点", "普通表情"],
    "face_happy": ["笑一笑", "微笑", "开心一点", "高兴一点"],
    "face_joy": ["超开心", "快乐一点", "乐一乐", "星星眼"],
    "face_sad": ["难过一点", "伤心一点", "委屈一下"],
    "face_angry": ["生气一点", "凶一点", "发火一下"],
    "face_speak": ["说话", "说句话", "讲话", "动动嘴"],
    "face_mouth_open": ["张嘴", "张开嘴巴", "嘴巴张开"],
    "face_blink": ["眨眼", "眨眨眼", "眨一下眼睛"],
    "face_reset": ["恢复表情", "表情复位", "脸部复位"],
}

IGNORED_TRANSCRIPT_ALIASES = [
    "您的指令已经完成了",
    "您的指令已为您完成",
    "指令已经完成",
    "已经完成了",
]


def normalize_voice_text(text: str) -> str:
    lowered = text.strip().lower()
    return re.sub(r"[\s,，。.!?！？;；:：\"'“”‘’、]+", "", lowered)


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
