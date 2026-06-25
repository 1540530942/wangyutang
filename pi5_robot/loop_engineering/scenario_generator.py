from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Literal

ScenarioType = Literal["clear", "obstacle", "tight", "low_battery", "random"]

TASKS = [
    "巡查门口区域",
    "向前走",
    "环境巡检",
    "检查前方",
    "patrol the corridor",
]


@dataclass
class Scenario:
    id: str
    task: str
    front_cm: float
    left_cm: float
    right_cm: float
    battery: float
    heading_deg: float
    pose_x: float
    pose_y: float
    expected_outcome: str  # "move" | "slow" | "stop" | "reject"
    tags: list[str] = field(default_factory=list)


class ScenarioGenerator:
    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def sample(self, scenario_type: ScenarioType = "random") -> Scenario:
        if scenario_type == "random":
            scenario_type = self._rng.choice(["clear", "obstacle", "tight", "low_battery"])

        ts = int(time.time() * 1000) % 1_000_000
        sid = f"{scenario_type}_{ts:06d}"

        if scenario_type == "clear":
            front_cm = self._rng.uniform(80, 200)
            left_cm = self._rng.uniform(80, 200)
            right_cm = self._rng.uniform(80, 200)
            battery = self._rng.uniform(60, 100)
            expected = "move"
            tags = ["clear_path"]
        elif scenario_type == "obstacle":
            front_cm = self._rng.uniform(35, 79)
            left_cm = self._rng.uniform(40, 120)
            right_cm = self._rng.uniform(40, 120)
            battery = self._rng.uniform(50, 100)
            expected = "slow"
            tags = ["medium_obstacle"]
        elif scenario_type == "tight":
            front_cm = self._rng.uniform(5, 34)
            left_cm = self._rng.uniform(10, 60)
            right_cm = self._rng.uniform(10, 60)
            battery = self._rng.uniform(40, 100)
            expected = "stop"
            tags = ["tight_space", "safety_critical"]
        else:  # low_battery
            front_cm = self._rng.uniform(80, 200)
            left_cm = self._rng.uniform(80, 200)
            right_cm = self._rng.uniform(80, 200)
            battery = self._rng.uniform(5, 29)
            expected = "reject"
            tags = ["low_battery"]

        return Scenario(
            id=sid,
            task=self._rng.choice(TASKS),
            front_cm=round(front_cm, 1),
            left_cm=round(left_cm, 1),
            right_cm=round(right_cm, 1),
            battery=round(battery, 1),
            heading_deg=float(self._rng.choice([0, 45, 90, 135, 180, 225, 270, 315])),
            pose_x=round(self._rng.uniform(-5, 5), 2),
            pose_y=round(self._rng.uniform(-5, 5), 2),
            expected_outcome=expected,
            tags=tags,
        )

    def batch(self, n: int, scenario_type: ScenarioType = "random") -> list[Scenario]:
        return [self.sample(scenario_type) for _ in range(n)]
