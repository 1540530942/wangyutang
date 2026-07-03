from __future__ import annotations

import os
import sys
from typing import Any

from pathlib import Path
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from slam_core import SlamMapper

sys.path.insert(0, str(Path(__file__).resolve().parent / "2d_action"))
from closed_loop import ClosedLoopController  # noqa: E402
from calibration import MotionCalibration  # noqa: E402
from feedback import ActionMoveCommander, SlamPoseFeedback  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CALIBRATION_PATH = Path(os.getenv("CALIBRATION_STORE", "/app/data/calibration.json"))

ACTION_BASE = os.getenv("ACTION_MOVE_URL", "https://www.wangyutang.cn/action")
SLAM_BASE = os.getenv("SLAM_SELF_URL", "https://www.wangyutang.cn/slam")

app = FastAPI(title="SLAM Mapping", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
mapper = SlamMapper()
calibration = MotionCalibration(store_path=CALIBRATION_PATH)


class ConfigPatch(BaseModel):
    map_enabled: bool | None = None
    resolution_m: float | None = Field(default=None, ge=0.02, le=0.25)
    size_m: float | None = Field(default=None, ge=1.0, le=20.0)
    max_range_m: float | None = Field(default=None, ge=0.2, le=8.0)


class OdometryUpdate(BaseModel):
    dx_m: float = Field(..., ge=-2.0, le=2.0)
    dy_m: float = Field(0.0, ge=-2.0, le=2.0)
    dyaw_rad: float = Field(0.0, ge=-6.28319, le=6.28319)
    source: str = Field("odometry", max_length=160)


class ScanUpdate(BaseModel):
    ranges_m: list[float] = Field(..., min_length=1, max_length=720)
    angle_min_rad: float = Field(..., ge=-6.28319, le=6.28319)
    angle_increment_rad: float = Field(..., ge=0.0001, le=1.0)


@app.get("/api/health")
def health() -> dict[str, Any]:
    snap = mapper.snapshot(include_map=False)
    return {
        "status": "ok",
        "service": "SLAM Mapping",
        "map_available": snap["map_available"],
        "pose": snap["pose"],
        "config": snap["config"],
    }


@app.get("/api/state")
def state(include_map: bool = False) -> dict[str, Any]:
    return mapper.snapshot(include_map=include_map)


@app.post("/api/config")
def configure(patch: ConfigPatch) -> dict[str, Any]:
    data = patch.model_dump(exclude_none=True)
    return {"ok": True, "config": mapper.configure(data)}


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    mapper.reset()
    return {"ok": True, **mapper.snapshot(include_map=False)}


@app.post("/api/odom")
def odometry(update: OdometryUpdate) -> dict[str, Any]:
    return {"ok": True, **mapper.update_odometry(update.dx_m, update.dy_m, update.dyaw_rad, update.source)}


@app.post("/api/scan")
def scan(update: ScanUpdate) -> dict[str, Any]:
    return {"ok": True, **mapper.update_scan(update.ranges_m, update.angle_min_rad, update.angle_increment_rad)}


class MoveRequest(BaseModel):
    action: str = Field(..., max_length=40)
    distance_cm: float | None = Field(default=None, ge=1.0, le=100.0)
    angle_deg: float | None = Field(default=None, ge=1.0, le=180.0)
    closed_loop: bool = Field(default=True)
    source: str = Field(default="slam-web", max_length=40)


@app.post("/api/move")
def move(req: MoveRequest) -> dict[str, Any]:
    """Issue a motion command, optionally via closed-loop correction.

    When closed_loop=True (default) the controller issues the motion through
    ActionMoveCommander then measures actual displacement from the SLAM pose
    delta, issuing corrective bursts until the error is within tolerance.
    When closed_loop=False it issues a single open-loop command.
    """
    import math as _math

    _TRANSLATE = {"move_forward", "move_backward", "move_left", "move_right"}
    _ROTATE = {"turn_left", "turn_right"}

    if req.action not in (_TRANSLATE | _ROTATE):
        return {"ok": False, "error": f"unknown action: {req.action}"}

    commander = ActionMoveCommander(ACTION_BASE, source=req.source)
    feedback = SlamPoseFeedback(commander, slam_base=SLAM_BASE, settle_timeout=12.0)

    if req.action in _TRANSLATE:
        kind = "translate"
        magnitude = (req.distance_cm or 10.0) / 100.0
        sign = -1.0 if req.action in {"move_backward", "move_right"} else 1.0
    else:
        kind = "rotate"
        magnitude = _math.radians(req.angle_deg or 45.0)
        sign = -1.0 if req.action == "turn_right" else 1.0

    target = magnitude * sign

    if not req.closed_loop:
        try:
            result = commander.command(target, kind)
            return {"ok": True, "closed_loop": False, "action": req.action, "target": target, "result": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    ctrl = ClosedLoopController(
        command_fn=feedback.command,
        measure_fn=feedback.measure,
        calibration=calibration,
        max_iterations=3,
    )
    try:
        move_result = ctrl.move(target, kind)
        return {
            "ok": True,
            "closed_loop": True,
            "action": req.action,
            **move_result.as_dict(),
            "calibration": calibration.snapshot(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@app.get("/api/calibration")
def get_calibration() -> dict[str, Any]:
    return {"ok": True, **calibration.snapshot(), "recent": calibration.recent(10)}


@app.post("/api/calibration/reset")
def reset_calibration() -> dict[str, Any]:
    calibration._kinds["translate"].__init__()  # type: ignore[misc]
    calibration._kinds["rotate"].__init__()  # type: ignore[misc]
    calibration._history.clear()
    calibration._save()
    return {"ok": True, "calibration": calibration.snapshot()}


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
