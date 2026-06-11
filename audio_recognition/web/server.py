from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Annotated, Any

import requests
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from audio_recognition.core.envelope import DecisionEnvelope
from audio_recognition.harness.react_loop import route_transcript
from audio_recognition.storage.case_store import append_case, build_case_id, copy_audio_file, find_case, load_cases, write_audio_bytes
from audio_recognition.storage.envelope_store import list_envelopes, load_envelope, save_envelope
from audio_recognition.storage.replay import replay_envelope


PACKAGE_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
DATA_DIR = PACKAGE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
RESULTS_FILE = DATA_DIR / "results.json"
EVENTS_FILE = DATA_DIR / "events.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
TOKEN_FILE = DATA_DIR / ".audio_token"
ACTION_CATALOG_PATH = (PACKAGE_DIR / "../action_move/skill_catalog.json").resolve()
SKILL_REGISTRY_PATH = (PACKAGE_DIR / "skills/registry.yaml").resolve()
ACTION_SERVER = os.getenv("AUDIO_ACTION_SERVER", "http://action-move:8094")
FACE_SERVER = os.getenv("AUDIO_FACE_SERVER", "http://127.0.0.1:8096")
CAMERA_SERVER = os.getenv("AUDIO_CAMERA_SERVER", "http://camera-snapshot:8099")
COMMON_ASR_URL = os.getenv("COMMON_ASR_URL", "https://www.wangyutang.cn/common/api/asr/transcribe")
MAX_RESULTS = 100
MAX_EVENTS = 200
MAX_DASHBOARD_RESULTS = 40
MAX_DASHBOARD_EVENTS = 60
DATA_LOCK = threading.Lock()
HTTP_TIMEOUT_SECONDS = 3.0

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="TurboPi Audio Recognition", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AudioResult(BaseModel):
    device_id: str = Field("turbopi-01", max_length=80)
    text: str = Field("", max_length=2000)
    wav_path: str = Field("", max_length=500)
    skill_id: str = Field("", max_length=80)
    audio_base64: str = Field("", max_length=12000000)
    audio_mime: str = Field("audio/wav", max_length=120)
    audio_filename: str = Field("", max_length=255)
    audio_url: str = Field("", max_length=500)
    audio_duration_seconds: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)
    reported_at: float = 0.0


class PipelineEvent(BaseModel):
    device_id: str = Field("turbopi-01", max_length=80)
    stage: str = Field(..., max_length=80)
    status: str = Field("ok", max_length=40)
    message: str = Field("", max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)
    reported_at: float = 0.0


class TextCommand(BaseModel):
    device_id: str = Field("web-audio", max_length=80)
    text: str = Field(..., max_length=2000)
    wav_path: str = Field("", max_length=500)
    source: str = Field("web-recording", max_length=80)
    route_action: bool = True
    raw: dict[str, Any] = Field(default_factory=dict)


class SimulationCommand(BaseModel):
    device_id: str = Field("offline-sim", max_length=80)
    text: str = Field(..., max_length=2000)
    source: str = Field("offline-simulation", max_length=80)
    raw: dict[str, Any] = Field(default_factory=dict)


class AudioSettings(BaseModel):
    input_mode: str = Field("wonderechopro", pattern="^(web_input|wonderechopro)$")
    manual_recording_enabled: bool = False


DEFAULT_SETTINGS = {
    "input_mode": "wonderechopro",
    "manual_recording_enabled": False,
}


def build_router_config() -> dict[str, Any]:
    config: dict[str, Any] = {
        "skill_catalog": str(ACTION_CATALOG_PATH),
        "skill_registry": str(os.getenv("AUDIO_SKILL_REGISTRY", str(SKILL_REGISTRY_PATH))).strip(),
    }
    react_llm = {
        "endpoint": str(os.getenv("AUDIO_REACT_LLM_ENDPOINT", "https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat/completions")).strip(),
        "model": str(os.getenv("AUDIO_REACT_LLM_MODEL", "qwen3-32b")).strip(),
        "timeout_seconds": float(os.getenv("AUDIO_REACT_LLM_TIMEOUT_SECONDS", "90") or 90),
        "retries": int(os.getenv("AUDIO_REACT_LLM_RETRIES", "2") or 2),
    }
    config["react_agent"] = {
        "mode": str(os.getenv("AUDIO_REACT_AGENT_MODE", "llm") or "llm").strip().lower(),
        "max_steps": int(os.getenv("AUDIO_REACT_MAX_STEPS", "8") or 8),
        "prompt_path": str(os.getenv("AUDIO_REACT_SYSTEM_PROMPT", "agent/prompts/walle_system_prompt.md") or "agent/prompts/walle_system_prompt.md").strip(),
        "llm": react_llm,
    }
    return config


