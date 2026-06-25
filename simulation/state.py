"""TurboPi 仿真机器人状态机。

坐标系：arena 800×600，Y轴向下（canvas 坐标系）。
heading: 0=正右，顺时针正向，单位 degrees。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


# arena 尺寸（逻辑像素）
ARENA_W = 800
ARENA_H = 600
ROBOT_RADIUS = 20

# 默认障碍物（圆形 cx,cy,r）
_DEFAULT_OBSTACLES: list[dict] = [
    {"cx": 200, "cy": 150, "r": 30},
    {"cx": 600, "cy": 150, "r": 40},
    {"cx": 150, "cy": 450, "r": 25},
    {"cx": 650, "cy": 430, "r": 35},
    {"cx": 400, "cy": 200, "r": 20},
]


@dataclass
class RobotState:
    x: float = 400.0
    y: float = 400.0
    heading_deg: float = 270.0      # 初始朝上
    pan_deg: float = 0.0            # 摄像头水平角，-45~+45
    tilt_deg: float = 0.0           # 摄像头垂直角，-30~+30
    rgb_r: int = 0
    rgb_g: int = 0
    rgb_b: int = 255
    rgb_on: bool = False
    trail: list[tuple[float, float]] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)
    obstacles: list[dict] = field(default_factory=lambda: list(_DEFAULT_OBSTACLES))
    last_action: str = ""
    last_action_at: float = 0.0
    frame_id: int = 0
    updated_at: float = field(default_factory=time.time)

    # ── 动作执行 ──────────────────────────────────────────────────────────

    def apply(self, skill_id: str, settings: dict[str, Any]) -> str:
        """执行技能，更新状态，返回描述文字。"""
        self.last_action = skill_id
        self.last_action_at = time.time()
        self.updated_at = time.time()

        unit_cm = float(settings.get("unit_distance_cm") or 5.0)
        turn_deg = float(settings.get("turn_angle_deg") or 5.0)
        sensitivity = float(settings.get("sensitivity") or 1.0)
        step_px = unit_cm * 4.0 * sensitivity  # 1 cm ≈ 4 px

        msg = skill_id
        if skill_id == "emergency_stop":
            msg = "急停 ✓"
        elif skill_id == "reset_pose":
            self.pan_deg = 0.0
            self.tilt_deg = 0.0
            msg = "Reset — 云台归中 ✓"
        elif skill_id == "move_forward":
            self._move(step_px)
            msg = f"前进 {unit_cm:.1f} cm ✓"
        elif skill_id == "move_backward":
            self._move(-step_px)
            msg = f"后退 {unit_cm:.1f} cm ✓"
        elif skill_id == "move_left":
            self._strafe(-step_px)
            msg = f"左移 {unit_cm:.1f} cm ✓"
        elif skill_id == "move_right":
            self._strafe(step_px)
            msg = f"右移 {unit_cm:.1f} cm ✓"
        elif skill_id == "turn_left":
            self.heading_deg = (self.heading_deg - turn_deg) % 360
            msg = f"左转 {turn_deg:.0f}° → heading={self.heading_deg:.0f}°"
        elif skill_id == "turn_right":
            self.heading_deg = (self.heading_deg + turn_deg) % 360
            msg = f"右转 {turn_deg:.0f}° → heading={self.heading_deg:.0f}°"
        elif skill_id == "look_left":
            self.pan_deg = max(-45.0, self.pan_deg - 10.0)
            msg = f"摄像头左移 → pan={self.pan_deg:.0f}°"
        elif skill_id == "look_right":
            self.pan_deg = min(45.0, self.pan_deg + 10.0)
            msg = f"摄像头右移 → pan={self.pan_deg:.0f}°"
        elif skill_id == "look_up":
            self.tilt_deg = max(-30.0, self.tilt_deg - 10.0)
            msg = f"摄像头上仰 → tilt={self.tilt_deg:.0f}°"
        elif skill_id == "look_down":
            self.tilt_deg = min(30.0, self.tilt_deg + 10.0)
            msg = f"摄像头下俯 → tilt={self.tilt_deg:.0f}°"
        elif skill_id == "rgb_on":
            r = int(settings.get("rgb_red") or 0)
            g = int(settings.get("rgb_green") or 0)
            b = int(settings.get("rgb_blue") or 0)
            if r == 0 and g == 0 and b == 0:
                r, g, b = self.rgb_r, self.rgb_g, self.rgb_b
                if r == 0 and g == 0 and b == 0:
                    b = 255
            self.rgb_r, self.rgb_g, self.rgb_b = r, g, b
            self.rgb_on = True
            msg = f"RGB 开灯 rgb({r},{g},{b}) ✓"
        elif skill_id == "rgb_off":
            self.rgb_on = False
            msg = "RGB 关灯 ✓"
        elif skill_id == "front_distance":
            d = self.front_distance_cm()
            msg = f"测距 → {d:.1f} cm"
        else:
            msg = f"{skill_id} (unknown)"

        self.trail.append((self.x, self.y))
        if len(self.trail) > 200:
            self.trail = self.trail[-200:]

        self.log.append({
            "t": time.strftime("%H:%M:%S"),
            "skill": skill_id,
            "msg": msg,
        })
        if len(self.log) > 100:
            self.log = self.log[-100:]

        self.frame_id += 1
        return msg

    # ── 物理 ──────────────────────────────────────────────────────────────

    def _move(self, delta_px: float) -> None:
        rad = math.radians(self.heading_deg)
        nx = self.x + math.cos(rad) * delta_px
        ny = self.y + math.sin(rad) * delta_px
        nx = max(ROBOT_RADIUS, min(ARENA_W - ROBOT_RADIUS, nx))
        ny = max(ROBOT_RADIUS, min(ARENA_H - ROBOT_RADIUS, ny))
        if not self._collides(nx, ny):
            self.x, self.y = nx, ny

    def _strafe(self, delta_px: float) -> None:
        rad = math.radians(self.heading_deg + 90)
        nx = self.x + math.cos(rad) * delta_px
        ny = self.y + math.sin(rad) * delta_px
        nx = max(ROBOT_RADIUS, min(ARENA_W - ROBOT_RADIUS, nx))
        ny = max(ROBOT_RADIUS, min(ARENA_H - ROBOT_RADIUS, ny))
        if not self._collides(nx, ny):
            self.x, self.y = nx, ny

    def _collides(self, nx: float, ny: float) -> bool:
        for obs in self.obstacles:
            dx = nx - obs["cx"]
            dy = ny - obs["cy"]
            if math.sqrt(dx * dx + dy * dy) < ROBOT_RADIUS + obs["r"] + 5:
                return True
        return False

    def front_distance_cm(self) -> float:
        """射线检测前方最近障碍/墙距离（cm）。"""
        rad = math.radians(self.heading_deg)
        dx, dy = math.cos(rad), math.sin(rad)
        max_dist = 300.0
        step = 2.0
        t = ROBOT_RADIUS + 2.0
        while t < max_dist * 4:
            px = self.x + dx * t
            py = self.y + dy * t
            # 撞墙
            if px <= 0 or px >= ARENA_W or py <= 0 or py >= ARENA_H:
                return t / 4.0
            # 撞障碍
            for obs in self.obstacles:
                odx = px - obs["cx"]
                ody = py - obs["cy"]
                if math.sqrt(odx * odx + ody * ody) < obs["r"]:
                    return t / 4.0
            t += step
        return max_dist

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "heading_deg": round(self.heading_deg, 1),
            "pan_deg": round(self.pan_deg, 1),
            "tilt_deg": round(self.tilt_deg, 1),
            "rgb": {"r": self.rgb_r, "g": self.rgb_g, "b": self.rgb_b, "on": self.rgb_on},
            "front_distance_cm": round(self.front_distance_cm(), 1),
            "trail": [[round(x, 1), round(y, 1)] for x, y in self.trail[-50:]],
            "log": self.log[-20:],
            "obstacles": self.obstacles,
            "last_action": self.last_action,
            "frame_id": self.frame_id,
            "updated_at": self.updated_at,
            "arena": {"w": ARENA_W, "h": ARENA_H},
        }
