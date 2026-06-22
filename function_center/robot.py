"""
Direct robot control for TurboPi on Raspberry Pi.

Usage (on the Pi):
    from function_center import move_forward, turn_left, rgb_on, front_distance
    move_forward(distance_cm=10)
    turn_left(angle_deg=45)
    rgb_on(r=255, g=0, b=0)
    print(front_distance())

Execution path:
  1. POST to edge_ros_controller at 127.0.0.1:8765 (persistent rclpy node, fastest)
  2. Fallback: subprocess → action_move_executor.py → docker exec ros2 topic pub
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_CONTROLLER_URL = "http://127.0.0.1:8765"
_EXECUTOR = Path(__file__).resolve().parent.parent / "action_move" / "action_move_executor.py"
_ACTION_TIMEOUT = 20


def _call(action: str, **settings: Any) -> dict[str, Any]:
    payload = json.dumps({"action": action, "settings": settings}).encode()
    try:
        req = urllib.request.Request(
            f"{_CONTROLLER_URL}/execute",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_ACTION_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError):
        pass

    # fallback to subprocess executor
    args = ["python3", str(_EXECUTOR), action]
    if settings:
        args += ["--params-json", json.dumps(settings, ensure_ascii=False)]
    result = subprocess.run(args, text=True, capture_output=True, timeout=_ACTION_TIMEOUT)
    return {
        "ok": result.returncode == 0,
        "output": result.stdout,
        "error": result.stderr,
    }


# ── 底盘移动 ──────────────────────────────────────────────────────────────────

def move_forward(distance_cm: float = 5.0, sensitivity: float = 1.0) -> dict[str, Any]:
    """前进一个单位（麦克纳姆轮，linear_x=+0.35）。"""
    return _call("move_forward", unit_distance_cm=distance_cm, sensitivity=sensitivity)


def move_backward(distance_cm: float = 5.0, sensitivity: float = 1.0) -> dict[str, Any]:
    """后退一个单位（linear_x=-0.35）。"""
    return _call("move_backward", unit_distance_cm=distance_cm, sensitivity=sensitivity)


def move_left(distance_cm: float = 5.0, sensitivity: float = 1.0) -> dict[str, Any]:
    """向左平移一个单位（linear_y=+0.35）。"""
    return _call("move_left", unit_distance_cm=distance_cm, sensitivity=sensitivity)


def move_right(distance_cm: float = 5.0, sensitivity: float = 1.0) -> dict[str, Any]:
    """向右平移一个单位（linear_y=-0.45）。"""
    return _call("move_right", unit_distance_cm=distance_cm, sensitivity=sensitivity)


def turn_left(angle_deg: float = 5.0, sensitivity: float = 1.0) -> dict[str, Any]:
    """原地左转（angular_z=+5.0）。"""
    return _call("turn_left", turn_angle_deg=angle_deg, sensitivity=sensitivity)


def turn_right(angle_deg: float = 5.0, sensitivity: float = 1.0) -> dict[str, Any]:
    """原地右转（angular_z=-5.0）。"""
    return _call("turn_right", turn_angle_deg=angle_deg, sensitivity=sensitivity)


def stop() -> dict[str, Any]:
    """急停：立即向 /cmd_vel 发布零速度。"""
    return _call("emergency_stop")


# ── 摄像头云台 ────────────────────────────────────────────────────────────────

def look_left() -> dict[str, Any]:
    """云台水平舵机（servo 2）偏转至 1800，向左看。"""
    return _call("look_left")


def look_right() -> dict[str, Any]:
    """云台水平舵机（servo 2）偏转至 1200，向右看。"""
    return _call("look_right")


def look_up() -> dict[str, Any]:
    """云台俯仰舵机（servo 1）偏转至 1000，向上看。"""
    return _call("look_up")


def look_down() -> dict[str, Any]:
    """云台俯仰舵机（servo 1）偏转至 1700，向下看。"""
    return _call("look_down")


def reset_pose() -> dict[str, Any]:
    """停止底盘 + 两轴舵机回中（PWM 1500）。"""
    return _call("reset_pose")


# ── 传感器 ────────────────────────────────────────────────────────────────────

def front_distance() -> float:
    """读取超声波前方距离，返回厘米；失败返回 -1.0。

    采 7 次取最小值（保守模式），置信度 = 有效采样数 / 总采样数。
    """
    result = _call("front_distance")
    for line in result.get("output", "").splitlines():
        if "front_distance_estimate_cm=" in line:
            try:
                return float(line.split("=", 1)[1])
            except ValueError:
                pass
    return -1.0


# ── 灯光 ─────────────────────────────────────────────────────────────────────

def rgb_on(r: int = 0, g: int = 0, b: int = 255) -> dict[str, Any]:
    """点亮 RGB 灯（LED 1/2 + 声纳灯 0/1）。默认蓝色。"""
    return _call("rgb_on", rgb_red=r, rgb_green=g, rgb_blue=b)


def rgb_off() -> dict[str, Any]:
    """关闭所有 RGB 灯。"""
    return _call("rgb_off")


# ── 系统 ─────────────────────────────────────────────────────────────────────

def shutdown() -> dict[str, Any]:
    """安全关机：执行 sudo shutdown -h now。"""
    return _call("remote_shutdown")
