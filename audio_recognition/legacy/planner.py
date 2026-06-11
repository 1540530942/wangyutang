from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

from audio_recognition.core.contracts import PlannedTask
from audio_recognition.legacy.voice_intents import resolve_voice_intent
from audio_recognition.skills.allowlist import ALLOWED_VOICE_SKILL_IDS
from audio_recognition.skills.catalog_loader import load_catalog
from audio_recognition.skills.face_router import is_face_skill


class PlannerError(RuntimeError):
    pass


def _route_for_skill(skill_id: str) -> str:
    if not skill_id:
        return "none"
    return "face" if is_face_skill(skill_id) else "action"


def _allowed_skills(catalog_path: str | Path) -> list[dict[str, Any]]:
    catalog = load_catalog(catalog_path)
    return [skill for skill in catalog.get("skills", []) if skill.get("id") in ALLOWED_VOICE_SKILL_IDS]


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.startswith("```")]
        stripped = "\n".join(lines).strip()
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise PlannerError("planner response is not a JSON object")
    return data


class RuleBasedTaskPlanner:
    def __init__(self, catalog_path: str | Path):
        self.catalog_path = Path(catalog_path)

    def plan(self, text: str) -> PlannedTask | None:
        skill = resolve_voice_intent(text, self.catalog_path)
        if not skill:
            return None
        return PlannedTask(
            skill_id=str(skill["id"]),
            route=_route_for_skill(str(skill["id"])),
            planner="rule",
            confidence=1.0,
            transcript=text.strip(),
            metadata={"aliases": skill.get("aliases", []), "name_zh": skill.get("name_zh", "")},
        )


class LlmTaskPlanner:
    def __init__(self, catalog_path: str | Path, config: dict[str, Any]):
        self.catalog_path = Path(catalog_path)
        self.config = config
        self.endpoint = str(config.get("endpoint") or "")
        self.model = str(config.get("model") or "")
        self.timeout = float(config.get("timeout_seconds") or 30)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = str(self.config.get("api_key") or "")
        api_key_env = str(self.config.get("api_key_env") or "")
        token = api_key or (os.environ.get(api_key_env, "") if api_key_env else "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        headers.update({str(k): str(v) for k, v in dict(self.config.get("headers") or {}).items()})
        return headers

    def _payload(self, text: str) -> dict[str, Any]:
        allowed = _allowed_skills(self.catalog_path)
        prompt = (
            "You are a robot task planner. "
            "Choose exactly one skill_id from the provided list when the transcript clearly matches a safe skill. "
            "If nothing matches, return an empty skill_id. "
            "Return strict JSON: {\"skill_id\":\"...\",\"confidence\":0.0,\"reason\":\"...\"}."
        )
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"transcript": text, "skills": allowed},
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    def plan(self, text: str) -> PlannedTask | None:
        if not self.endpoint:
            raise PlannerError("router.planner.llm.endpoint is required")
        response = requests.post(
            self.endpoint,
            headers=self._headers(),
            json=self._payload(text),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not isinstance(content, str) or not content.strip():
            raise PlannerError("planner response content is empty")
        data = _parse_json_object(content)
        skill_id = str(data.get("skill_id") or "").strip()
        if not skill_id:
            return None
        if skill_id not in ALLOWED_VOICE_SKILL_IDS:
            raise PlannerError(f"planner returned unsupported skill_id: {skill_id}")
        return PlannedTask(
            skill_id=skill_id,
            route=_route_for_skill(skill_id),
            planner="llm",
            confidence=max(0.0, min(1.0, float(data.get("confidence") or 0.0))),
            transcript=text.strip(),
            metadata={"reason": str(data.get("reason") or "")},
        )


class HybridTaskPlanner:
    def __init__(self, primary: LlmTaskPlanner, fallback: RuleBasedTaskPlanner):
        self.primary = primary
        self.fallback = fallback

    def plan(self, text: str) -> PlannedTask | None:
        try:
            plan = self.primary.plan(text)
        except Exception:
            plan = None
        return plan or self.fallback.plan(text)


def build_task_planner(router_config: dict[str, Any] | None, catalog_path: str | Path):
    router_config = router_config or {}
    planner_config = dict(router_config.get("planner") or {})
    mode = str(planner_config.get("mode") or router_config.get("mode") or "rule").strip().lower()
    rule_planner = RuleBasedTaskPlanner(catalog_path)
    if mode == "rule":
        return rule_planner
    llm_config = dict(planner_config.get("llm") or {})
    llm_planner = LlmTaskPlanner(catalog_path, llm_config)
    if mode == "llm":
        return llm_planner
    if mode == "hybrid":
        return HybridTaskPlanner(llm_planner, rule_planner)
    raise PlannerError(f"unsupported planner mode: {mode}")
