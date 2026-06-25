from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pose_estimator import PoseEstimator

app = FastAPI(title="pose_tracker")
estimator = PoseEstimator()
_clients: list[WebSocket] = []

_STATIC = Path(__file__).parent / "static"


# ── data ingestion ────────────────────────────────────────────────────────────

@app.post("/api/imu")
async def receive_imu(data: dict[str, Any]) -> dict[str, Any]:
    o = data.get("orientation", {})
    estimator.update_imu(float(o["x"]), float(o["y"]), float(o["z"]), float(o["w"]))
    await _broadcast()
    return {"ok": True}


@app.post("/api/cmd_vel")
async def receive_cmd_vel(data: dict[str, Any]) -> dict[str, Any]:
    estimator.update_cmd_vel(
        float(data.get("linear_x", 0)),
        float(data.get("linear_y", 0)),
        float(data.get("angular_z", 0)),
    )
    await _broadcast()
    return {"ok": True}


@app.post("/api/reset")
async def reset_pose() -> dict[str, Any]:
    estimator.reset()
    await _broadcast()
    return {"ok": True}


# ── query ─────────────────────────────────────────────────────────────────────

@app.get("/api/pose")
async def get_pose() -> dict[str, Any]:
    return estimator.snapshot()


# ── WebSocket (browser) ───────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.append(ws)
    await ws.send_text(json.dumps(estimator.snapshot()))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _clients:
            _clients.remove(ws)


async def _broadcast() -> None:
    if not _clients:
        return
    msg = json.dumps(estimator.snapshot())
    dead = []
    for c in _clients:
        try:
            await c.send_text(msg)
        except Exception:
            dead.append(c)
    for c in dead:
        if c in _clients:
            _clients.remove(c)


# ── static ────────────────────────────────────────────────────────────────────

@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8300, reload=False)
