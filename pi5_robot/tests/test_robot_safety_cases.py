"""Data-driven RobotController safety tests from JSON input/expected cases.

Run from pi5_robot/:  python -m pytest tests/test_robot_safety_cases.py -q
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.robotd.simulator import RobotCommandError, RobotController  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "robot_safety_cases.json")
with open(DATA, encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]


def _run(ops, robot):
    """Run ops; return the last move/rotate state, or raise RobotCommandError."""
    state = None
    for op in ops:
        if "set_obstacle" in op:
            robot.set_obstacle(**op["set_obstacle"])
        elif "move" in op:
            state = robot.move(op["move"][0], op["move"][1])
        elif "rotate" in op:
            state = robot.rotate(op["rotate"])
    return state


@pytest.mark.parametrize("c", CASES, ids=[c["name"] for c in CASES])
def test_robot_safety_case(c):
    robot = RobotController()
    exp = c["expected"]
    if exp.get("raises"):
        with pytest.raises(RobotCommandError):
            _run(c["ops"], robot)
        return
    state = _run(c["ops"], robot)
    if exp.get("no_raise"):
        return  # reaching here means no exception was raised
    if "x" in exp:
        assert math.isclose(state["pose"]["x"], exp["x"], abs_tol=exp.get("x_tol", 1e-6)), c["name"]
    if "last_command" in exp:
        assert state["motion"]["last_command"] == exp["last_command"], c["name"]
