"""Loop Engineering evaluator for pi5_robot.

Usage:
    cd pi5_robot
    python3 loop_eval.py [--rounds N] [--seed S] [--type TYPE]

Success criteria (PASS):
    pass_rate  >= 0.80
    avg_reward >= 0.70

Allowed files to modify between loops:
    services/harnessd/executor.py
    services/robotd/simulator.py   (behaviour logic only)
    services/visiond/simulator.py

Do NOT modify:
    loop_eval.py
    loop_engineering/scenario_generator.py
    loop_engineering/reward_evaluator.py
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

# Allow running from repo root or from pi5_robot/
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loop_engineering.reward_evaluator import EpisodeScore, RewardEvaluator
from loop_engineering.scenario_generator import Scenario, ScenarioGenerator
from services.harnessd.executor import HarnessExecutor
from services.harnessd.logger import EpisodeLogger
from services.robotd.simulator import RobotController
from services.visiond.simulator import VisionService

PASS_RATE_THRESHOLD = 0.80
PASS_REWARD_THRESHOLD = 0.70
REPORT_PATH = _HERE / "reports" / "loop_history.md"


def _apply_scenario(robot: RobotController, scenario: Scenario) -> None:
    robot.pose.x = scenario.pose_x
    robot.pose.y = scenario.pose_y
    robot.pose.theta = scenario.heading_deg
    robot.battery_percent = scenario.battery
    robot.set_obstacle(
        front_cm=scenario.front_cm,
        left_cm=scenario.left_cm,
        right_cm=scenario.right_cm,
    )


def run_eval(n_rounds: int, seed: int | None, scenario_type: str) -> tuple[float, float, list[EpisodeScore]]:
    gen = ScenarioGenerator(seed=seed)
    evaluator = RewardEvaluator()
    scenarios = gen.batch(n_rounds, scenario_type=scenario_type)  # type: ignore[arg-type]
    scores: list[EpisodeScore] = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        for scenario in scenarios:
            robot = RobotController()
            vision = VisionService(root)
            logger = EpisodeLogger(root)
            harness = HarnessExecutor(robot, vision, logger)

            _apply_scenario(robot, scenario)
            harness.submit(scenario.task)

            # find the episode just created
            episode_dirs = sorted((root / "episodes").iterdir(), key=lambda p: p.stat().st_mtime)
            episode_id = episode_dirs[-1].name if episode_dirs else "unknown"
            events = logger.trace(episode_id)
            score = evaluator.score(episode_id, events, scenario)
            scores.append(score)

    passed = sum(1 for s in scores if s.grade == "PASS")
    pass_rate = passed / len(scores) if scores else 0.0
    avg_reward = sum(s.reward for s in scores) / len(scores) if scores else 0.0
    return pass_rate, avg_reward, scores


def _print_table(scores: list[EpisodeScore]) -> None:
    header = f"{'#':<4} {'grade':<6} {'reward':<8} {'steps':<6} {'tags'}"
    print(header)
    print("-" * 60)
    for i, s in enumerate(scores, 1):
        tags = ",".join(s.details.get("scenario_tags", []))
        print(f"{i:<4} {s.grade:<6} {s.reward:<8.3f} {s.steps:<6} {tags}")


def _append_report(pass_rate: float, avg_reward: float, scores: list[EpisodeScore], overall: str) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    n = len(scores)
    passed = sum(1 for s in scores if s.grade == "PASS")

    lines = [
        f"\n## {timestamp}  [{overall}]",
        f"- rounds: {n}  passed: {passed}  pass_rate: {pass_rate:.2%}  avg_reward: {avg_reward:.3f}",
        "",
        f"| # | grade | reward | steps | tags |",
        f"|---|-------|--------|-------|------|",
    ]
    for i, s in enumerate(scores, 1):
        tags = ",".join(s.details.get("scenario_tags", []))
        lines.append(f"| {i} | {s.grade} | {s.reward:.3f} | {s.steps} | {tags} |")

    with REPORT_PATH.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--type", dest="scenario_type", default="random",
                        choices=["clear", "obstacle", "tight", "low_battery", "random"])
    args = parser.parse_args()

    print(f"Running {args.rounds} scenarios  type={args.scenario_type}  seed={args.seed}")
    print()

    pass_rate, avg_reward, scores = run_eval(args.rounds, args.seed, args.scenario_type)

    _print_table(scores)
    print()
    print(f"pass_rate  : {pass_rate:.2%}  (threshold >= {PASS_RATE_THRESHOLD:.0%})")
    print(f"avg_reward : {avg_reward:.3f}  (threshold >= {PASS_REWARD_THRESHOLD:.3f})")
    print()

    overall = "PASS" if pass_rate >= PASS_RATE_THRESHOLD and avg_reward >= PASS_REWARD_THRESHOLD else "FAIL"
    print(overall)

    _append_report(pass_rate, avg_reward, scores, overall)
    print(f"\nReport appended → {REPORT_PATH.relative_to(_HERE)}")

    sys.exit(0 if overall == "PASS" else 1)


if __name__ == "__main__":
    main()
