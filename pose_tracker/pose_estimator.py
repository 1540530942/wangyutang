from __future__ import annotations

import math
import time
from dataclasses import dataclass


@dataclass
class PosePoint:
    x: float       # metres, forward from start
    y: float       # metres, left from start
    yaw: float     # radians, counter-clockwise from start heading
    t: float       # unix timestamp


class PoseEstimator:
    MAX_TRAIL = 2000

    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self._imu_active = False
        self._last_vel_t: float | None = None
        self._last_vel: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.trail: list[PosePoint] = [PosePoint(0.0, 0.0, 0.0, time.time())]

    # ── IMU update (yaw from quaternion) ─────────────────────────────────────

    def update_imu(self, qx: float, qy: float, qz: float, qw: float) -> None:
        siny = 2.0 * (qw * qz + qx * qy)
        cosy = 1.0 - 2.0 * (qy * qy + qz * qz)
        self.yaw = math.atan2(siny, cosy)
        self._imu_active = True
        self._record()

    # ── cmd_vel update (dead-reckoning for x/y) ──────────────────────────────

    def update_cmd_vel(self, linear_x: float, linear_y: float, angular_z: float) -> None:
        now = time.time()
        if self._last_vel_t is not None:
            dt = min(now - self._last_vel_t, 0.5)
            vx, vy, vw = self._last_vel
            cos_y = math.cos(self.yaw)
            sin_y = math.sin(self.yaw)
            self.x += (vx * cos_y - vy * sin_y) * dt
            self.y += (vx * sin_y + vy * cos_y) * dt
            if not self._imu_active:
                self.yaw += vw * dt
        self._last_vel = (linear_x, linear_y, angular_z)
        self._last_vel_t = now
        self._record()

    def reset(self) -> None:
        self.x = self.y = self.yaw = 0.0
        self._last_vel_t = None
        self._last_vel = (0.0, 0.0, 0.0)
        self.trail = [PosePoint(0.0, 0.0, 0.0, time.time())]

    # ── output ───────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        trail_out = [
            {"x": round(p.x, 3), "y": round(p.y, 3), "yaw": round(p.yaw, 3)}
            for p in self.trail[-300:]
        ]
        return {
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "yaw_deg": round(math.degrees(self.yaw), 1),
            "dist_cm": round(math.hypot(self.x, self.y) * 100, 1),
            "imu_active": self._imu_active,
            "trail": trail_out,
            "description": self._describe(),
        }

    def _describe(self) -> str:
        dist_cm = math.hypot(self.x, self.y) * 100
        yaw_deg = math.degrees(self.yaw) % 360

        if dist_cm < 3:
            pos_desc = "原地静止"
        elif dist_cm < 20:
            pos_desc = f"距起点 {dist_cm:.0f} cm"
        else:
            pos_desc = f"距起点 {dist_cm/100:.2f} m"

        if yaw_deg < 22.5 or yaw_deg >= 337.5:
            dir_desc = "朝向正前方"
        elif yaw_deg < 67.5:
            dir_desc = "偏左前方"
        elif yaw_deg < 112.5:
            dir_desc = "朝向正左"
        elif yaw_deg < 157.5:
            dir_desc = "偏左后方"
        elif yaw_deg < 202.5:
            dir_desc = "朝向正后方"
        elif yaw_deg < 247.5:
            dir_desc = "偏右后方"
        elif yaw_deg < 292.5:
            dir_desc = "朝向正右"
        else:
            dir_desc = "偏右前方"

        src = "IMU融合" if self._imu_active else "指令推算"
        return f"{pos_desc}，{dir_desc}（{yaw_deg:.0f}°），数据源：{src}"

    def _record(self) -> None:
        self.trail.append(PosePoint(self.x, self.y, self.yaw, time.time()))
        if len(self.trail) > self.MAX_TRAIL:
            self.trail = self.trail[-self.MAX_TRAIL:]
