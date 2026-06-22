"""
pi5-monitor 服务端 FastAPI 应用。
端口: 8098
接口:
  POST /api/monitor/heartbeat           — Pi 心跳
  POST /api/monitor/events              — Pi 批量上传事件
  POST /api/monitor/shutdown_snapshot   — Pi 关机前快照（紧急）
  GET  /api/monitor/devices             — 所有设备状态
  GET  /api/monitor/incidents           — 失联事件列表
  GET  /api/monitor/incidents/{id}/report  — 失联事件详细报告
  GET  /api/monitor/events/{device_id}  — 设备事件查询
  GET  /api/monitor/health              — 健康检查
"""
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from alerter import send_offline_alert, send_online_alert, send_shutdown_snapshot_alert
from analyzer.analyzer import analyze_incident, analyze_shutdown_snapshot
from database import DB
from watchdog import Watchdog

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("monitor-server")

API_KEY = os.environ.get("PI5_MONITOR_API_KEY", "changeme")

db = DB()
watchdog: Watchdog | None = None


def _on_offline(device_id: str, incident_id: int):
    last_ts = db.last_heartbeat_time(device_id)
    send_offline_alert(device_id, incident_id, last_ts)


def _on_online(device_id: str, incident_id: int, cause: str):
    send_online_alert(device_id, incident_id, cause)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global watchdog
    watchdog = Watchdog(db, _on_offline, _on_online)
    watchdog.start()
    logger.info("Watchdog started")
    yield
    watchdog.stop()
    logger.info("Watchdog stopped")


app = FastAPI(title="Pi5 Monitor Server", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_auth(x_api_key: str | None, x_device_id: str | None):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not x_device_id:
        raise HTTPException(status_code=400, detail="Missing X-Device-ID header")


# ── Models ────────────────────────────────────────────────────────────

class HeartbeatPayload(BaseModel):
    device_id: str
    timestamp: str
    uptime_s: float | None = None
    memory_mb: dict | None = None
    cpu_temp_c: float | None = None
    active_users: list[str] = []
    net_interfaces: dict[str, str] = {}


class EventsBatch(BaseModel):
    device_id: str
    events: list[dict[str, Any]]


class ShutdownSnapshot(BaseModel):
    device_id: str
    type: str = "shutdown_snapshot"
    captured_at: str | None = None
    model_config = {"extra": "allow"}


# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/api/monitor/health")
def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/api/monitor/heartbeat")
def receive_heartbeat(
    payload: HeartbeatPayload,
    x_api_key: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None),
):
    _check_auth(x_api_key, x_device_id)
    now = time.time()
    db.save_heartbeat(payload.device_id, payload.model_dump())
    if watchdog:
        watchdog.register_device(payload.device_id)
        watchdog.update_heartbeat(payload.device_id, now)
    return {"ok": True, "received_at": now}


@app.post("/api/monitor/events")
def receive_events(
    batch: EventsBatch,
    x_api_key: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None),
):
    _check_auth(x_api_key, x_device_id)
    db.save_events(batch.device_id, batch.events)
    logger.info("Received %d events from %s", len(batch.events), batch.device_id)
    return {"ok": True, "saved": len(batch.events)}


@app.post("/api/monitor/shutdown_snapshot")
def receive_shutdown_snapshot(
    snapshot: ShutdownSnapshot,
    x_api_key: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None),
):
    _check_auth(x_api_key, x_device_id)
    data = snapshot.model_dump()
    snapshot_id = db.save_shutdown_snapshot(snapshot.device_id, data)

    # 立即分析并告警
    summary = analyze_shutdown_snapshot(data)
    send_shutdown_snapshot_alert(snapshot.device_id, summary)

    db.mark_snapshot_analyzed(snapshot_id)
    logger.warning("Shutdown snapshot received from %s: %s", snapshot.device_id, summary)
    return {"ok": True, "snapshot_id": snapshot_id, "summary": summary}


@app.get("/api/monitor/devices")
def list_devices(x_api_key: str | None = Header(default=None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401)
    states = []
    if watchdog:
        for device_id, state in watchdog._states.items():
            last_ts = state.last_heartbeat_ts
            age = time.time() - last_ts if last_ts else None
            states.append(
                {
                    "device_id": device_id,
                    "online": state.online,
                    "last_heartbeat": (
                        datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()
                        if last_ts else None
                    ),
                    "last_heartbeat_age_s": age,
                    "current_incident_id": state.incident_id,
                }
            )
    return {"devices": states}


@app.get("/api/monitor/incidents")
def list_incidents(
    device_id: str = Query(...),
    x_api_key: str | None = Header(default=None),
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401)
    incidents = db.get_incidents(device_id)
    return {"incidents": incidents}


@app.get("/api/monitor/incidents/{incident_id}/report")
def get_incident_report(
    incident_id: int,
    x_api_key: str | None = Header(default=None),
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401)
    report = analyze_incident(db, incident_id)
    return report


@app.get("/api/monitor/events/{device_id}")
def get_events(
    device_id: str,
    since: float | None = Query(default=None, description="Unix timestamp"),
    until: float | None = Query(default=None),
    types: str | None = Query(default=None, description="逗号分隔的事件类型"),
    limit: int = Query(default=100, le=1000),
    x_api_key: str | None = Header(default=None),
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401)
    event_types = types.split(",") if types else None
    events = db.get_events(device_id, since, until, event_types, limit)
    return {"device_id": device_id, "count": len(events), "events": events}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8098, reload=False)
