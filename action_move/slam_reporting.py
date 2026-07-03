from __future__ import annotations

import math
import re
from typing import Any


TRANSLATION_SKILLS = {
    "move_forward": (1.0, 0.0),
    "move_backward": (-1.0, 0.0),
    "move_left": (0.0, 1.0),
    "move_right": (0.0, -1.0),
}
TURN_SKILLS = {
    "turn_left": 1.0,
    "turn_right": -1.0,
    "rotate_in_place": 1.0,
}


def parse_edge_actuals(output: str) -> tuple[dict[str, float], str]:
    """Extract measured actuals from the edge controller output.

    Returns (actuals_dict, source_tag) where source_tag is:
    - "imu"      — hardware IMU measured values ([IMU] prefix)
    - "cmd_vel"  — cmd_vel dead-reckoning estimate ([CMD] prefix)
    - "commanded"— no measurement, use task commanded values
    """
    actuals: dict[str, float] = {}
    source_tag = "commanded"
    output = output or ""
    for key, imu_pattern, cmd_pattern in (
        ("actual_distance_cm", r"\[IMU\] actual_distance_cm=(-?[0-9.]+)", r"\[CMD\] actual_distance_cm=(-?[0-9.]+)"),
        ("actual_yaw_deg", r"\[IMU\] actual_yaw_deg=(-?[0-9.]+)", r"\[CMD\] actual_yaw_deg=(-?[0-9.]+)"),
    ):
        m = re.search(imu_pattern, output)
        if m:
            try:
                actuals[key] = float(m.group(1))
                source_tag = "imu"
            except ValueError:
                pass
            continue
        m = re.search(cmd_pattern, output)
        if m:
            try:
                actuals[key] = float(m.group(1))
                if source_tag == "commanded":
                    source_tag = "cmd_vel"
            except ValueError:
                pass
    return actuals, source_tag


def slam_odometry_payload(task: dict[str, Any]) -> dict[str, Any] | None:
    skill_id = str(task.get("skill_id") or "")
    actuals, measured = parse_edge_actuals(str(task.get("output") or ""))

    if skill_id in TRANSLATION_SKILLS:
        x_scale, y_scale = TRANSLATION_SKILLS[skill_id]
        if "actual_distance_cm" in actuals:
            distance_m = actuals["actual_distance_cm"] / 100.0
        else:
            distance_m = float(task.get("unit_distance_cm") or 0.0) / 100.0
        return {
            "dx_m": round(distance_m * x_scale, 4),
            "dy_m": round(distance_m * y_scale, 4),
            "dyaw_rad": 0.0,
            "source": f"action_move:{skill_id}:{measured}:{task.get('id', '')}",
        }
    if skill_id in TURN_SKILLS:
        if "actual_yaw_deg" in actuals:
            angle_rad = math.radians(actuals["actual_yaw_deg"]) * (1.0 if TURN_SKILLS[skill_id] >= 0 else -1.0)
            # actual_yaw_deg is reported as a signed magnitude of the turn; keep
            # the commanded direction sign for consistency with the skill.
            angle_rad = math.radians(abs(actuals["actual_yaw_deg"])) * TURN_SKILLS[skill_id]
        else:
            angle_rad = math.radians(float(task.get("turn_angle_deg") or 0.0)) * TURN_SKILLS[skill_id]
        return {
            "dx_m": 0.0,
            "dy_m": 0.0,
            "dyaw_rad": round(angle_rad, 6),
            "source": f"action_move:{skill_id}:{measured}:{task.get('id', '')}",
        }
    return None
