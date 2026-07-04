from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from slam_reporting import slam_odometry_payload


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
CATALOG_PATH = BASE_DIR / "skill_catalog.json"
TOKEN_FILE = DATA_DIR / ".action_token"
SETTINGS_FILE = DATA_DIR / "settings.json"
SLAM_MAPPING_URL = os.getenv("ACTION_SLAM_MAPPING_URL", "http://slam-mapping:8301").rstrip("/")
SLAM_TIMEOUT_SECONDS = float(os.getenv("ACTION_SLAM_TIMEOUT_SECONDS", "2.0") or 2.0)

DATA_DIR.mkdir(parents=True, exist_ok=True)

DEVICE_ONLINE_SECONDS = 30.0
MAX_TASKS = 100
CLAIM_TIMEOUT_SECONDS = 35.0
MAX_LONG_POLL_SECONDS = 20.0
LONG_POLL_TICK_SECONDS = 0.1
DEFAULT_SETTINGS = {
    "unit_distance_cm": 10.0,
    "turn_angle_deg": 5.0,
    "sensitivity": 1.0,
    "voice_volume_percent": 0.0,
    "rgb_red": 0,
    "rgb_green": 0,
    "rgb_blue": 0,
}
ACTIVE_STATUSES = {"pending", "claimed", "running"}
MOTION_TYPES = {"base_move", "base_turn"}

app = FastAPI(title="TurboPi Action Move", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

tasks: list[dict[str, Any]] = []
device_state: dict[str, Any] = {
    "device_id": "",
    "last_seen_at": 0.0,
    "current_task_id": "",
    "last_result": None,
    "status": "offline",
    "hostname": "",
    "ip_address": "",
    "wifi_ssid": "",
    "gateway": "",
    "diagnostics": {},
}


class ActionRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=80)
    source: str = Field("web", max_length=40)
    note: str = Field("", max_length=200)
    ttl_seconds: int = Field(30, ge=5, le=300)
    verification_code: str = Field("", max_length=20)
    settings_override: dict[str, float] = Field(default_factory=dict)


class ActionSettings(BaseModel):
    unit_distance_cm: float = Field(DEFAULT_SETTINGS["unit_distance_cm"], ge=1.0, le=50.0)
    turn_angle_deg: float = Field(DEFAULT_SETTINGS["turn_angle_deg"], ge=1.0, le=90.0)
    sensitivity: float = Field(DEFAULT_SETTINGS["sensitivity"], ge=0.2, le=2.0)
    voice_volume_percent: float = Field(DEFAULT_SETTINGS["voice_volume_percent"], ge=0.0, le=100.0)
    rgb_red: int = Field(DEFAULT_SETTINGS["rgb_red"], ge=0, le=255)
    rgb_green: int = Field(DEFAULT_SETTINGS["rgb_green"], ge=0, le=255)
    rgb_blue: int = Field(DEFAULT_SETTINGS["rgb_blue"], ge=0, le=255)


class DeviceHeartbeat(BaseModel):
    device_id: str = Field("turbopi-01", max_length=80)
    current_task_id: str = Field("", max_length=80)
    status: str = Field("idle", max_length=80)
    detail: str = Field("", max_length=300)
    hostname: str = Field("", max_length=120)
    ip_address: str = Field("", max_length=120)
    wifi_ssid: str = Field("", max_length=120)
    gateway: str = Field("", max_length=120)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=80)
    device_id: str = Field("turbopi-01", max_length=80)
    status: str = Field(..., pattern="^(running|complete|failed|rejected)$")
    output: str = Field("", max_length=5000)
    error: str = Field("", max_length=2000)


def read_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def normalize_settings(data: dict[str, Any] | None = None) -> dict[str, float]:
    merged = {**DEFAULT_SETTINGS, **(data or {})}
    settings = ActionSettings(**merged)
    return {
        "unit_distance_cm": round(float(settings.unit_distance_cm), 2),
        "turn_angle_deg": round(float(settings.turn_angle_deg), 2),
        "sensitivity": round(float(settings.sensitivity), 2),
        "voice_volume_percent": round(float(settings.voice_volume_percent), 2),
        "rgb_red": int(settings.rgb_red),
        "rgb_green": int(settings.rgb_green),
        "rgb_blue": int(settings.rgb_blue),
    }