def expected_token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def require_token(x_audio_token: Annotated[str | None, Header()] = None) -> None:
    expected = expected_token()
    if not expected:
        return
    if not x_audio_token or not secrets.compare_digest(x_audio_token, expected):
        raise HTTPException(status_code=401, detail="invalid audio token")


def load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data, _ = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError:
            backup = path.with_suffix(f"{path.suffix}.corrupt-{int(time.time())}.bak")
            path.replace(backup)
            return []
    return data if isinstance(data, list) else []


def write_json_list(path: Path, items: list[dict[str, Any]], limit: int) -> None:
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(items[-limit:], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def load_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)
    merged = {**DEFAULT_SETTINGS, **data}
    return AudioSettings(**merged).model_dump()


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = AudioSettings(**{**load_settings(), **settings}).model_dump()
    tmp_path = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(SETTINGS_FILE)
    return normalized


def load_results() -> list[dict[str, Any]]:
    return load_json_list(RESULTS_FILE)


def load_events() -> list[dict[str, Any]]:
    return load_json_list(EVENTS_FILE)


def save_results(results: list[dict[str, Any]]) -> None:
    write_json_list(RESULTS_FILE, results, MAX_RESULTS)


def save_events(events: list[dict[str, Any]]) -> None:
    write_json_list(EVENTS_FILE, events, MAX_EVENTS)


def append_event(item: dict[str, Any]) -> dict[str, Any]:
    if not item.get("reported_at"):
        item["reported_at"] = time.time()
    with DATA_LOCK:
        events = load_events()
        events.append(item)
        save_events(events)
    return item


def append_result(item: dict[str, Any]) -> dict[str, Any]:
    if not item.get("reported_at"):
        item["reported_at"] = time.time()
    with DATA_LOCK:
        results = load_results()
        results.append(item)
        save_results(results)
    return item


def sanitize_audio_filename(name: str) -> str:
    candidate = Path(name or "recording.wav").name
    suffix = Path(candidate).suffix.lower()
    if suffix in {".wav", ".mp3", ".ogg", ".m4a", ".flac"}:
        return candidate
    stem = Path(candidate).stem or "recording"
    return f"{stem}.wav"


