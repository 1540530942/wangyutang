"""Data-driven tests: load JSON input/expected cases and run against RobotState.

Run from the simulation/ directory:  python -m pytest tests/test_state_cases.py -q
Test data lives in tests/data/state_cases.json (input + expected per case), so
the fixtures are explicit, reviewable JSON rather than inline literals.
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from state import RobotState  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "state_cases.json")
with open(DATA, encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_state_case(case):
    inp, exp = case["input"], case["expected"]
    st = RobotState(**inp.get("initial", {}))
    msg = ""
    for _ in range(int(inp.get("repeat", 1))):
        msg = st.apply(inp["action"], inp.get("settings", {}))

    for key, want in exp.items():
        if key == "msg_contains":
            assert want in msg, f"{case['name']}: msg={msg!r} missing {want!r}"
        elif key == "x_max":
            assert st.x <= want, f"{case['name']}: x={st.x} exceeds max {want}"
        else:
            got = getattr(st, key)
            assert math.isclose(got, want, abs_tol=1e-6) if isinstance(want, (int, float)) else got == want, (
                f"{case['name']}: {key}={got!r} != expected {want!r}"
            )