def load_settings() -> dict[str, float]:
    if not SETTINGS_FILE.exists():
        return normalize_settings()
    try:
        return normalize_settings(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError):
        return normalize_settings()


def save_settings(settings: dict[str, Any]) -> dict[str, float]:
    normalized = normalize_settings(settings)
    SETTINGS_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


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
        if task["status"] == "claimed" and now - float(task.get("claimed_at") or 0) > CLAIM_TIMEOUT_SECONDS:
            task["status"] = "failed"
            task["updated_at"] = now
            task["completed_at"] = now
            task["error"] = f"claimed task timed out after {int(CLAIM_TIMEOUT_SECONDS)}s"
            if device_state.get("current_task_id") == task["id"]:
                device_state["current_task_id"] = ""
                device_state["status"] = "failed"
                device_state["last_result"] = public_task(task)


def public_task(task: dict[str, Any]) -> dict[str, Any]:
    result = dict(task)
    requested_at = float(result.get("requested_at") or 0)
    claimed_at = float(result.get("claimed_at") or 0)
    completed_at = float(result.get("completed_at") or 0)
    result["claim_latency_seconds"] = round(claimed_at - requested_at, 3) if requested_at and claimed_at else None
    result["completion_latency_seconds"] = round(completed_at - requested_at, 3) if requested_at and completed_at else None
    return result


def report_slam_motion(task: dict[str, Any]) -> dict[str, Any] | None:
    if not SLAM_MAPPING_URL:
        return None
    payload = slam_odometry_payload(task)
    if payload is None:
        return None
    request = urllib.request.Request(
        f"{SLAM_MAPPING_URL}/api/odom",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=SLAM_TIMEOUT_SECONDS) as response:
        result = json.loads(response.read().decode("utf-8"))
    return {"ok": True, "request": payload, "response": result}


def has_active_motion_task() -> bool:
    return any(task.get("type") in MOTION_TYPES and task.get("status") in ACTIVE_STATUSES for task in tasks)


def expire_pending_motion_tasks(reason: str) -> None:
    now = time.time()
    for task in tasks:
        if task.get("type") in MOTION_TYPES and task.get("status") == "pending":
            task["status"] = "expired"
            task["updated_at"] = now
            task["completed_at"] = now
            task["error"] = reason


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
        "settings": load_settings(),
        "pending_tasks": sum(1 for task in tasks if task["status"] == "pending"),
        "skills": len(read_catalog().get("skills", [])),
    }


@app.get("/api/diagnostics")
def diagnostics() -> dict[str, Any]:
    refresh_tasks()
    device = current_device()
    return {
        "status": "ok",
        "service": "TurboPi Action Move",
        "device": device,
        "diagnostics": device.get("diagnostics") or {},
        "settings": load_settings(),
        "pending_tasks": sum(1 for task in tasks if task["status"] == "pending"),
        "recent_tasks": [public_task(task) for task in reversed(tasks[-12:])],
    }


@app.get("/api/skills")
def skills() -> dict[str, Any]:
    catalog = read_catalog()
    return {"defaults": catalog.get("defaults", {}), "skills": catalog.get("skills", [])}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"settings": load_settings()}


@app.post("/api/settings")
def update_settings(payload: ActionSettings) -> dict[str, Any]:
    return {"ok": True, "settings": save_settings(payload.model_dump())}


@app.get("/api/tasks")
def list_tasks() -> dict[str, Any]:
    refresh_tasks()
    return {"tasks": [public_task(task) for task in reversed(tasks[-MAX_TASKS:])]}