def save_embedded_audio(payload: dict[str, Any]) -> tuple[str, float]:
    audio_base64 = str(payload.get("audio_base64") or "").strip()
    if not audio_base64:
        return str(payload.get("audio_url") or ""), float(payload.get("audio_duration_seconds") or 0.0)

    try:
        content = base64.b64decode(audio_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid audio_base64 payload: {exc}") from exc

    filename = sanitize_audio_filename(str(payload.get("audio_filename") or payload.get("wav_path") or "recording.wav"))
    stored_name = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}-{filename}"
    stored_path = UPLOADS_DIR / stored_name
    stored_path.write_bytes(content)

    duration = float(payload.get("audio_duration_seconds") or 0.0)
    if duration <= 0 and stored_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(stored_path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
            duration = frames / rate if rate else 0.0
        except wave.Error:
            duration = 0.0

    payload["audio_base64"] = ""
    payload["audio_filename"] = filename
    payload["audio_url"] = f"/audio/api/audio/{stored_name}"
    payload["audio_duration_seconds"] = duration
    return payload["audio_url"], duration


def local_audio_path_from_url(audio_url: str) -> Path | None:
    prefix = "/audio/api/audio/"
    if not audio_url.startswith(prefix):
        return None
    target = (UPLOADS_DIR / Path(audio_url.removeprefix(prefix)).name).resolve()
    if target.parent != UPLOADS_DIR.resolve() or not target.exists():
        return None
    return target


def record_intermediate_case(
    *,
    source: str,
    device_id: str,
    text: str = "",
    wav_path: str = "",
    audio_url: str = "",
    audio_info: dict[str, Any] | None = None,
    raw_asr: dict[str, Any] | None = None,
    routed: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    route_action: bool = False,
    notes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case_id = build_case_id("audio")
    audio = dict(audio_info or {})
    source_path = Path(wav_path) if wav_path else None
    if not audio and source_path and source_path.exists():
        audio = copy_audio_file(data_dir=DATA_DIR, case_id=case_id, source_path=source_path)
    if not audio and audio_url:
        local_audio = local_audio_path_from_url(audio_url)
        if local_audio:
            audio = copy_audio_file(data_dir=DATA_DIR, case_id=case_id, source_path=local_audio)
    if not audio and not raw_asr:
        return {}
    result_meta = {}
    if result:
        result_meta = {
            "device_id": result.get("device_id", ""),
            "text": result.get("text", ""),
            "wav_path": result.get("wav_path", ""),
            "skill_id": result.get("skill_id", ""),
            "audio_url": result.get("audio_url", ""),
            "audio_duration_seconds": result.get("audio_duration_seconds", 0.0),
            "reported_at": result.get("reported_at", 0.0),
        }
    item = {
        "case_id": case_id,
        "envelope_id": str((routed or {}).get("envelope", {}).get("envelope_id", "")),
        "source": source,
        "device_id": device_id,
        "text": text,
        "wav_path": wav_path,
        "audio_url": audio_url,
        "audio": audio,
        "raw_asr": raw_asr or {},
        "plan": (routed or {}).get("plan"),
        "skill_id": str((routed or {}).get("skill_id") or (result or {}).get("skill_id") or ""),
        "route_action": route_action,
        "action_task": (routed or {}).get("action_task"),
        "action_error": (routed or {}).get("action_error", ""),
        "face_task": (routed or {}).get("face_task"),
        "face_error": (routed or {}).get("face_error", ""),
        "result": result_meta,
        "notes": notes or {},
    }
    return append_case(DATA_DIR, item)


def simulate_route_text(*, text: str, device_id: str, source: str, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    routed = route_transcript(
        base_dir=PACKAGE_DIR,
        text=text,
        router_config=build_router_config(),
        cloud_config={
            "face_server": FACE_SERVER,
            "face_enabled": True,
            "action_server": ACTION_SERVER,
            "action_enabled": True,
        },
        route_action=False,
        source=source,
        device_id=device_id,
    )
    return {
        "ok": True,
        "simulation": {
            "source": source,
            "device_id": device_id,
            "text": text,
            "dispatch": "disabled",
            "raw": raw or {},
        },
        "skill": {"id": routed["skill_id"]} if routed.get("skill_id") else None,
        "plan": routed.get("plan"),
        "envelope": routed.get("envelope"),
        "action_task": None,
        "action_error": "",
        "face_task": None,
        "face_error": "",
    }


def fetch_json(url: str) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str) -> tuple[bytes, str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"Accept": "image/jpeg,application/json"})
    with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read(), response.headers.get("content-type") or "application/octet-stream"


def action_snapshot() -> dict[str, Any]:
    base = ACTION_SERVER.rstrip("/")
    snapshot: dict[str, Any] = {
        "server": ACTION_SERVER,
        "online": False,
        "health": None,
        "tasks": [],
        "error": "",
    }
    if not base:
        snapshot["error"] = "AUDIO_ACTION_SERVER is empty"
        return snapshot
    try:
        snapshot["health"] = fetch_json(f"{base}/api/health")
        tasks_payload = fetch_json(f"{base}/api/tasks")
        snapshot["tasks"] = tasks_payload.get("tasks", [])
        snapshot["online"] = True
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        snapshot["error"] = str(exc)
    return snapshot


def camera_snapshot() -> dict[str, Any]:
    base = CAMERA_SERVER.rstrip("/")
    snapshot: dict[str, Any] = {
        "server": CAMERA_SERVER,
        "online": False,
        "health": None,
        "latest": None,
        "control": None,
        "error": "",
    }
    if not base:
        snapshot["error"] = "AUDIO_CAMERA_SERVER is empty"
        return snapshot
    try:
        snapshot["health"] = fetch_json(f"{base}/api/health")
        snapshot["latest"] = fetch_json(f"{base}/api/latest")
        snapshot["control"] = fetch_json(f"{base}/api/control")
        snapshot["online"] = True
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        snapshot["error"] = str(exc)
    return snapshot


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    results = load_results()
    events = load_events()
    latest = results[-1] if results else None
    latest_event = events[-1] if events else None
    age = time.time() - float(latest.get("reported_at") or 0) if latest else 0
    return {
        "status": "ok",
        "service": "TurboPi Audio Recognition",
        "has_result": bool(latest),
        "latest": latest,
        "latest_event": latest_event,
        "age_seconds": age,
        "router": build_router_config(),
    }


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    results = load_results()
    events = load_events()
    latest = results[-1] if results else None
    latest_event = events[-1] if events else None
    age = time.time() - float(latest.get("reported_at") or 0) if latest else 0
    return {
        "status": "ok",
        "service": "TurboPi Audio Recognition",
        "action_server": ACTION_SERVER,
        "latest": latest,
        "latest_event": latest_event,
        "age_seconds": age,
        "results": list(reversed(results[-MAX_DASHBOARD_RESULTS:])),
        "events": list(reversed(events[-MAX_DASHBOARD_EVENTS:])),
        "action": action_snapshot(),
        "camera": camera_snapshot(),
        "settings": load_settings(),
        "router": build_router_config(),
        "server_time": time.time(),
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"settings": load_settings()}


@app.post("/api/settings")
def update_settings(payload: AudioSettings, x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    require_token(x_audio_token)
    updates = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    settings = save_settings(updates)
    append_event(
        {
            "device_id": "cloud",
            "stage": "input_mode",
            "status": "ok",
            "message": f"input mode updated to {settings['input_mode']}",
            "details": settings,
            "reported_at": time.time(),
        }
    )
    return {"ok": True, "settings": settings}


@app.post("/api/manual-recording/start")
def start_manual_recording(x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    require_token(x_audio_token)
    settings = save_settings({"manual_recording_enabled": True})
    append_event(
        {
            "device_id": "cloud",
            "stage": "manual_recording",
            "status": "running",
            "message": "manual WonderEchoPro recording started",
            "details": settings,
            "reported_at": time.time(),
        }
    )
    return {"ok": True, "settings": settings}


@app.post("/api/manual-recording/stop")
def stop_manual_recording(x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    require_token(x_audio_token)
    settings = save_settings({"manual_recording_enabled": False})
    append_event(
        {
            "device_id": "cloud",
            "stage": "manual_recording",
            "status": "stopped",
            "message": "manual WonderEchoPro recording stopped",
            "details": settings,
            "reported_at": time.time(),
        }
    )
    return {"ok": True, "settings": settings}


@app.post("/api/tasks/clear")
def clear_action_tasks() -> dict[str, Any]:
    try:
        return post_json(f"{ACTION_SERVER.rstrip('/')}/api/tasks/clear", {})
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"failed to clear action tasks: {exc}") from exc


@app.post("/api/camera/open")
def open_camera() -> dict[str, Any]:
    try:
        return post_json(f"{CAMERA_SERVER.rstrip('/')}/api/capture", {"mode": "continuous", "interval_ms": 1000})
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"failed to open camera: {exc}") from exc


@app.post("/api/camera/close")
def close_camera() -> dict[str, Any]:
    try:
        return post_json(f"{CAMERA_SERVER.rstrip('/')}/api/stop", {})
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"failed to close camera: {exc}") from exc


@app.get("/api/camera/latest.jpg")
def latest_camera_image() -> Response:
    try:
        content, content_type = fetch_bytes(f"{CAMERA_SERVER.rstrip('/')}/api/latest.jpg")
        return Response(content=content, media_type=content_type, headers={"Cache-Control": "no-store"})
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=exc.code, detail="camera image unavailable") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise HTTPException(status_code=502, detail=f"failed to fetch camera image: {exc}") from exc


