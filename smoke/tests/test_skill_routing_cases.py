"""Data-driven smoke test: JSON skill-routing cases vs live cases + registry.

Run from repo root:  python -m pytest smoke/tests/test_skill_routing_cases.py -q
The JSON (smoke/tests/data/skill_routing_cases.json) records input=transcript
and expected=skill_id/tool/route. This loader verifies:
  1. the JSON has not drifted from the live SKILL_CASES in smoke/cases.py;
  2. every expected skill_id is defined in the robot_sandbox registry with the
     same tool + route (catches registry/case-data drift).
Full LLM routing behaviour is covered by smoke/test_routing.py.
"""
import json
import os

import pytest

from smoke.cases import SKILL_CASES
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
    assert js == live, "skill_routing_cases.json drifted from smoke/cases.py SKILL_CASES"


@pytest.mark.parametrize("c", CASES, ids=[f'{c["transcript"]}->{c["expected"]["skill_id"]}' for c in CASES])
def test_expected_skill_defined_in_registry(c):
    exp = c["expected"]
    sk = REGISTRY.get(exp["skill_id"])
    assert sk is not None, f"skill {exp['skill_id']} missing from registry"
    assert sk.tool == exp["tool"], f"{exp['skill_id']}: tool {sk.tool} != {exp['tool']}"
    assert sk.route == exp["route"], f"{exp['skill_id']}: route {sk.route} != {exp['route']}"
