from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import ProxyHandler, build_opener

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "app" / "static"
DATA_DIR = ROOT / "data"
REGISTRY_PATH = ROOT / os.getenv("MODULE_REGISTRY", "modules/registry.json")
HEALTH_TIMEOUT_SECONDS = float(os.getenv("MODULE_HEALTH_TIMEOUT_SECONDS", "1.5"))
VISITOR_ADMIN_CODE = os.getenv("VISITOR_ADMIN_CODE", "123")
VISITOR_REGISTRY_FILE = DATA_DIR / "visitor_registrations.jsonl"
VISITOR_EVENTS_FILE = DATA_DIR / "visitor_events.jsonl"
NO_PROXY_OPENER = build_opener(ProxyHandler({}))

DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=os.getenv("PLATFORM_TITLE", "Control Platform"))
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class VisitorRegistration(BaseModel):
    visitor_name: str = Field("", max_length=80)
    contact: str = Field("", max_length=120)
    purpose: str = Field("", max_length=300)
    note: str = Field("", max_length=500)
    consent: bool = False
    session_id: str = Field("", max_length=80)


class VisitorEvent(BaseModel):
    session_id: str = Field("", max_length=80)
    event_type: str = Field(..., max_length=80)
    page: str = Field("", max_length=300)
    target: str = Field("", max_length=200)
    detail: str = Field("", max_length=500)
    duration_seconds: float = Field(0, ge=0, le=86400)
    consent: bool = False


def load_modules() -> list[dict[str, Any]]:
    with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_jsonl(path: Path, limit: int = 300) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def request_context(request: Request) -> dict[str, Any]:
    user_agent = request.headers.get("user-agent", "")[:300]
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",", 1)[0].strip() if forwarded_for else ""
    if not client_ip and request.client:
        client_ip = request.client.host
    return {
        "client_ip": client_ip[:80],
        "user_agent": user_agent,
        "referer": request.headers.get("referer", "")[:300],
        "created_at": time.time(),
    }


def require_visitor_admin(x_visitor_admin_code: str | None = None) -> None:
    if not VISITOR_ADMIN_CODE:
        return
    if not x_visitor_admin_code or not secrets.compare_digest(x_visitor_admin_code, VISITOR_ADMIN_CODE):
        raise HTTPException(status_code=401, detail="invalid visitor admin code")


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    page_counts: dict[str, int] = {}
    target_counts: dict[str, int] = {}
    total_duration = 0.0
    duration_count = 0
    sessions = set()
    for event in events:
        event_type = str(event.get("event_type") or "")
        page = str(event.get("page") or "")
        target = str(event.get("target") or "")
        session_id = str(event.get("session_id") or "")
        if event_type:
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if page:
            page_counts[page] = page_counts.get(page, 0) + 1
        if target:
            target_counts[target] = target_counts.get(target, 0) + 1
        if session_id:
            sessions.add(session_id)
        duration = float(event.get("duration_seconds") or 0)
        if duration:
            total_duration += duration
            duration_count += 1
    return {
        "event_counts": event_counts,
        "top_pages": sorted(page_counts.items(), key=lambda item: item[1], reverse=True)[:10],
        "top_targets": sorted(target_counts.items(), key=lambda item: item[1], reverse=True)[:10],
        "session_count": len(sessions),
        "avg_duration_seconds": round(total_duration / duration_count, 2) if duration_count else 0,
    }


def health_candidates(module: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    health_url = module.get("health_url")
    local_url = module.get("local_url")
    if health_url:
        candidates.append(str(health_url))
    if local_url and module.get("status") != "extension-point":
        candidates.append(urljoin(str(local_url).rstrip("/") + "/", "api/health"))
    return list(dict.fromkeys(candidates))


def probe_health(url: str) -> dict[str, Any]:
    try:
        with NO_PROXY_OPENER.open(url, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            body = response.read(256).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= response.status < 300,
                "status_code": response.status,
                "url": url,
                "body": body,
            }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "error": exc.__class__.__name__,
            "detail": str(exc),
        }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"ok": "true"}


@app.get("/api/modules")
def modules() -> dict[str, Any]:
    return {"modules": load_modules()}


@app.get("/api/modules/health")
def module_health() -> dict[str, Any]:
    results = []
    for module in load_modules():
        candidates = health_candidates(module)
        if not candidates:
            results.append({
                "id": module.get("id"),
                "ok": None,
                "status": "unknown",
                "checked_url": "",
                "attempts": [],
            })
            continue

        attempts = [probe_health(url) for url in candidates]
        first_ok = next((attempt for attempt in attempts if attempt["ok"]), None)
        results.append({
            "id": module.get("id"),
            "ok": bool(first_ok),
            "status": "healthy" if first_ok else "unreachable",
            "checked_url": first_ok["url"] if first_ok else attempts[-1]["url"],
            "attempts": attempts,
        })
    return {"modules": results}


@app.post("/api/visitor/register")
async def visitor_register(payload: VisitorRegistration, request: Request) -> dict[str, Any]:
    if not payload.consent:
        raise HTTPException(status_code=400, detail="visitor consent is required")
    item = {
        **payload.model_dump(),
        **request_context(request),
    }
    append_jsonl(VISITOR_REGISTRY_FILE, item)
    return {"ok": True}


@app.post("/api/visitor/event")
async def visitor_event(payload: VisitorEvent, request: Request) -> dict[str, Any]:
    if not payload.consent:
        return {"ok": True, "stored": False}
    item = {
        **payload.model_dump(),
        **request_context(request),
    }
    append_jsonl(VISITOR_EVENTS_FILE, item)
    return {"ok": True, "stored": True}


@app.get("/api/visitor/summary")
def visitor_summary(x_visitor_admin_code: str | None = Header(default=None)) -> dict[str, Any]:
    require_visitor_admin(x_visitor_admin_code)
    registrations = read_jsonl(VISITOR_REGISTRY_FILE)
    events = read_jsonl(VISITOR_EVENTS_FILE, limit=1000)
    return {
        "registrations": list(reversed(registrations[-100:])),
        "events": list(reversed(events[-300:])),
        "summary": {
            "registration_count": len(registrations),
            **summarize_events(events),
        },
    }


@app.get("/visitor-insights-2026", response_class=HTMLResponse)
def visitor_insights() -> str:
    return (STATIC_DIR / "visitor_insights.html").read_text(encoding="utf-8")


@app.get("/ideas", response_class=HTMLResponse)
def ideas() -> str:
    return (STATIC_DIR / "ideas.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")
