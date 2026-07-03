from __future__ import annotations

from pathlib import Path
from typing import Any

from audio_recognition.skills.registry import SkillRegistry, load_skill_registry


def _registry(registry_path: str | Path | None = None, catalog_path: str | Path | None = None) -> SkillRegistry:
    candidate = Path(registry_path) if registry_path else None
    if candidate and candidate.suffix.lower() == ".json" and catalog_path is None:
        return load_skill_registry(None, candidate)
    return load_skill_registry(candidate, catalog_path)


def tool_skill_groups(registry_path: str | Path | None = None, catalog_path: str | Path | None = None) -> dict[str, list[str]]:
    registry = _registry(registry_path, catalog_path)
    return {
        "action": [spec.skill_id for spec in registry.by_tool("dispatch_action")],
        "face": [spec.skill_id for spec in registry.by_tool("dispatch_face")],
    }


def build_react_tools_schema(registry_path: str | Path | None = None, catalog_path: str | Path | None = None) -> list[dict[str, Any]]:
    registry = _registry(registry_path, catalog_path)
    action_skills = registry.by_tool("dispatch_action")
    face_skills = registry.by_tool("dispatch_face")
    action_max = registry.max_duration_for_tool("dispatch_action") or 1000
    face_max = registry.max_duration_for_tool("dispatch_face") or 5000
    common = {
        "order": {"type": "integer", "minimum": 1},
        "wait_until": {"type": "string", "enum": ["accepted", "completed"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "text": {"type": "string"},
    }
    action_notes = registry.duration_notes_for_tool("dispatch_action")
    face_notes = registry.duration_notes_for_tool("dispatch_face")
    return [
        {
            "type": "function",
            "function": {
                "name": "dispatch_action",
                "description": f"Execute one bounded robot motion or pose skill from the configured YAML registry. Duration limits: {action_notes}.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "string", "enum": [spec.skill_id for spec in action_skills]},
                        "duration_ms": {"type": "integer", "minimum": 0, "maximum": action_max},
                        "distance_cm": {
                            "type": "number",
                            "minimum": 1,
                            "maximum": 50,
                            "description": "Requested move distance in centimeters for move_forward/move_backward/move_left/move_right.",
                        },
                        **common,
                    },
                    "required": ["skill_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dispatch_face",
                "description": f"Execute one face expression skill from the configured YAML registry. Duration limits: {face_notes}.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "string", "enum": [spec.skill_id for spec in face_skills]},
                        "duration_ms": {"type": "integer", "minimum": 0, "maximum": face_max},
                        "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                        **common,
                    },
                    "required": ["skill_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "camera_snapshot",
                "description": "Capture a camera observation before deciding the next action.",
                "parameters": {
                    "type": "object",
                    "properties": {"focus": {"type": "string"}, "purpose": {"type": "string"}, "reason": {"type": "string"}, **common},
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "front_distance",
                "description": "Read the latest front distance estimate before deciding whether forward motion is safe.",
                "parameters": {
                    "type": "object",
                    "properties": {"focus": {"type": "string"}, "purpose": {"type": "string"}, **common},
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_robot_state",
                "description": "Read robot health/state before deciding the next action.",
                "parameters": {"type": "object", "properties": {"reason": {"type": "string"}, **common}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ask_confirmation",
                "description": "Ask the user for confirmation when an instruction is ambiguous.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "timeout_ms": {"type": "integer", "minimum": 1000, "maximum": 60000},
                        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 60},
                        **common,
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "emergency_stop",
                "description": "Immediately stop robot motion.",
                "parameters": {"type": "object", "properties": {"skill_id": {"type": "string", "enum": ["emergency_stop"]}, **common}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "inspect_scene",
                "description": "Capture a camera image and analyze it with a vision model to answer questions about what is visible in front of the robot (e.g., whether there are people, obstacles, or objects). Use this when the user asks about scene content rather than requesting a raw snapshot.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The question to ask the vision model about the scene, in Chinese."},
                        "focus": {"type": "string"},
                        **common,
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_rgb_color",
                "description": "Set the robot's RGB lights to a specific color by providing red, green, and blue values. Use this when the user asks to change the light color (e.g., 变成红色, 蓝色灯光, 调成绿色). You must know the color's RGB values — e.g., red=(255,0,0), green=(0,255,0), blue=(0,0,255), yellow=(255,200,0), purple=(180,0,255), white=(255,255,255), orange=(255,100,0), pink=(255,80,160), cyan=(0,255,255).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "red": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Red channel (0-255)"},
                        "green": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Green channel (0-255)"},
                        "blue": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Blue channel (0-255)"},
                        **common,
                    },
                    "required": ["red", "green", "blue"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Finish the ReAct loop when all requested positive commands are handled.",
                "parameters": {"type": "object", "properties": {"message": {"type": "string"}, "final": {"type": "string"}}, "additionalProperties": False},
            },
        },
    ]
