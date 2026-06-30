from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class SlamConfig:
    map_enabled: bool = False
    resolution_m: float = 0.05
    size_m: float = 6.0
    max_range_m: float = 2.5


@dataclass
class SlamState:
    x_m: float = 0.0
    y_m: float = 0.0
    yaw_rad: float = 0.0
    source: str = "idle"
    updated_at: float = field(default_factory=time.time)
    distance_travelled_m: float = 0.0


class SlamMapper:
    MAX_TRAIL = 2000

    """Small pluggable pose/map store.

    It intentionally works without scan data. Odometry can keep movement
    control informed, while range scans can be added later to build a map.
    """

    def __init__(self, config: SlamConfig | None = None) -> None:
        self.config = config or SlamConfig()
        self.state = SlamState()
        self._grid_size = self._compute_grid_size()
        self._grid: list[list[int]] = self._new_grid()
        self._trail: list[dict[str, float]] = []
        self._record_trail()

    def configure(self, patch: dict[str, Any]) -> dict[str, Any]:
        if "map_enabled" in patch:
            self.config.map_enabled = bool(patch["map_enabled"])
        if "resolution_m" in patch:
            self.config.resolution_m = _clamp(float(patch["resolution_m"]), 0.02, 0.25)
        if "size_m" in patch:
            self.config.size_m = _clamp(float(patch["size_m"]), 1.0, 20.0)
        if "max_range_m" in patch:
            self.config.max_range_m = _clamp(float(patch["max_range_m"]), 0.2, 8.0)
        self._grid_size = self._compute_grid_size()
        self._grid = self._new_grid()
        return self.config_snapshot()

    def reset(self) -> None:
        self.state = SlamState()
        self._grid = self._new_grid()
        self._trail = []
        self._record_trail()

    def update_odometry(self, dx_m: float, dy_m: float = 0.0, dyaw_rad: float = 0.0, source: str = "odometry") -> dict[str, Any]:
        cos_y = math.cos(self.state.yaw_rad)
        sin_y = math.sin(self.state.yaw_rad)
        world_dx = dx_m * cos_y - dy_m * sin_y
        world_dy = dx_m * sin_y + dy_m * cos_y
        self.state.x_m += world_dx
        self.state.y_m += world_dy
        self.state.yaw_rad = self._normalize_angle(self.state.yaw_rad + dyaw_rad)
        self.state.distance_travelled_m += math.hypot(dx_m, dy_m)
        self.state.source = source
        self.state.updated_at = time.time()
        self._record_trail()
        return self.snapshot(include_map=False)

    def update_scan(self, ranges_m: list[float], angle_min_rad: float, angle_increment_rad: float) -> dict[str, Any]:
        self.state.source = "scan+odometry" if self.state.source != "idle" else "scan"
        self.state.updated_at = time.time()
        if not self.config.map_enabled:
            return self.snapshot(include_map=False)

        for idx, raw_distance in enumerate(ranges_m):
            if not math.isfinite(raw_distance) or raw_distance <= 0:
                continue
            distance = min(float(raw_distance), self.config.max_range_m)
            angle = self.state.yaw_rad + angle_min_rad + idx * angle_increment_rad
            hit_x = self.state.x_m + math.cos(angle) * distance
            hit_y = self.state.y_m + math.sin(angle) * distance
            cell = self._world_to_cell(hit_x, hit_y)
            if cell is not None:
                row, col = cell
                self._grid[row][col] = 100
        return self.snapshot(include_map=True)

    def snapshot(self, include_map: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pose": {
                "x_m": round(self.state.x_m, 4),
                "y_m": round(self.state.y_m, 4),
                "yaw_deg": round(math.degrees(self.state.yaw_rad), 2),
                "distance_travelled_cm": round(self.state.distance_travelled_m * 100.0, 1),
                "source": self.state.source,
                "updated_at": self.state.updated_at,
            },
            "config": self.config_snapshot(),
            "map_available": self.config.map_enabled,
            "trail": self._trail[-500:],
        }
        if include_map:
            payload["map"] = self.map_snapshot()
        return payload

    def map_snapshot(self) -> dict[str, Any]:
        return {
            "resolution_m": self.config.resolution_m,
            "size": self._grid_size,
            "origin": {
                "x_m": round(-self.config.size_m / 2.0, 3),
                "y_m": round(-self.config.size_m / 2.0, 3),
            },
            "encoding": "occupancy_grid",
            "values": self._grid,
        }

    def config_snapshot(self) -> dict[str, Any]:
        return {
            "map_enabled": self.config.map_enabled,
            "resolution_m": self.config.resolution_m,
            "size_m": self.config.size_m,
            "max_range_m": self.config.max_range_m,
        }

    def _compute_grid_size(self) -> int:
        return max(4, int(round(self.config.size_m / self.config.resolution_m)))

    def _new_grid(self) -> list[list[int]]:
        return [[-1 for _ in range(self._grid_size)] for _ in range(self._grid_size)]

    def _world_to_cell(self, x_m: float, y_m: float) -> tuple[int, int] | None:
        half = self.config.size_m / 2.0
        if x_m < -half or x_m >= half or y_m < -half or y_m >= half:
            return None
        col = int((x_m + half) / self.config.resolution_m)
        row = self._grid_size - 1 - int((y_m + half) / self.config.resolution_m)
        if row < 0 or row >= self._grid_size or col < 0 or col >= self._grid_size:
            return None
        return row, col

    def _record_trail(self) -> None:
        self._trail.append(
            {
                "x_m": round(self.state.x_m, 4),
                "y_m": round(self.state.y_m, 4),
                "yaw_deg": round(math.degrees(self.state.yaw_rad), 2),
                "t": self.state.updated_at,
            }
        )
        if len(self._trail) > self.MAX_TRAIL:
            self._trail = self._trail[-self.MAX_TRAIL:]

    @staticmethod
    def _normalize_angle(angle: float) -> float:
        while angle > math.pi:
            angle -= 2.0 * math.pi
        while angle < -math.pi:
            angle += 2.0 * math.pi
        return angle
