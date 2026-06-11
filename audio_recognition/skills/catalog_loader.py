from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


def load_catalog(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def flatten_skills(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for skill in catalog.get("skills", []):
        keys = [skill["id"], skill["name_zh"], *skill.get("aliases", [])]
        for key in keys:
            result[str(key).strip().lower()] = skill
    return result


def resolve_skill(text: str, catalog_path: str | Path) -> dict[str, Any] | None:
    key = text.strip().lower()
    skills = flatten_skills(load_catalog(catalog_path))
    if key in skills:
        return skills[key]
    for alias, skill in skills.items():
        if alias and alias in key:
            return skill
    return None


def create_action_task(action_server: str, skill_id: str, source: str = "audio_recognition") -> dict[str, Any]:
    if not action_server:
        raise RuntimeError("action_server is required")
    body = json.dumps({"action": skill_id, "source": source, "ttl_seconds": 120}).encode("utf-8")
    request = urllib.request.Request(
        f"{action_server.rstrip('/')}/api/tasks",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if is_loopback_url(action_server):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        open_url = opener.open
    else:
        open_url = urllib.request.urlopen
    with open_url(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def is_loopback_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}
