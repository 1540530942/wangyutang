from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from slam_core import SlamMapper


app = FastAPI(title="SLAM Mapping", version="0.1.0")
mapper = SlamMapper()


class ConfigPatch(BaseModel):
    map_enabled: bool | None = None
    resolution_m: float | None = Field(default=None, ge=0.02, le=0.25)
    size_m: float | None = Field(default=None, ge=1.0, le=20.0)
    max_range_m: float | None = Field(default=None, ge=0.2, le=8.0)


class OdometryUpdate(BaseModel):
    dx_m: float = Field(..., ge=-2.0, le=2.0)
    dy_m: float = Field(0.0, ge=-2.0, le=2.0)
    dyaw_rad: float = Field(0.0, ge=-6.28319, le=6.28319)
    source: str = Field("odometry", max_length=40)


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


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "SLAM Mapping",
        "description": "Optional pose and occupancy-grid module for robot motion feedback.",
        "health": "/api/health",
        "state": "/api/state",
    }
