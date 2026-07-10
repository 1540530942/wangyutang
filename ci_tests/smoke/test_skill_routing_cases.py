"""Data-driven smoke: JSON skill-routing cases vs live cases + registry."""
import json
import os

import pytest

from ci_tests.smoke.cases import SKILL_CASES
from robot_sandbox.skills.registry import load_skill_registry

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(os.path.dirname(__file__), "data", "skill_routing_cases.json")
with open(DATA, encoding="utf-8") as fh:
    DOC = json.load(fh)
CASES = DOC["cases"]
REGISTRY = load_skill_registry(os.path.join(_ROOT, "robot_sandbox", "skills", "registry.yaml"))


def test_json_matches_live_cases():
    live = {(c.transcript, c.skill_id, c.tool, c.route) for c in SKILL_CASES}
    js = {(c["transcript"], c["expected"]["skill_id"], c["expected"]["tool"], c["expected"]["route"]) for c in CASES}
    assert js == live, "skill_routing_cases.json drifted from ci_tests/smoke/cases.py"


@pytest.mark.parametrize("c", CASES, ids=[f'{c["transcript"]}->{c["expected"]["skill_id"]}' for c in CASES])
def test_expected_skill_defined_in_registry(c):
    exp = c["expected"]
    sk = REGISTRY.get(exp["skill_id"])
    assert sk is not None, f"skill {exp['skill_id']} missing from registry"
    assert sk.tool == exp["tool"], f"{exp['skill_id']}: tool {sk.tool} != {exp['tool']}"
    assert sk.route == exp["route"], f"{exp['skill_id']}: route {sk.route} != {exp['route']}"
