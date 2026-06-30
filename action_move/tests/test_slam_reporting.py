from __future__ import annotations

import math
import unittest

from slam_reporting import slam_odometry_payload


class SlamReportingTest(unittest.TestCase):
    def test_move_forward_reports_unit_distance_as_dx(self) -> None:
        payload = slam_odometry_payload(
            {
                "id": "task-forward",
                "skill_id": "move_forward",
                "unit_distance_cm": 20.0,
                "turn_angle_deg": 90.0,
            }
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["dx_m"], 0.2)
        self.assertEqual(payload["dy_m"], 0.0)
        self.assertEqual(payload["dyaw_rad"], 0.0)
        self.assertEqual(payload["source"], "action_move:move_forward:task-forward")

    def test_turn_left_reports_turn_angle_as_positive_yaw(self) -> None:
        payload = slam_odometry_payload(
            {
                "id": "task-left",
                "skill_id": "turn_left",
                "unit_distance_cm": 20.0,
                "turn_angle_deg": 90.0,
            }
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["dx_m"], 0.0)
        self.assertEqual(payload["dy_m"], 0.0)
        self.assertAlmostEqual(payload["dyaw_rad"], math.pi / 2.0, places=5)
        self.assertEqual(payload["source"], "action_move:turn_left:task-left")


if __name__ == "__main__":
    unittest.main()
