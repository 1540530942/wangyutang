from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "control_state.json"
INSPECTION_FILE = DATA_DIR / "inspection_state.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_TOKEN = secrets.compare_digest
DEVICE_ONLINE_SECONDS = 10.0
CAPTURE_KINDS = ("camera", "screen", "face")
CAPTURE_KIND_SET = set(CAPTURE_KINDS)


app = FastAPI(title="TurboPi Camera Snapshot", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def latest_image_path(kind: str) -> Path:
    return DATA_DIR / f"latest_{kind}.jpg"


def default_gpio_meta(gpio: int = 26) -> dict[str, object]:
    return {
        "available": False,
        "gpio": gpio,
        "level": "",
        "value": None,
        "source": "",
        "sampled_at": 0.0,
        "raw": "",
    }


def default_latest_meta(kind: str) -> dict[str, object]:
    return {
        "kind": kind,
        "has_image": False,
        "updated_at": 0.0,
        "device_id": "",
        "frame_id": "",
        "content_length": 0,
        "task_id": "",
        "capture_source": "",
        "capture_error": "",
        "gpio": default_gpio_meta(),
        "led1": default_gpio_meta(gpio=16),
    }


def load_latest_meta(kind: str) -> dict[str, object]:
    meta = default_latest_meta(kind)
    image = latest_image_path(kind)
    if image.exists():
        meta.update(
            {
                "has_image": True,
                "updated_at": image.stat().st_mtime,
                "content_length": image.stat().st_size,
                "capture_source": "server-cache",
            }
        )
    return meta


state = {
    "tasks": {kind: None for kind in CAPTURE_KINDS},
    "updated_at": 0.0,
}

latest_meta_by_kind = {kind: load_latest_meta(kind) for kind in CAPTURE_KINDS}

gpio_status = {
    "available": False,
    "gpio": 26,
    "level": "",
    "value": None,
    "source": "",
    "sampled_at": 0.0,
    "reported_at": 0.0,
    "device_id": "",
    "raw": "",
}

sonar_status = {
    "available": False,
    "front_distance_estimate_cm": None,
    "confidence": 0.0,
    "source": "",
    "sampled_at": 0.0,
    "reported_at": 0.0,
    "device_id": "",
    "raw": "",
}

latest_inspection = {
    "available": False,
    "reported_at": 0.0,
    "device_id": "",
    "task_id": "",
    "hostname": "",
    "uptime": "",
    "temperature_c": None,
    "cpu_usage_percent": None,
    "cpu_frequency_mhz": "",
    "memory": "",
    "throttled": "",
    "wifi_ssid": "",
    "ip_address": "",
    "gateway": "",
    "disk": "",
    "sender_service": "",
    "load_average": "",
}
if INSPECTION_FILE.exists():
    try:
        saved_inspection = json.loads(INSPECTION_FILE.read_text(encoding="utf-8"))
        if isinstance(saved_inspection, dict):
            latest_inspection.update(saved_inspection)
    except (OSError, json.JSONDecodeError):
        pass


def require_token(x_camera_token: str | None) -> None:
    token_file = BASE_DIR / ".camera_token"
    data_token_file = DATA_DIR / ".camera_token"
    expected = ""
    if data_token_file.exists():
        expected = data_token_file.read_text(encoding="utf-8").strip()
    elif token_file.exists():
        expected = token_file.read_text(encoding="utf-8").strip()
    if not expected:
        return
    if not x_camera_token or not UPLOAD_TOKEN(x_camera_token, expected):
        raise HTTPException(status_code=401, detail="invalid camera token")


def normalize_kind(value: object | None, default: str = "camera") -> str:
    kind = str(value or default).strip().lower()
    if kind == "screenshot":
        kind = "screen"
    if kind not in CAPTURE_KIND_SET:
        raise HTTPException(status_code=400, detail="kind must be camera, screen, or face")
    return kind


def infer_kind_from_source(source: str, task_id: str = "") -> str:
    source = source.strip().lower()
    if source in {"face", "face-screenshot", "smile-face-render"}:
        return "face"
    if source in {"screen", "screenshot", "inspect"}:
        return "screen"
    if task_id:
        for kind, task in state["tasks"].items():
            if isinstance(task, dict) and task.get("id") == task_id:
                return kind
    return "camera"


def mode_to_kind_and_mode(mode_value: object, kind_value: object | None) -> tuple[str, str]:
    raw_mode = str(mode_value or "single").strip().lower()
    if raw_mode == "screenshot":
        return "screen", "single"
    if raw_mode == "face":
        return "face", "single"
    if raw_mode == "inspect":
        return "screen", "inspect"

    kind = normalize_kind(kind_value, "camera")
    if raw_mode not in {"single", "continuous"}:
        raise HTTPException(status_code=400, detail="mode must be single, screenshot, face, inspect, or continuous")
    if raw_mode == "continuous" and kind != "camera":
        raise HTTPException(status_code=400, detail="continuous mode is only supported for camera")
    return kind, raw_mode


def refresh_task_status(kind: str | None = None) -> None:
    kinds = [normalize_kind(kind)] if kind else CAPTURE_KINDS
    now = time.time()
    for item in kinds:
        task = state["tasks"].get(item)
        if not isinstance(task, dict):
            continue
        if task.get("status") in {"complete", "expired", "stopped", "failed"}:
            continue
        if now > float(task.get("deadline_at") or 0):
            task["status"] = "expired"
            state["updated_at"] = now


def task_snapshot(task: object) -> object:
    return dict(task) if isinstance(task, dict) else None


def control_snapshot(kind: str | None = None) -> dict[str, object]:
    refresh_task_status(kind)
    tasks = {item: task_snapshot(state["tasks"].get(item)) for item in CAPTURE_KINDS}
    if kind:
        selected = normalize_kind(kind)
        return {"task": tasks[selected], "tasks": tasks, "updated_at": state["updated_at"]}
    return {"task": tasks["camera"], "tasks": tasks, "updated_at": state["updated_at"]}


def update_task_status(kind: str, task_id: str, status: str, capture_error: str = "") -> dict[str, object]:
    task = state["tasks"].get(kind)
    now = time.time()
    if isinstance(task, dict) and task_id and task.get("id") == task_id:
        if status not in {"pending", "active", "complete", "expired", "stopped", "failed"}:
            raise HTTPException(status_code=400, detail="status is invalid")
        task["status"] = status
        if capture_error:
            task["capture_error"] = capture_error[:300]
        state["updated_at"] = now

    latest_meta_by_kind[kind].update(
        {
            "kind": kind,
            "updated_at": now,
            "task_id": task_id,
            "capture_error": capture_error[:300],
        }
    )
    return task_snapshot(state["tasks"].get(kind)) or {}


def sonar_snapshot() -> dict[str, object]:
    data = dict(sonar_status)
    reported_at = float(data.get("reported_at") or 0)
    data["age_seconds"] = time.time() - reported_at if reported_at else 0.0
    return data


def normalize_gpio(value: object, default: int = 26) -> int:
    try:
        gpio = int(value)
    except (TypeError, ValueError):
        return default
    if 0 <= gpio <= 53:
        return gpio
    raise HTTPException(status_code=400, detail="query_gpio must be an integer from 0 to 53")


def latest_seen_at() -> float:
    latest_frame_at = max(float(meta.get("updated_at") or 0) for meta in latest_meta_by_kind.values())
    return max(float(gpio_status.get("reported_at") or 0), latest_frame_at)


def device_status() -> dict[str, object]:
    last_seen_at = latest_seen_at()
    age_seconds = time.time() - last_seen_at if last_seen_at else 0.0
    device_id = str(gpio_status.get("device_id") or "")
    if not device_id:
        for kind in CAPTURE_KINDS:
            device_id = str(latest_meta_by_kind[kind].get("device_id") or "")
            if device_id:
                break
    return {
        "online": bool(last_seen_at and age_seconds <= DEVICE_ONLINE_SECONDS),
        "device_id": device_id,
        "last_seen_at": last_seen_at,
        "age_seconds": age_seconds,
        "online_threshold_seconds": DEVICE_ONLINE_SECONDS,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "has_image": latest_meta_by_kind["camera"]["has_image"],
        "captures": {kind: dict(meta) for kind, meta in latest_meta_by_kind.items()},
        "device": device_status(),
        "sonar": sonar_snapshot(),
    }


@app.get("/api/control")
def get_control(kind: str | None = Query(default=None)) -> dict[str, object]:
    return control_snapshot(kind)


@app.get("/api/gpio")
def latest_gpio() -> dict[str, object]:
    return dict(gpio_status)


@app.get("/api/device")
def latest_device() -> dict[str, object]:
    return device_status()


@app.get("/api/sonar")
def latest_sonar() -> dict[str, object]:
    return sonar_snapshot()


@app.post("/api/sonar")
async def upload_sonar_status(
    payload: dict[str, object],
    x_camera_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    require_token(x_camera_token)
    distance_raw = payload.get("front_distance_estimate_cm")
    distance_cm = None
    if distance_raw is not None:
        try:
            distance_cm = float(distance_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="front_distance_estimate_cm must be numeric")
        if distance_cm < 0 or distance_cm > 1000:
            raise HTTPException(status_code=400, detail="front_distance_estimate_cm must be between 0 and 1000")
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))
    sonar_status.update(
        {
            "available": bool(payload.get("available")) and distance_cm is not None,
            "front_distance_estimate_cm": distance_cm,
            "confidence": confidence,
            "source": str(payload.get("source") or ""),
            "sampled_at": float(payload.get("sampled_at") or 0),
            "reported_at": time.time(),
            "device_id": str(payload.get("device_id") or ""),
            "raw": str(payload.get("raw") or payload.get("error") or "")[:300],
        }
    )
    return {"ok": True, "sonar": sonar_snapshot()}


