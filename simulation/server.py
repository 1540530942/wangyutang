"""TurboPi 全功能仿真服务器。

兼容接口（替代真实树莓派硬件）：
  POST /execute               ← local_action_server (edge_ros_controller)
  GET  /health                ← action server health
  POST /api/capture           ← camera_snapshot server
  GET  /api/latest            ← latest frame metadata
  GET  /api/latest.jpg        ← latest frame JPEG
  GET  /api/control           ← camera task status
  GET  /api/sonar             ← front_distance sensor
  POST /vision/analyze        ← inspect_scene LLM vision
  GET  /api/state             ← UI state (JSON)
  WS   /ws                    ← 实时状态推送
  GET  /                      ← Web UI

启动：
  python -m simulation.server           # 默认 8766
  SIM_PORT=9000 python -m simulation.server
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import os
import secrets
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from simulation.state import RobotState
from simulation.renderer import compute_obstacle_projections, render_camera_view

# ── 全局状态 ──────────────────────────────────────────────────────────────────

robot = RobotState()
_ws_clients: list[WebSocket] = []
_latest_frame_meta: dict[str, Any] = {"updated_at": 0.0, "task_id": "", "available": False}
_camera_task: dict[str, Any] = {}

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="TurboPi Simulator", version="1.0.0")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── 广播状态到所有 WebSocket 客户端 ────────────────────────────────────────────

async def _broadcast(data: dict) -> None:
    msg = json.dumps(data, ensure_ascii=False)
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


# ── Action Server —— POST /execute ────────────────────────────────────────────

@app.post("/execute")
async def execute(payload: dict) -> JSONResponse:
    skill_id = str(payload.get("action") or "")
    settings = payload.get("settings") or {}
    if not skill_id:
        return JSONResponse({"ok": False, "error": "missing action"}, status_code=400)

    msg = robot.apply(skill_id, settings)

    # 更新摄像头帧（每次动作都生成新帧）
    _refresh_frame()

    await _broadcast(robot.to_dict())
    return JSONResponse({"ok": True, "output": msg, "skill_id": skill_id})


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "TurboPi Simulator",
        "device": {
            "online": True,
            "device_id": "sim-turbopi-01",
            "hostname": "turbopi-sim",
            "ip_address": "127.0.0.1",
        },
        "settings": {
            "unit_distance_cm": 5.0,
            "turn_angle_deg": 5.0,
            "sensitivity": 1.0,
            "rgb_red": robot.rgb_r,
            "rgb_green": robot.rgb_g,
            "rgb_blue": robot.rgb_b,
        },
    }


# ── Camera Server —— 兼容 camera_snapshot.server ─────────────────────────────

@app.post("/api/capture")
async def capture(payload: dict = {}) -> dict:
    global _camera_task, _latest_frame_meta
    now = time.time()
    task_id = f"sim-{int(now * 1000)}-{secrets.token_hex(3)}"
    _camera_task = {
        "id": task_id,
        "kind": "camera",
        "mode": str(payload.get("mode") or "single"),
        "status": "completed",
        "requested_at": now,
        "deadline_at": now + 30,
    }
    _refresh_frame(task_id=task_id)
    await _broadcast(robot.to_dict())
    return {"ok": True, "task": _camera_task}


@app.get("/api/latest")
def latest(kind: str = "camera") -> dict:
    return dict(_latest_frame_meta)


@app.get("/api/latest.jpg")
def latest_jpg() -> Response:
    projs = compute_obstacle_projections(
        robot.x, robot.y, robot.heading_deg, robot.pan_deg, robot.obstacles
    )
    jpg = render_camera_view(
        front_distance_cm=robot.front_distance_cm(),
        pan_deg=robot.pan_deg,
        tilt_deg=robot.tilt_deg,
        rgb_on=robot.rgb_on,
        rgb_r=robot.rgb_r,
        rgb_g=robot.rgb_g,
        rgb_b=robot.rgb_b,
        obstacles_screen=projs,
    )
    return Response(content=jpg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/control")
def control(kind: str = "camera") -> dict:
    return {"task": _camera_task}


# ── Sensor Server —— GET /api/sonar ──────────────────────────────────────────

@app.get("/api/sonar")
def sonar() -> dict:
    dist = robot.front_distance_cm()
    now = time.time()
    return {
        "available": True,
        "front_distance_estimate_cm": round(dist, 1),
        "confidence": 0.95,
        "source": "simulator",
        "sampled_at": now,
        "reported_at": now,
        "age_seconds": 0.0,
        "device_id": "sim-turbopi-01",
    }


# ── Vision Analyze —— POST /vision/analyze ───────────────────────────────────

@app.post("/vision/analyze")
def vision_analyze(payload: dict) -> dict:
    dist = robot.front_distance_cm()
    obs = dist < 60
    obs_str = "检测到障碍物" if obs else "畅通无阻"
    answers = [
        f"前方约 {dist:.0f} cm，{obs_str}，地面平坦无杂物，光线良好。",
        f"视野内可见室内场景，前方 {dist:.0f} cm {obs_str}，适合继续行进。",
        f"环境分析：前方 {dist:.0f} cm，{obs_str}，两侧墙壁清晰。",
    ]
    answer = answers[robot.frame_id % len(answers)]
    return {"text": answer, "model": "sim-vision-v1", "confidence": 0.92}


# ── 全局状态 API ──────────────────────────────────────────────────────────────

@app.get("/api/state")
def api_state() -> dict:
    return robot.to_dict()


@app.post("/api/reset")
async def api_reset() -> dict:
    global robot
    robot = RobotState()
    _refresh_frame()
    await _broadcast(robot.to_dict())
    return {"ok": True}


@app.post("/api/obstacle")
async def add_obstacle(payload: dict) -> dict:
    robot.obstacles.append({
        "cx": float(payload.get("cx") or 300),
        "cy": float(payload.get("cy") or 300),
        "r": float(payload.get("r") or 25),
    })
    _refresh_frame()
    await _broadcast(robot.to_dict())
    return {"ok": True, "obstacles": robot.obstacles}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _ws_clients.append(websocket)
    await websocket.send_text(json.dumps(robot.to_dict(), ensure_ascii=False))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


# ── Web UI ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    ui_path = STATIC_DIR / "index.html"
    if ui_path.exists():
        return ui_path.read_text(encoding="utf-8")
    return "<h1>Simulator UI not found</h1>"


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _refresh_frame(task_id: str = "") -> None:
    global _latest_frame_meta
    now = time.time()
    _latest_frame_meta = {
        "available": True,
        "task_id": task_id or _camera_task.get("id") or "init",
        "updated_at": now + 0.001,   # > requested_at, 让 _wait_for_latest_frame 立即返回
        "kind": "camera",
        "width": 640,
        "height": 480,
        "mime": "image/jpeg",
        "size_bytes": 0,
        "captured_at": now,
        "frame_id": robot.frame_id,
    }


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("SIM_PORT", "8766"))
    uvicorn.run("simulation.server:app", host="0.0.0.0", port=port, reload=False)
