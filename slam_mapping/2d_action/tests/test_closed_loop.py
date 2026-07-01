from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

# The package dir name (2d_action) is not a valid identifier, so put it on the
# path and import the modules flat — matching slam_mapping's import style.
MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from calibration import MotionCalibration
from closed_loop import ClosedLoopController
from feedback import SimulatedFeedback, SlamPoseFeedback


class _FakeCommander:
    def __init__(self) -> None:
        self.calls: list = []

    def command(self, commanded: float, kind: str) -> None:
        self.calls.append((commanded, kind))


class _FakeSlamFeedback(SlamPoseFeedback):
    """SlamPoseFeedback with the HTTP pose replaced by an in-memory virtual
    robot that moves by commanded*true_scale — exercises the real command/
    measure loop logic without a live slam service."""

    def __init__(self, true_scale: float = 1.2) -> None:
        super().__init__(_FakeCommander(), slam_base="http://x", settle_timeout=1.0, poll_interval=0.0)
        self._scale = true_scale
        self._x = 0.0
        self._yaw_deg = 0.0
        self._ts = 0.0

    def _pose(self) -> dict:
        return {"x_m": self._x, "y_m": 0.0, "yaw_deg": self._yaw_deg, "updated_at": self._ts}

    def command(self, commanded: float, kind: str) -> None:
        self._before = self._pose()
        self._commander.command(commanded, kind)
        if kind == "translate":
            self._x += commanded * self._scale
        else:
            self._yaw_deg += math.degrees(commanded) * self._scale
        self._ts += 1


def build(sim: SimulatedFeedback, cal: MotionCalibration | None = None, **kw) -> ClosedLoopController:
    return ClosedLoopController(command_fn=sim.command, measure_fn=sim.measure, calibration=cal, **kw)


class ClosedLoopTest(unittest.TestCase):
    def test_translate_converges_within_tolerance(self) -> None:
        sim = SimulatedFeedback(true_scale_translate=1.2)
        ctrl = build(sim)
        res = ctrl.move(0.10, "translate")  # want 10 cm actual
        self.assertTrue(res.converged, res.as_dict())
        self.assertLessEqual(abs(res.error), 0.01)
        self.assertAlmostEqual(res.total_actual, 0.10, delta=0.01)

    def test_rotate_converges(self) -> None:
        sim = SimulatedFeedback(true_scale_rotate=0.85)
        ctrl = build(sim, rotate_tol_rad=0.02)
        res = ctrl.move(math.radians(45), "rotate")
        self.assertTrue(res.converged, res.as_dict())
        self.assertAlmostEqual(res.total_actual, math.radians(45), delta=0.02)

    def test_learns_scale_then_single_shot_feedforward(self) -> None:
        # After calibration warms up, feed-forward alone should nail the target
        # in one burst (residual within tolerance, no correction needed).
        sim = SimulatedFeedback(true_scale_translate=1.2)
        cal = MotionCalibration()
        warm = build(sim, cal)
        for _ in range(5):
            warm.move(0.10, "translate")
        self.assertAlmostEqual(cal.scale("translate"), 1.2, delta=0.05)

        res = build(sim, cal, max_iterations=1).move(0.10, "translate")
        self.assertLessEqual(abs(res.error), 0.01, res.as_dict())
        self.assertEqual(res.iterations, 1)

    def test_scale_is_clamped_against_bad_sample(self) -> None:
        sim = SimulatedFeedback(true_scale_translate=100.0)  # absurd
        cal = MotionCalibration()
        build(sim, cal).move(0.10, "translate")
        self.assertLessEqual(cal.scale("translate"), 2.5)  # MAX_SCALE guard

    def test_slam_pose_feedback_closes_loop(self) -> None:
        # Full software wiring: command via action_move, measure via slam pose
        # delta (which reflects IMU actuals once the edge emits them).
        fb = _FakeSlamFeedback(true_scale=1.2)
        ctrl = ClosedLoopController(command_fn=fb.command, measure_fn=fb.measure, max_iterations=4)
        res = ctrl.move(0.10, "translate")
        self.assertTrue(res.converged, res.as_dict())
        self.assertAlmostEqual(res.total_actual, 0.10, delta=0.012)
        self.assertGreaterEqual(len(fb._commander.calls), 1)

    def test_reports_bursts_for_traceability(self) -> None:
        sim = SimulatedFeedback(true_scale_translate=1.3)
        res = build(sim).move(0.20, "translate")
        self.assertGreaterEqual(len(res.bursts), 1)
        for b in res.bursts:
            self.assertIn("commanded", b)
            self.assertIn("actual", b)
            self.assertIn("error", b)


if __name__ == "__main__":
    unittest.main()
