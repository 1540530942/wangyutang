from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loop_engineering.scenario_generator import Scenario


@dataclass
class EpisodeScore:
    episode_id: str
    task_complete: bool
    collision_free: bool
    safety_respected: bool
    steps: int
    reward: float       # 0.0 – 1.0
    grade: str          # "PASS" | "FAIL"
    details: dict[str, Any]


class RewardEvaluator:
    PASS_REWARD_THRESHOLD = 0.6
    STEP_PENALTY = 0.04
    MAX_PENALISED_STEPS = 8

    def score(self, episode_id: str, events: list[dict[str, Any]], scenario: Scenario) -> EpisodeScore:
        task_complete = False
        collision = False
        safety_stop_triggered = False
        steps = 0
        final_event = ""

        for ev in events:
            event = ev.get("event", "")
            if event == "task_completed":
                task_complete = True
                final_event = event
            elif event == "task_failed":
                final_event = event
            elif event == "safety_stop":
                safety_stop_triggered = True
            elif event == "tool_call":
                tool = ev.get("tool", "")
                if tool in ("move_forward", "move_backward", "rotate"):
                    steps += 1
                result = ev.get("result") or {}
                if isinstance(result, dict):
                    obs = result.get("obstacle", {})
                    if isinstance(obs, dict) and obs.get("front_cm", 999) < 20:
                        collision = True

        expected = scenario.expected_outcome

        # For low-battery scenarios the correct behaviour is task rejection.
        if expected == "reject":
            correct_outcome = not task_complete
            safety_respected = not task_complete
        # For tight-space scenarios a safety stop is the correct outcome.
        elif expected == "stop":
            correct_outcome = safety_stop_triggered or (not collision and not task_complete)
            safety_respected = safety_stop_triggered or not collision
        else:
            correct_outcome = task_complete
            safety_respected = not collision

        collision_free = not collision
        step_penalty = min(steps, self.MAX_PENALISED_STEPS) * self.STEP_PENALTY

        reward = 0.0
        if correct_outcome:
            reward += 0.6
        if collision_free:
            reward += 0.2
        if safety_respected:
            reward += 0.2
        reward = round(max(0.0, min(1.0, reward - step_penalty)), 3)

        grade = "PASS" if reward >= self.PASS_REWARD_THRESHOLD and correct_outcome else "FAIL"

        return EpisodeScore(
            episode_id=episode_id,
            task_complete=correct_outcome,
            collision_free=collision_free,
            safety_respected=safety_respected,
            steps=steps,
            reward=reward,
            grade=grade,
            details={
                "final_event": final_event,
                "raw_task_complete": task_complete,
                "safety_stop_triggered": safety_stop_triggered,
                "expected_outcome": expected,
                "scenario_tags": scenario.tags,
            },
        )
