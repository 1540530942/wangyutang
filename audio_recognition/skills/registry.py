from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from audio_recognition.skills.allowlist import ALLOWED_VOICE_SKILL_IDS
from audio_recognition.skills.catalog_loader import load_catalog
from audio_recognition.skills.face_router import FACE_SKILL_IDS


OBSERVATION_TOOLS = {"camera_snapshot", "front_distance", "get_robot_state", "ask_confirmation"}
SYSTEM_TOOLS = {"finish"}
DEFAULT_REGISTRY_RELATIVE_PATH = "skills/registry.yaml"
DEFAULT_REGISTRY_DEFAULTS: dict[str, Any] = {
    "max_action_duration_ms": 1000,
    "max_turn_duration_ms": 800,
    "max_face_duration_ms": 5000,
    "observation_ttl_ms": {"camera_snapshot": 2000, "front_distance": 2000, "get_robot_state": 5000},
    "safety_thresholds": {"min_front_distance_estimate_cm": 15},
}


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    route: str
    tool: str
    min_duration_ms: int = 0
    max_duration_ms: int | None = None
    wait_until: str = "completed"
    enabled: bool = True
    aliases: tuple[str, ...] = ()
    risk: str = "low"
    pre_conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillRegistry:
    source: str
    skills: dict[str, SkillSpec]
    defaults: dict[str, Any]

    def by_tool(self, tool: str) -> list[SkillSpec]:
        return sorted((spec for spec in self.skills.values() if spec.tool == tool and spec.enabled), key=lambda item: item.skill_id)

    def get(self, skill_id: str) -> SkillSpec | None:
        spec = self.skills.get(skill_id)
        return spec if spec and spec.enabled else None

    def max_duration_for_tool(self, tool: str) -> int | None:
        values = [spec.max_duration_ms for spec in self.by_tool(tool) if spec.max_duration_ms is not None]
        return max(values) if values else None

    def duration_notes_for_tool(self, tool: str) -> str:
        parts = []
        for spec in self.by_tool(tool):
            if spec.max_duration_ms is not None:
                parts.append(f"{spec.skill_id}<={spec.max_duration_ms}ms")
        return "; ".join(parts)


def resolve_catalog_path(base_dir: Path, router_config: dict[str, Any] | None) -> Path:
    router_config = router_config or {}
    catalog_path = Path(str(router_config.get("skill_catalog") or "../action_move/skill_catalog.json"))
    if not catalog_path.is_absolute():
        catalog_path = (base_dir / catalog_path).resolve()
    return catalog_path


def resolve_registry_path(base_dir: Path, router_config: dict[str, Any] | None) -> Path:
    router_config = router_config or {}
    raw_path = (
        router_config.get("skill_registry")
        or router_config.get("skill_registry_path")
        or os.getenv("AUDIO_SKILL_REGISTRY")
        or DEFAULT_REGISTRY_RELATIVE_PATH
    )
    registry_path = Path(str(raw_path))
    if not registry_path.is_absolute():
        registry_path = (base_dir / registry_path).resolve()
    return registry_path


