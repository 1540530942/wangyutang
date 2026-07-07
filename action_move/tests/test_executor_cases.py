"""Data-driven action_move_executor pure-function tests from JSON cases.

Run from action_move/:  python -m pytest tests/test_executor_cases.py -q
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import action_move_executor as ex  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "executor_cases.json")
with open(DATA, encoding="utf-8") as fh:
    DOC = json.load(fh)


@pytest.mark.parametrize("c", DOC["clamp_cases"], ids=[f"clamp{c['input']}" for c in DOC["clamp_cases"]])
def test_clamp(c):
    assert math.isclose(ex.clamp(*c["input"]), c["expected"], abs_tol=1e-9)


@pytest.mark.parametrize("c", DOC["unit_duration_cases"], ids=[f"{c['kind']}_{c['expected']}" for c in DOC["unit_duration_cases"]])
def test_unit_duration(c):
    assert ex.unit_duration_ms(c["defaults"], c["kind"]) == c["expected"], c.get("why", "")


@pytest.mark.parametrize("c", DOC["velocity_scale_cases"], ids=[str(c["expected"]) for c in DOC["velocity_scale_cases"]])
def test_velocity_scale(c):
    assert math.isclose(ex.velocity_scale(c["defaults"]), c["expected"], abs_tol=1e-9)


@pytest.mark.parametrize("c", DOC["cmd_vel_topics_cases"], ids=[",".join(c["expected"]) for c in DOC["cmd_vel_topics_cases"]])
def test_cmd_vel_topics(c):
    assert ex.cmd_vel_topics(c["defaults"]) == c["expected"]