@app.get("/api/results")
def list_results() -> dict[str, Any]:
    return {"results": list(reversed(load_results()))}


@app.get("/api/audio/{name}")
def get_uploaded_audio(name: str) -> FileResponse:
    safe_name = Path(name).name
    target = (UPLOADS_DIR / safe_name).resolve()
    if target.parent != UPLOADS_DIR.resolve() or not target.exists():
        raise HTTPException(status_code=404, detail="audio not found")
    media_type = "audio/wav" if target.suffix.lower() == ".wav" else "application/octet-stream"
    return FileResponse(target, media_type=media_type, filename=safe_name, headers={"Cache-Control": "no-store"})


@app.get("/api/events")
def list_events() -> dict[str, Any]:
    return {"events": list(reversed(load_events()))}


@app.get("/api/intermediate/cases")
def list_intermediate_cases(limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 500))
    return {"cases": list(reversed(load_cases(DATA_DIR, limit=safe_limit)))}


@app.get("/api/intermediate/cases/{case_id}")
def get_intermediate_case(case_id: str) -> dict[str, Any]:
    item = find_case(DATA_DIR, case_id)
    if not item:
        raise HTTPException(status_code=404, detail="case not found")
    return {"case": item}


@app.get("/api/envelopes")
def api_list_envelopes(limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 500))
    return {"envelopes": list(reversed(list_envelopes(DATA_DIR, limit=safe_limit)))}


