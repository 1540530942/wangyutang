from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
CATALOG_PATH = BASE_DIR / "skill_catalog.json"
TOKEN_FILE = DATA_DIR / ".action_token"

DATA_DIR.mkdir(parents=True, exist_ok=True)

DEVICE_ONLINE_SECONDS = 15.0
MAX_TASKS = 100

app = FastAPI(title="TurboPi Action Move", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

tasks: list[dict[str, Any]] = []
device_state: dict[str, Any] = {
    "device_id": "",
    "last_seen_at": 0.0,
    "current_task_id": "",
    "last_result": None,
    "status": "offline",
}


class ActionRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=80)
    source: str = Field("web", max_length=40)
    note: str = Field("", max_length=200)
    ttl_seconds: int = Field(30, ge=5, le=300)


class DeviceHeartbeat(BaseModel):
    device_id: str = Field("turbopi-01", max_length=80)
    current_task_id: str = Field("", max_length=80)
    status: str = Field("idle", max_length=80)
    detail: str = Field("", max_length=300)


class TaskResult(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=80)
    device_id: str = Field("turbopi-01", max_length=80)
    status: str = Field(..., pattern="^(running|complete|failed|rejected)$")
    output: str = Field("", max_length=5000)
    error: str = Field("", max_length=2000)


def read_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def flatten_skills() -> dict[str, dict[str, Any]]:
    catalog = read_catalog()
    result: dict[str, dict[str, Any]] = {}
    for skill in catalog.get("skills", []):
        keys = [skill["id"], skill["name_zh"], *skill.get("aliases", [])]
        for key in keys:
            result[str(key).strip().lower()] = skill
    return result


def resolve_skill(text: str) -> dict[str, Any]:
    key = text.strip().lower()
    skills = flatten_skills()
    if key in skills:
        return skills[key]
    for alias, skill in skills.items():
        if alias and alias in key:
            return skill
    raise HTTPException(status_code=400, detail=f"unknown action: {text}")


def expected_token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def require_token(x_action_token: Annotated[str | None, Header()] = None) -> None:
    expected = expected_token()
    if not expected:
        return
    if not x_action_token or not secrets.compare_digest(x_action_token, expected):
        raise HTTPException(status_code=401, detail="invalid action token")


def refresh_tasks() -> None:
    now = time.time()
    for task in tasks:
        if task["status"] == "pending" and now > float(task["deadline_at"]):
            task["status"] = "expired"
            task["updated_at"] = now


def public_task(task: dict[str, Any]) -> dict[str, Any]:
    return dict(task)


def current_device() -> dict[str, Any]:
    last_seen_at = float(device_state.get("last_seen_at") or 0)
    age_seconds = time.time() - last_seen_at if last_seen_at else 0.0
    online = bool(last_seen_at and age_seconds <= DEVICE_ONLINE_SECONDS)
    return {
        **device_state,
        "online": online,
        "age_seconds": age_seconds,
        "online_threshold_seconds": DEVICE_ONLINE_SECONDS,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    refresh_tasks()
    return {
        "status": "ok",
        "service": "TurboPi Action Move",
        "device": current_device(),
        "pending_tasks": sum(1 for task in tasks if task["status"] == "pending"),
        "skills": len(read_catalog().get("skills", [])),
    }


@app.get("/api/skills")
def skills() -> dict[str, Any]:
    catalog = read_catalog()
    return {"defaults": catalog.get("defaults", {}), "skills": catalog.get("skills", [])}


@app.get("/api/tasks")
def list_tasks() -> dict[str, Any]:
    refresh_tasks()
    return {"tasks": [public_task(task) for task in reversed(tasks[-MAX_TASKS:])]}


@app.post("/api/tasks")
def create_task(payload: ActionRequest) -> dict[str, Any]:
    skill = resolve_skill(payload.action)
    now = time.time()
    task = {
        "id": f"{int(now * 1000)}-{secrets.token_hex(3)}",
        "action": payload.action,
        "skill_id": skill["id"],
        "name_zh": skill["name_zh"],
        "type": skill["type"],
        "source": payload.source,
        "note": payload.note,
        "status": "pending",
        "requested_at": now,
        "updated_at": now,
        "deadline_at": now + payload.ttl_seconds,
        "claimed_at": 0.0,
        "completed_at": 0.0,
        "device_id": "",
        "output": "",
        "error": "",
    }
    tasks.append(task)
    del tasks[:-MAX_TASKS]
    return {"ok": True, "task": public_task(task)}


@app.get("/api/tasks/next")
def next_task(x_action_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    require_token(x_action_token)
    refresh_tasks()
    now = time.time()
    for task in tasks:
        if task["status"] == "pending":
            task["status"] = "claimed"
            task["claimed_at"] = now
            task["updated_at"] = now
            device_state["current_task_id"] = task["id"]
            return {"task": public_task(task)}
    return {"task": None}


@app.post("/api/tasks/result")
def complete_task(payload: TaskResult, x_action_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    require_token(x_action_token)
    now = time.time()
    for task in tasks:
        if task["id"] == payload.task_id:
            task["status"] = payload.status
            task["device_id"] = payload.device_id
            task["output"] = payload.output
            task["error"] = payload.error
            task["updated_at"] = now
            if payload.status in {"complete", "failed", "rejected"}:
                task["completed_at"] = now
            device_state.update(
                {
                    "device_id": payload.device_id,
                    "last_seen_at": now,
                    "current_task_id": "" if payload.status != "running" else payload.task_id,
                    "status": payload.status,
                    "last_result": public_task(task),
                }
            )
            return {"ok": True, "task": public_task(task)}
    raise HTTPException(status_code=404, detail="task not found")


@app.post("/api/device/heartbeat")
def heartbeat(payload: DeviceHeartbeat, x_action_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    require_token(x_action_token)
    device_state.update(
        {
            "device_id": payload.device_id,
            "last_seen_at": time.time(),
            "current_task_id": payload.current_task_id,
            "status": payload.status,
            "detail": payload.detail,
        }
    )
    return {"ok": True, "device": current_device()}