@app.get("/api/inspection")
def latest_inspection_status() -> dict[str, object]:
    return dict(latest_inspection)


@app.post("/api/gpio")
async def upload_gpio_status(
    payload: dict[str, object],
    x_camera_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    require_token(x_camera_token)
    gpio = normalize_gpio(payload.get("gpio"), 26)
    value = payload.get("value")
    level = str(payload.get("level") or "")
    if value not in {0, 1, None}:
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = None
    if value not in {0, 1}:
        value = None
    gpio_status.update(
        {
            "available": bool(payload.get("available")),
            "gpio": gpio,
            "level": level,
            "value": value,
            "source": str(payload.get("source") or ""),
            "sampled_at": float(payload.get("sampled_at") or 0),
            "reported_at": time.time(),
            "device_id": str(payload.get("device_id") or ""),
            "raw": str(payload.get("raw") or payload.get("error") or "")[:300],
        }
    )
    return {"ok": True, "gpio": dict(gpio_status)}


@app.post("/api/inspection")
async def upload_inspection(
    payload: dict[str, object],
    x_camera_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    require_token(x_camera_token)
    latest_inspection.update(
        {
            "available": True,
            "reported_at": time.time(),
            "device_id": str(payload.get("device_id") or ""),
            "task_id": str(payload.get("task_id") or ""),
            "hostname": str(payload.get("hostname") or ""),
            "uptime": str(payload.get("uptime") or ""),
            "temperature_c": payload.get("temperature_c"),
            "cpu_usage_percent": payload.get("cpu_usage_percent"),
            "cpu_frequency_mhz": str(payload.get("cpu_frequency_mhz") or ""),
            "memory": str(payload.get("memory") or ""),
            "throttled": str(payload.get("throttled") or ""),
            "wifi_ssid": str(payload.get("wifi_ssid") or ""),
            "ip_address": str(payload.get("ip_address") or ""),
            "gateway": str(payload.get("gateway") or ""),
            "disk": str(payload.get("disk") or ""),
            "sender_service": str(payload.get("sender_service") or ""),
            "load_average": str(payload.get("load_average") or ""),
        }
    )
    INSPECTION_FILE.write_text(json.dumps(latest_inspection, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "inspection": dict(latest_inspection)}


@app.post("/api/capture")
def create_capture_task(payload: dict[str, object]) -> dict[str, object]:
    kind, mode = mode_to_kind_and_mode(payload.get("mode"), payload.get("kind"))
    query_gpio = normalize_gpio(payload.get("query_gpio"), 26)
    now = time.time()
    if mode in {"single", "inspect"}:
        max_frames = 1
        if mode == "inspect":
            duration_seconds = 20
        elif kind == "camera":
            duration_seconds = 30
        else:
            duration_seconds = 10
        interval_ms = 0
    elif mode == "continuous":
        try:
            interval_ms = int(payload.get("interval_ms") or 1000)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="interval_ms must be an integer")
        interval_ms = max(200, min(interval_ms, 10000))
        max_frames = 0
        duration_seconds = 24 * 60 * 60
    else:
        raise HTTPException(status_code=400, detail="mode must be single, screenshot, face, inspect, or continuous")

    task = {
        "id": f"{int(now * 1000)}-{secrets.token_hex(3)}",
        "kind": kind,
        "mode": mode,
        "status": "pending",
        "requested_at": now,
        "deadline_at": now + duration_seconds,
        "max_frames": max_frames,
        "uploaded_frames": 0,
        "interval_ms": interval_ms,
        "query_gpio": query_gpio,
    }
    state["tasks"][kind] = task
    state["updated_at"] = time.time()
    return {"ok": True, "task": task, "tasks": {item: task_snapshot(state["tasks"].get(item)) for item in CAPTURE_KINDS}}


@app.post("/api/stop")
async def stop_capture_task(request: Request, kind: str | None = Query(default=None)) -> dict[str, object]:
    selected_kind = kind
    if selected_kind is None:
        try:
            payload = await request.json()
            if isinstance(payload, dict):
                selected_kind = str(payload.get("kind") or "") or None
        except Exception:
            selected_kind = None
    selected = normalize_kind(selected_kind, "camera")
    task = state["tasks"].get(selected)
    if isinstance(task, dict) and task.get("status") not in {"complete", "expired", "stopped", "failed"}:
        task["status"] = "stopped"
    state["updated_at"] = time.time()
    return {"ok": True, "kind": selected, "task": state["tasks"].get(selected), "tasks": {item: task_snapshot(state["tasks"].get(item)) for item in CAPTURE_KINDS}}


@app.post("/api/task-status")
async def report_task_status(
    payload: dict[str, object],
    x_camera_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    require_token(x_camera_token)
    task_id = str(payload.get("task_id") or "")
    kind = normalize_kind(payload.get("kind"))
    status = str(payload.get("status") or "").strip().lower()
    capture_error = str(payload.get("capture_error") or payload.get("error") or "")
    capture_source = str(payload.get("capture_source") or "")
    device_id = str(payload.get("device_id") or "")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    task = update_task_status(kind, task_id, status, capture_error)
    if capture_source or device_id:
        latest_meta_by_kind[kind].update(
            {
                "capture_source": capture_source[:80],
                "device_id": device_id,
            }
        )
    return {"ok": True, "kind": kind, "task": task, "latest": dict(latest_meta_by_kind[kind])}


@app.post("/api/frame")
async def upload_frame(
    request: Request,
    x_device_id: Annotated[str, Header()] = "turbopi",
    x_frame_id: Annotated[str, Header()] = "",
    x_task_id: Annotated[str, Header()] = "",
    x_gpio_available: Annotated[str | None, Header()] = None,
    x_gpio_number: Annotated[str | None, Header()] = None,
    x_gpio_level: Annotated[str | None, Header()] = None,
    x_gpio_value: Annotated[str | None, Header()] = None,
    x_gpio_source: Annotated[str | None, Header()] = None,
    x_gpio_sampled_at: Annotated[str | None, Header()] = None,
    x_gpio_raw: Annotated[str | None, Header()] = None,
    x_led1_available: Annotated[str | None, Header()] = None,
    x_led1_gpio: Annotated[str | None, Header()] = None,
    x_led1_level: Annotated[str | None, Header()] = None,
    x_led1_value: Annotated[str | None, Header()] = None,
    x_led1_source: Annotated[str | None, Header()] = None,
    x_led1_sampled_at: Annotated[str | None, Header()] = None,
    x_led1_raw: Annotated[str | None, Header()] = None,
    x_capture_kind: Annotated[str | None, Header()] = None,
    x_capture_source: Annotated[str | None, Header()] = None,
    x_capture_error: Annotated[str | None, Header()] = None,
    x_camera_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    require_token(x_camera_token)

    content_type = (request.headers.get("content-type") or "").lower()
    if content_type not in {"image/jpeg", "image/jpg"}:
        raise HTTPException(status_code=415, detail="only JPEG frames are accepted")

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="empty image")
    if len(raw) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image too large")

    if x_capture_kind:
        kind = normalize_kind(x_capture_kind)
    else:
        kind = infer_kind_from_source(x_capture_source or "", x_task_id)

    tmp_file = DATA_DIR / f"latest_{kind}.tmp"
    image_file = latest_image_path(kind)
    tmp_file.write_bytes(raw)
    tmp_file.replace(image_file)

    gpio_meta = {
        "available": x_gpio_available == "1",
        "gpio": int(x_gpio_number) if x_gpio_number and x_gpio_number.isdigit() else None,
        "level": x_gpio_level or "",
        "value": int(x_gpio_value) if x_gpio_value in {"0", "1"} else None,
        "source": x_gpio_source or "",
        "sampled_at": float(x_gpio_sampled_at) if x_gpio_sampled_at else 0.0,
        "raw": x_gpio_raw or "",
    }
    if gpio_meta["gpio"] is None:
        gpio_meta = {
            "available": x_led1_available == "1",
            "gpio": int(x_led1_gpio) if x_led1_gpio and x_led1_gpio.isdigit() else None,
            "level": x_led1_level or "",
            "value": int(x_led1_value) if x_led1_value in {"0", "1"} else None,
            "source": x_led1_source or "",
            "sampled_at": float(x_led1_sampled_at) if x_led1_sampled_at else 0.0,
            "raw": x_led1_raw or "",
        }
    led1_meta = gpio_meta if gpio_meta.get("gpio") == 16 else {
        "available": x_led1_available == "1",
        "gpio": int(x_led1_gpio) if x_led1_gpio and x_led1_gpio.isdigit() else None,
        "level": x_led1_level or "",
        "value": int(x_led1_value) if x_led1_value in {"0", "1"} else None,
        "source": x_led1_source or "",
        "sampled_at": float(x_led1_sampled_at) if x_led1_sampled_at else 0.0,
        "raw": x_led1_raw or "",
    }

    latest_meta_by_kind[kind].update(
        {
            "kind": kind,
            "has_image": True,
            "updated_at": time.time(),
            "device_id": x_device_id,
            "frame_id": x_frame_id,
            "task_id": x_task_id,
            "content_length": len(raw),
            "capture_source": (x_capture_source or "")[:80],
            "capture_error": (x_capture_error or "")[:300],
            "gpio": gpio_meta,
            "led1": led1_meta,
            "sonar": sonar_snapshot(),
        }
    )
    gpio_status.update({**gpio_meta, "reported_at": time.time(), "device_id": x_device_id})

    task = state["tasks"].get(kind)
    if isinstance(task, dict) and x_task_id and x_task_id == task.get("id"):
        if time.time() > float(task.get("deadline_at") or 0):
            task["status"] = "expired"
        else:
            task["uploaded_frames"] = int(task.get("uploaded_frames") or 0) + 1
            max_frames = int(task.get("max_frames") or 0)
            task["status"] = "complete" if max_frames and task["uploaded_frames"] >= max_frames else "active"
        state["updated_at"] = time.time()

    return {"ok": True, **latest_meta_by_kind[kind]}


@app.get("/api/latest")
def latest(kind: str = Query(default="camera")) -> dict[str, object]:
    selected = normalize_kind(kind)
    return dict(latest_meta_by_kind[selected])


@app.get("/api/latest.jpg", response_model=None)
def latest_image(kind: str = Query(default="camera")):
    selected = normalize_kind(kind)
    image = latest_image_path(selected)
    if not image.exists():
        return JSONResponse({"error": "no_image", "kind": selected}, status_code=404)
    return FileResponse(
        image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
