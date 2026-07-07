"""Data-driven SlamMapper tests from JSON input/expected cases.

Run from slam_mapping/:  python -m pytest tests/test_slam_cases.py -q
Angles are stored in degrees in the JSON and converted to radians here.
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from slam_core import SlamMapper  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "slam_cases.json")
with open(DATA, encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]


@pytest.mark.parametrize("c", CASES, ids=[c["name"] for c in CASES])
def test_slam_case(c):
    mapper = SlamMapper()
    snap = {}
    for op in c["ops"]:
        if "configure" in op:
            mapper.configure(op["configure"])
        elif "odometry" in op:
            o = op["odometry"]
            snap = mapper.update_odometry(
                dx_m=float(o.get("dx_m", 0.0)),
                dyaw_rad=math.radians(float(o.get("dyaw_deg", 0.0))),
                source=o.get("source", "test"),
            )
        elif "scan" in op:
            s = op["scan"]
            snap = mapper.update_scan(
                s["ranges"],
                angle_min_rad=math.radians(float(s.get("angle_min_deg", 0.0))),
                angle_increment_rad=math.radians(float(s.get("angle_increment_deg", 1.0))),
            )

    if "expected_map_available" in c:
        assert snap["map_available"] == c["expected_map_available"], c["name"]
    for key, want in c.get("expected_pose", {}).items():
        if key.endswith("_tol"):
            continue
        got = snap["pose"][key]
        tol = c["expected_pose"].get(f"{key.split('_m')[0]}_tol") if key.endswith("_m") else None
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            assert math.isclose(got, want, abs_tol=tol or 1e-6), f"{c['name']}: pose.{key}={got} != {want}"
        else:
            assert got == want, f"{c['name']}: pose.{key}={got!r} != {want!r}"
    if "expected_occupied" in c:
        occ = sum(1 for row in snap["map"]["values"] for v in row if v == 100)
        assert occ == c["expected_occupied"], f"{c['name']}: occupied={occ} != {c['expected_occupied']}"
