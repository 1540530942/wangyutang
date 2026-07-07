"""Data-driven evaluator tests from JSON input/expected cases.

Run from loop_engineering/:  python -m pytest tests/test_evaluator_cases.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audio.case import CaseResult, LoopCase  # noqa: E402
from audio.evaluator import apply_obs, evaluate  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "evaluator_cases.json")
with open(DATA, encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]


@pytest.mark.parametrize("c", CASES, ids=[c["name"] for c in CASES])
def test_evaluator_case(c):
    cd = c["case"]
    case = LoopCase(
        id=c["name"],
        transcript=cd.get("transcript", "x"),
        expected_skill_id=cd.get("expected_skill_id", ""),
        expected_route=cd.get("expected_route", "action"),
        obs_check=cd.get("obs_check", {}),
    )
    rd = c["result"]
    res = CaseResult(case_id=c["name"], mode=rd["mode"], transcript=cd.get("transcript", "x"))
    for k, v in rd.items():
        if k != "mode":
            setattr(res, k, v)

    if case.obs_check:
        apply_obs(case, res)
    evaluate(case, res)

    exp = c["expected"]
    if "planning_ok" in exp:
        assert res.planning_ok == exp["planning_ok"], c["name"]
    if "passed" in exp:
        assert res.passed == exp["passed"], c["name"]
    if "score" in exp:
        assert res.score == exp["score"], f"{c['name']}: score={res.score} != {exp['score']}"
    if "obs_passed" in exp:
        assert res.obs_passed == exp["obs_passed"], c["name"]
    if "note_contains" in exp:
        assert any(exp["note_contains"] in n for n in res.notes), f"{c['name']}: notes={res.notes}"