@app.get("/api/envelopes/{envelope_id}")
def api_get_envelope(envelope_id: str) -> dict[str, Any]:
    envelope = load_envelope(DATA_DIR, envelope_id)
    if not envelope:
        raise HTTPException(status_code=404, detail="envelope not found")
    return {"envelope": envelope.model_dump()}


@app.post("/api/simulate/text")
def simulate_text(payload: SimulationCommand) -> dict[str, Any]:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty simulation text")
    return simulate_route_text(text=text, device_id=payload.device_id, source=payload.source, raw=payload.raw)


@app.post("/api/intermediate/cases/{case_id}/replay")
def replay_intermediate_case(case_id: str) -> dict[str, Any]:
    item = find_case(DATA_DIR, case_id)
    if not item:
        raise HTTPException(status_code=404, detail="case not found")
    text = str(item.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="case has no recognized text to replay")
    raw_asr = item.get("raw_asr") if isinstance(item.get("raw_asr"), dict) else {}
    return simulate_route_text(text=text, device_id=str(item.get("device_id") or "offline-sim"), source="case-replay", raw=raw_asr)


@app.post("/api/envelopes/{envelope_id}/replay")
def api_replay_envelope(envelope_id: str, replay_from: str = "text") -> dict[str, Any]:
    if replay_from not in {"text", "tool_calls", "tasks"}:
        raise HTTPException(status_code=400, detail="replay_from must be one of text/tool_calls/tasks")
    try:
        return replay_envelope(
            data_dir=DATA_DIR,
            envelope_id=envelope_id,
            base_dir=PACKAGE_DIR,
            router_config=build_router_config(),
            replay_from=replay_from,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="envelope not found") from exc