def _normalize_defaults(defaults: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(DEFAULT_REGISTRY_DEFAULTS)
    normalized["observation_ttl_ms"] = dict(DEFAULT_REGISTRY_DEFAULTS["observation_ttl_ms"])
    normalized["safety_thresholds"] = dict(DEFAULT_REGISTRY_DEFAULTS["safety_thresholds"])
    for key, value in defaults.items():
        if isinstance(value, dict) and isinstance(normalized.get(key), dict):
            nested = dict(normalized[key])
            nested.update(value)
            normalized[key] = nested
        else:
            normalized[key] = value
    return normalized


def _normalize_risk(value: Any) -> str:
    risk = str(value or "low").strip().lower()
    return risk if risk in {"low", "medium", "high"} else "low"


def _normalize_pre_conditions(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _tool_for_skill(skill_id: str, route: str) -> str:
    if skill_id == "emergency_stop":
        return "emergency_stop"
    if route == "face":
        return "dispatch_face"
    return "dispatch_action"


def _default_route_for_skill(skill_id: str, item: dict[str, Any] | None = None) -> str:
    if skill_id in FACE_SKILL_IDS:
        return "face"
    if skill_id == "emergency_stop":
        return "action"
    item_type = str((item or {}).get("type") or "")
    if item_type.startswith("face_"):
        return "face"
    return "action"


def _default_duration_for_skill(skill_id: str, route: str, defaults: dict[str, Any]) -> int | None:
    if skill_id == "emergency_stop":
        return 0
    if route == "face":
        return int(defaults.get("max_face_duration_ms") or 5000)
    if skill_id.startswith("turn_"):
        return int(defaults.get("max_turn_duration_ms") or 800)
    return int(defaults.get("max_action_duration_ms") or 1000)


def _duration_limit(item: dict[str, Any], skill_id: str, route: str, defaults: dict[str, Any]) -> tuple[int, int | None]:
    limits = item.get("limits") if isinstance(item.get("limits"), dict) else {}
    duration = limits.get("duration_ms") if isinstance(limits.get("duration_ms"), dict) else {}
    min_value = int(duration.get("min") or item.get("min_duration_ms") or 0)
    max_raw = duration.get("max") if "max" in duration else item.get("max_duration_ms")
    max_value = int(max_raw) if max_raw is not None else _default_duration_for_skill(skill_id, route, defaults)
    return min_value, max_value


def _registry_from_data(data: dict[str, Any], source: str) -> SkillRegistry:
    raw_defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    defaults = _normalize_defaults(raw_defaults)
    skills: dict[str, SkillSpec] = {}
    for item in data.get("skills", []):
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("id") or "").strip()
        if not skill_id or item.get("enabled", True) is False:
            continue
        route = str(item.get("route") or _default_route_for_skill(skill_id, item)).strip()
        tool = str(item.get("tool") or _tool_for_skill(skill_id, route)).strip()
        min_duration, max_duration = _duration_limit(item, skill_id, route, defaults)
        aliases = tuple(str(alias) for alias in item.get("aliases", []) if str(alias).strip())
        skills[skill_id] = SkillSpec(
            skill_id=skill_id,
            route=route,
            tool=tool,
            min_duration_ms=min_duration,
            max_duration_ms=max_duration,
            wait_until=str(item.get("wait_until") or "completed"),
            aliases=aliases,
            risk=_normalize_risk(item.get("risk")),
            pre_conditions=_normalize_pre_conditions(item.get("pre_conditions")),
        )
    return SkillRegistry(source=source, skills=skills, defaults=defaults)


def _registry_from_catalog(catalog_path: Path) -> SkillRegistry:
    catalog = load_catalog(catalog_path)
    defaults = catalog.get("defaults") if isinstance(catalog.get("defaults"), dict) else {}
    data: dict[str, Any] = {"defaults": {}, "skills": []}
    data["defaults"] = _normalize_defaults(
        {
            "max_action_duration_ms": min(int(defaults.get("max_move_duration_ms") or 1000), 1000),
            "max_turn_duration_ms": min(int(defaults.get("max_turn_duration_ms") or 800), 800),
            "max_face_duration_ms": 5000,
        }
    )
    for item in catalog.get("skills", []):
        skill_id = str(item.get("id") or "").strip()
        if skill_id not in ALLOWED_VOICE_SKILL_IDS:
            continue
        route = _default_route_for_skill(skill_id, item)
        data["skills"].append(
            {
                "id": skill_id,
                "route": route,
                "tool": _tool_for_skill(skill_id, route),
                "aliases": item.get("aliases", []),
            }
        )
    return _registry_from_data(data, str(catalog_path))


def _default_registry_data() -> dict[str, Any]:
    action_skills = [
        "move_forward", "move_backward", "move_left", "move_right",
        "turn_left", "turn_right", "look_left", "look_right",
        "look_up", "look_down", "reset_pose",
    ]
    face_skills = [
        "face_neutral", "face_happy", "face_joy", "face_sad",
        "face_angry", "face_speak", "face_mouth_open", "face_blink", "face_reset",
    ]
    skills = [
        {"id": "emergency_stop", "route": "action", "tool": "emergency_stop", "max_duration_ms": 0, "risk": "low"},
        {"id": "move_forward", "route": "action", "tool": "dispatch_action", "risk": "medium", "pre_conditions": ["recent_camera_snapshot", "front_distance_clear"]},
        *({"id": skill_id, "route": "action", "tool": "dispatch_action"} for skill_id in action_skills if skill_id != "move_forward"),
        *({"id": skill_id, "route": "face", "tool": "dispatch_face"} for skill_id in face_skills),
    ]
    return {
        "version": 1,
        "defaults": _normalize_defaults({}),
        "skills": skills,
    }


@lru_cache(maxsize=32)
def _load_skill_registry_cached(registry_path: str, catalog_path: str) -> SkillRegistry:
    registry = Path(registry_path) if registry_path else None
    catalog = Path(catalog_path) if catalog_path else None
    if registry and registry.exists():
        data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"skill registry must be a mapping: {registry}")
        return _registry_from_data(data, str(registry))
    if catalog and catalog.exists():
        return _registry_from_catalog(catalog)
    return _registry_from_data(_default_registry_data(), "builtin-default")


def load_skill_registry(registry_path: str | Path | None = None, catalog_path: str | Path | None = None) -> SkillRegistry:
    return _load_skill_registry_cached(str(registry_path or ""), str(catalog_path or ""))


def load_skill_registry_for_config(base_dir: Path, router_config: dict[str, Any] | None) -> SkillRegistry:
    return load_skill_registry(resolve_registry_path(base_dir, router_config), resolve_catalog_path(base_dir, router_config))


def export_registry_json(registry: SkillRegistry) -> str:
    return json.dumps(
        {
            "source": registry.source,
            "defaults": registry.defaults,
            "skills": {
                skill_id: {
                    "route": spec.route,
                    "tool": spec.tool,
                    "min_duration_ms": spec.min_duration_ms,
                    "max_duration_ms": spec.max_duration_ms,
                    "risk": spec.risk,
                    "pre_conditions": list(spec.pre_conditions),
                }
                for skill_id, spec in registry.skills.items()
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )
