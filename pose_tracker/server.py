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

_browser_clients: list[WebSocket] = []
_pi_ws: WebSocket | None = None
_streaming = False

_STATIC = Path(__file__).parent / "static"


# ── Pi control (called by browser buttons) ────────────────────────────────────

@app.post("/api/start")
async def start_stream() -> dict[str, Any]:
    global _streaming
    _streaming = True
    if _pi_ws:
        await _pi_ws.send_text(json.dumps({"cmd": "start"}))
    await _notify_status()
    return {"ok": True}


@app.post("/api/stop")
async def stop_stream() -> dict[str, Any]:
    global _streaming
    _streaming = False
    if _pi_ws:
        await _pi_ws.send_text(json.dumps({"cmd": "stop"}))
    await _notify_status()
    return {"ok": True}


@app.post("/api/reset")
async def reset_pose() -> dict[str, Any]:
    estimator.reset()
    await _broadcast_full()
    return {"ok": True}


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    return {"pi_connected": _pi_ws is not None, "streaming": _streaming}


# ── Pi WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws/pi")
async def pi_ws_endpoint(ws: WebSocket) -> None:
    global _pi_ws
    await ws.accept()
    _pi_ws = ws
    await _notify_status()
    print("[pose_tracker] Pi connected")
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            t = data.get("type")
            if t == "imu":
                o = data["orientation"]
                estimator.update_imu(float(o["x"]), float(o["y"]), float(o["z"]), float(o["w"]))
                await _broadcast_update()
            elif t == "cmd_vel":
                estimator.update_cmd_vel(
                    float(data["linear_x"]),
                    float(data["linear_y"]),
                    float(data["angular_z"]),
                )
                await _broadcast_update()
    except WebSocketDisconnect:
        pass
    finally:
        _pi_ws = None
        print("[pose_tracker] Pi disconnected")
        await _notify_status()


# ── Browser WebSocket ─────────────────────────────────────────────────────────

@app.websocket("/ws")
async def browser_ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _browser_clients.append(ws)
    await ws.send_text(json.dumps(_full_payload()))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _browser_clients:
            _browser_clients.remove(ws)


# ── payload helpers ───────────────────────────────────────────────────────────

def _status() -> dict[str, Any]:
    return {"pi_connected": _pi_ws is not None, "streaming": _streaming}


def _full_payload() -> dict[str, Any]:
    snap = estimator.snapshot()
    snap["full_trail"] = snap.pop("trail")
    snap["new_point"] = None
    snap.update(_status())
    return snap


def _update_payload() -> dict[str, Any]:
    snap = estimator.snapshot()
    trail = snap.pop("trail")
    snap["new_point"] = trail[-1] if trail else None
    snap.update(_status())
    return snap


async def _broadcast_update() -> None:
    if not _browser_clients:
        return
    msg = json.dumps(_update_payload())
    await _send_all(msg)


async def _broadcast_full() -> None:
    if not _browser_clients:
        return
    msg = json.dumps(_full_payload())
    await _send_all(msg)


async def _notify_status() -> None:
    if not _browser_clients:
        return
    msg = json.dumps({"status_update": True, **_status()})
    await _send_all(msg)


async def _send_all(msg: str) -> None:
    dead = []
    for c in _browser_clients:
        try:
            await c.send_text(msg)
        except Exception:
            dead.append(c)
    for c in dead:
        if c in _browser_clients:
            _browser_clients.remove(c)


# ── static ────────────────────────────────────────────────────────────────────

@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8300, reload=False)