@app.post("/api/results")
def add_result(payload: AudioResult, x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    require_token(x_audio_token)
    item = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    text = str(item.get("text") or "").strip()
    routed = route_transcript(
        base_dir=PACKAGE_DIR,
        text=text,
        router_config=build_router_config(),
        cloud_config={
            "face_server": FACE_SERVER,
            "face_enabled": True,
            "action_server": ACTION_SERVER,
            "action_enabled": True,
        },
        route_action=text != "noise_or_unrecognized_audio",
        source="audio_result",
        device_id=str(item.get("device_id") or "turbopi-01"),
    )
    plan = routed.get("plan")
    if routed.get("skill_id"):
        item["skill_id"] = str(routed["skill_id"])
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    item["raw"] = {
        **raw,
        "plan": plan,
        "action_task": routed.get("action_task"),
        "action_error": routed.get("action_error", ""),
        "face_task": routed.get("face_task"),
        "face_error": routed.get("face_error", ""),
    }
    if routed.get("face_task"):
        append_event(
            {
                "device_id": item.get("device_id") or "turbopi-01",
                "stage": "face_route",
                "status": "ok",
                "message": f"routed result to face {routed['skill_id']}",
                "details": {"state": routed["face_task"].get("state", routed["face_task"]), "plan": plan},
                "reported_at": time.time(),
            }
        )
    if routed.get("face_error"):
        append_event(
            {
                "device_id": item.get("device_id") or "turbopi-01",
                "stage": "face_route",
                "status": "failed",
                "message": str(routed["face_error"]),
                "details": {"skill_id": routed.get("skill_id", ""), "plan": plan},
                "reported_at": time.time(),
            }
        )
    save_embedded_audio(item)
    append_result(item)
    if isinstance(routed.get("envelope"), dict):
        save_envelope(DATA_DIR, DecisionEnvelope(**routed["envelope"]))
    case = record_intermediate_case(
        source="audio_result",
        device_id=str(item.get("device_id") or "turbopi-01"),
        text=text,
        wav_path=str(item.get("wav_path") or ""),
        audio_url=str(item.get("audio_url") or ""),
        raw_asr=raw.get("asr") if isinstance(raw.get("asr"), dict) else raw,
        routed=routed,
        result=item,
        route_action=text != "noise_or_unrecognized_audio",
    )
    return {
        "ok": True,
        "result": item,
        "case_id": case.get("case_id", ""),
        "skill": {"id": routed["skill_id"]} if routed.get("skill_id") else None,
        "face_task": routed.get("face_task"),
        "face_error": routed.get("face_error", ""),
        "plan": plan,
    }


@app.post("/api/events")
def add_event(payload: PipelineEvent, x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    require_token(x_audio_token)
    item = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    append_event(item)
    return {"ok": True, "event": item}


@app.post("/api/asr/transcribe")
async def proxy_asr_transcribe(
    file: UploadFile = File(...),
    language: str = Form("zh"),
) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty audio file")

    case_id = build_case_id("audio-asr")
    audio_info = write_audio_bytes(
        data_dir=DATA_DIR,
        case_id=case_id,
        content=content,
        original_filename=file.filename or "recording.wav",
        content_type=file.content_type or "audio/wav",
    )
    endpoint = COMMON_ASR_URL
    data = {"language": language or "zh"}
    files = {"file": (file.filename or "recording.wav", content, file.content_type or "audio/wav")}
    try:
        response = requests.post(endpoint, data=data, files=files, timeout=180)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        append_case(
            DATA_DIR,
            {
                "case_id": case_id,
                "source": "web-asr",
                "device_id": "web-audio",
                "text": "",
                "wav_path": "",
                "audio_url": "",
                "audio": audio_info,
                "raw_asr": {},
                "plan": None,
                "skill_id": "",
                "route_action": False,
                "action_task": None,
                "action_error": "",
                "face_task": None,
                "face_error": "",
                "result": {},
                "notes": {"language": language or "zh", "endpoint": endpoint, "error": str(exc)},
            },
        )
        append_event(
            {
                "device_id": "web-audio",
                "stage": "model_asr",
                "status": "failed",
                "message": f"ASR proxy failed: {exc}",
                "details": {"provider": "common_api", "endpoint": endpoint},
                "reported_at": time.time(),
            }
        )
        raise HTTPException(status_code=502, detail=f"ASR proxy failed: {exc}") from exc
    except ValueError as exc:
        append_case(
            DATA_DIR,
            {
                "case_id": case_id,
                "source": "web-asr",
                "device_id": "web-audio",
                "text": "",
                "wav_path": "",
                "audio_url": "",
                "audio": audio_info,
                "raw_asr": {},
                "plan": None,
                "skill_id": "",
                "route_action": False,
                "action_task": None,
                "action_error": "",
                "face_task": None,
                "face_error": "",
                "result": {},
                "notes": {"language": language or "zh", "endpoint": endpoint, "error": "non-json ASR response"},
            },
        )
        raise HTTPException(status_code=502, detail="ASR provider returned non-JSON response") from exc

    payload.setdefault("provider", "common_api")
    payload.setdefault("endpoint", endpoint)
    text = str(payload.get("text") or "").strip()
    save_envelope(
        DATA_DIR,
        DecisionEnvelope(
            device_id="web-audio",
            source="web-asr",
            transcript=text,
            audio_ref=audio_info.get("audio_path"),
            audio_meta=audio_info,
            asr_meta=payload,
            dispatch_mode="dry_run",
        ),
    )
    case = append_case(
        DATA_DIR,
        {
            "case_id": case_id,
            "source": "web-asr",
            "device_id": "web-audio",
            "text": text,
            "wav_path": "",
            "audio_url": "",
            "audio": audio_info,
            "raw_asr": payload,
            "plan": None,
            "skill_id": "",
            "route_action": False,
            "action_task": None,
            "action_error": "",
            "face_task": None,
            "face_error": "",
            "result": {},
            "notes": {"language": language or "zh", "endpoint": endpoint},
        },
    )
    payload.setdefault("case_id", case["case_id"])
    return payload


@app.post("/api/recognize-text")
def recognize_text(payload: TextCommand, x_audio_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    require_token(x_audio_token)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty recognized text")

    append_event(
        {
            "device_id": payload.device_id,
            "stage": "model_asr",
            "status": "ok",
            "message": "recognized text received from real ASR API",
            "details": {"source": payload.source, "text": text},
            "reported_at": time.time(),
        }
    )

    routed = route_transcript(
        base_dir=PACKAGE_DIR,
        text=text,
        router_config=build_router_config(),
        cloud_config={
            "face_server": FACE_SERVER,
            "face_enabled": True,
            "action_server": ACTION_SERVER,
            "action_enabled": True,
        },
        route_action=payload.route_action,
        source="audio_recognition",
        device_id=payload.device_id,
    )
    skill_id = str(routed.get("skill_id") or "")
    plan = routed.get("plan")
    if not skill_id:
        append_event(
            {
                "device_id": payload.device_id,
                "stage": "skill_route",
                "status": "skipped",
                "message": "no matching voice skill",
                "details": {"text": text, "plan": plan},
                "reported_at": time.time(),
            }
        )
    elif routed.get("face_task"):
        append_event(
            {
                "device_id": payload.device_id,
                "stage": "face_route",
                "status": "ok",
                "message": f"routed text to face {skill_id}",
                "details": {"state": routed["face_task"].get("state", routed["face_task"]), "plan": plan},
                "reported_at": time.time(),
            }
        )
    elif routed.get("face_error"):
        append_event(
            {
                "device_id": payload.device_id,
                "stage": "face_route",
                "status": "failed",
                "message": str(routed["face_error"]),
                "details": {"skill_id": skill_id, "plan": plan},
                "reported_at": time.time(),
            }
        )
    elif routed.get("action_task"):
        append_event(
            {
                "device_id": payload.device_id,
                "stage": "action_route",
                "status": "ok",
                "message": f"routed text to action {skill_id}",
                "details": {"task": routed["action_task"].get("task", routed["action_task"]), "plan": plan},
                "reported_at": time.time(),
            }
        )
    elif routed.get("action_error"):
        append_event(
            {
                "device_id": payload.device_id,
                "stage": "action_route",
                "status": "failed",
                "message": str(routed["action_error"]),
                "details": {"skill_id": skill_id, "plan": plan},
                "reported_at": time.time(),
            }
        )

    result = append_result(
        {
            "device_id": payload.device_id,
            "text": text,
            "wav_path": payload.wav_path,
            "skill_id": skill_id,
            "raw": {
                **payload.raw,
                "source": payload.source,
                "plan": plan,
                "action_task": routed.get("action_task"),
                "action_error": routed.get("action_error", ""),
                "face_task": routed.get("face_task"),
                "face_error": routed.get("face_error", ""),
            },
            "reported_at": time.time(),
        }
    )
    if isinstance(routed.get("envelope"), dict):
        save_envelope(DATA_DIR, DecisionEnvelope(**routed["envelope"]))
    raw_asr = payload.raw.get("asr") if isinstance(payload.raw.get("asr"), dict) else payload.raw
    case = record_intermediate_case(
        source=payload.source,
        device_id=payload.device_id,
        text=text,
        wav_path=payload.wav_path,
        raw_asr=raw_asr,
        routed=routed,
        result=result,
        route_action=payload.route_action,
    )
    return {
        "ok": True,
        "result": result,
        "case_id": case.get("case_id", ""),
        "skill": {"id": skill_id} if skill_id else None,
        "action_task": routed.get("action_task"),
        "action_error": routed.get("action_error", ""),
        "face_task": routed.get("face_task"),
        "face_error": routed.get("face_error", ""),
        "plan": plan,
    }
