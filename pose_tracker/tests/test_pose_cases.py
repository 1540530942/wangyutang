"""Data-driven PoseEstimator tests from JSON input/expected cases.

Run from pose_tracker/:  python -m pytest tests/test_pose_cases.py -q
cmd_vel ops use {"last_vel_dt": s} to rewind _last_vel_t by s seconds so the
next update integrates over a known dt (real wall-clock adds microseconds).
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pose_estimator import PoseEstimator  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "pose_cases.json")
with open(DATA, encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_pose_case(case):
    est = PoseEstimator()
    for op in case["input"]["ops"]:
        if "imu" in op:
            est.update_imu(*op["imu"])
        elif "cmd_vel" in op:
            est.update_cmd_vel(*op["cmd_vel"])
        elif "last_vel_dt" in op:
            est._last_vel_t -= float(op["last_vel_dt"])
        elif op.get("reset"):
            est.reset()

    exp = case["expected"]
    for field in ("x", "y", "yaw"):
        if field in exp:
            tol = exp.get(f"{field}_tol", 1e-6)
            got = getattr(est, field)
            assert math.isclose(got, exp[field], abs_tol=tol), f"{case['name']}: {field}={got} != {exp[field]}"
    if "imu_active" in exp:
        assert est.snapshot()["imu_active"] == exp["imu_active"]