@app.post("/api/tasks/clear")
def clear_tasks() -> dict[str, Any]:
    count = len(tasks)
    tasks.clear()
    device_state["current_task_id"] = ""
    device_state["last_result"] = None
    return {"ok": True, "cleared": count}


@app.post("/api/tasks")
def create_task(payload: ActionRequest) -> dict[str, Any]:
    skill = resolve_skill(payload.action)
    refresh_tasks()
    if skill["id"] == "emergency_stop":
        expire_pending_motion_tasks("cancelled by emergency stop")
    elif skill["id"] == "remote_shutdown":
        if payload.verification_code != "123":
            raise HTTPException(status_code=403, detail="invalid shutdown verification code")
        expire_pending_motion_tasks("cancelled by remote shutdown")
    elif not current_device().get("online"):
        raise HTTPException(status_code=503, detail="robot edge device is offline")
    elif skill["type"] in MOTION_TYPES and has_active_motion_task():
        raise HTTPException(status_code=409, detail="a motion task is already active")
    now = time.time()
    settings = load_settings()
    for key in ("rgb_red", "rgb_green", "rgb_blue"):
        if key in payload.settings_override:
            settings[key] = max(0, min(255, int(payload.settings_override[key])))
    # Per-command distance/angle override enables precise single-command moves
    # (e.g. a 45° rotation button, or closed-loop "advance exactly N cm").
    if "unit_distance_cm" in payload.settings_override:
        settings["unit_distance_cm"] = round(max(1.0, min(50.0, float(payload.settings_override["unit_distance_cm"]))), 2)
    if "turn_angle_deg" in payload.settings_override:
        settings["turn_angle_deg"] = round(max(1.0, min(90.0, float(payload.settings_override["turn_angle_deg"]))), 2)
    task = {
        "id": f"{int(now * 1000)}-{secrets.token_hex(3)}",
        "action": payload.action,
        "skill_id": skill["id"],
        "name_zh": skill["name_zh"],
        "type": skill["type"],
        "source": payload.source,
        "note": payload.note,
        "settings": settings,
        "unit_distance_cm": settings["unit_distance_cm"],
        "turn_angle_deg": settings["turn_angle_deg"],
        "sensitivity": settings["sensitivity"],
        "voice_volume_percent": settings["voice_volume_percent"],
        "rgb_red": settings["rgb_red"],
        "rgb_green": settings["rgb_green"],
        "rgb_blue": settings["rgb_blue"],
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
async def next_task(
    wait_seconds: float = 0.0,
    x_action_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    require_token(x_action_token)
    deadline = time.monotonic() + min(max(wait_seconds, 0.0), MAX_LONG_POLL_SECONDS)

    while True:
        refresh_tasks()
        now = time.time()
        pending_tasks = [task for task in tasks if task["status"] == "pending"]
        pending_tasks.sort(key=lambda task: 0 if task.get("skill_id") == "emergency_stop" else 1)
        for task in pending_tasks:
            if task["status"] == "pending":
                task["status"] = "claimed"
                task["claimed_at"] = now
                task["updated_at"] = now
                device_state["current_task_id"] = task["id"]
                return {"task": public_task(task)}

        if time.monotonic() >= deadline:
            return {"task": None}
        await asyncio.sleep(LONG_POLL_TICK_SECONDS)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    refresh_tasks()
    for task in tasks:
        if task["id"] == task_id:
            return {"task": public_task(task)}
    raise HTTPException(status_code=404, detail="task not found")


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
            if payload.status == "complete":
                try:
                    slam_update = report_slam_motion(task)
                    if slam_update is not None:
                        task["slam_update"] = slam_update
                except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                    task["slam_update"] = {
                        "ok": False,
                        "error": str(exc),
                        "url": SLAM_MAPPING_URL,
                    }
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
            "hostname": payload.hostname,
            "ip_address": payload.ip_address,
            "wifi_ssid": payload.wifi_ssid,
            "gateway": payload.gateway,
        }
    )
    if payload.diagnostics:
        device_state["diagnostics"] = payload.diagnostics
    return {"ok": True, "device": current_device()}
