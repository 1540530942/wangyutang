from __future__ import annotations

import math
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


def slam_odometry_payload(task: dict[str, Any]) -> dict[str, Any] | None:
    skill_id = str(task.get("skill_id") or "")
    if skill_id in TRANSLATION_SKILLS:
        x_scale, y_scale = TRANSLATION_SKILLS[skill_id]
        distance_m = float(task.get("unit_distance_cm") or 0.0) / 100.0
        return {
            "dx_m": round(distance_m * x_scale, 4),
            "dy_m": round(distance_m * y_scale, 4),
            "dyaw_rad": 0.0,
            "source": f"action_move:{skill_id}:{task.get('id', '')}",
        }
    if skill_id in TURN_SKILLS:
        angle_rad = math.radians(float(task.get("turn_angle_deg") or 0.0)) * TURN_SKILLS[skill_id]
        return {
            "dx_m": 0.0,
            "dy_m": 0.0,
            "dyaw_rad": round(angle_rad, 6),
            "source": f"action_move:{skill_id}:{task.get('id', '')}",
        }
    return None
