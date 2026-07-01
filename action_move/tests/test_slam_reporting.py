from __future__ import annotations

import math
import unittest

from slam_reporting import parse_edge_actuals, slam_odometry_payload


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
        # No IMU actuals -> commanded, tagged as such.
        self.assertEqual(payload["source"], "action_move:move_forward:commanded:task-forward")

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
        self.assertAlmostEqual(payload["dyaw_rad"], math.pi / 2.0, places=5)
        self.assertEqual(payload["source"], "action_move:turn_left:commanded:task-left")

    def test_imu_actual_distance_overrides_commanded(self) -> None:
        # Commanded 10 cm but the edge measured 12 cm -> slam must use 12 cm.
        payload = slam_odometry_payload(
            {
                "id": "t",
                "skill_id": "move_forward",
                "unit_distance_cm": 10.0,
                "output": "[INFO] elapsed_seconds=1.0\n[IMU] actual_distance_cm=12.0",
            }
        )
        assert payload is not None
        self.assertAlmostEqual(payload["dx_m"], 0.12, places=4)
        self.assertIn(":imu:", payload["source"])

    def test_imu_actual_yaw_overrides_commanded(self) -> None:
        payload = slam_odometry_payload(
            {
                "id": "t",
                "skill_id": "turn_left",
                "turn_angle_deg": 45.0,
                "output": "[IMU] actual_yaw_deg=43.5",
            }
        )
        assert payload is not None
        self.assertAlmostEqual(math.degrees(payload["dyaw_rad"]), 43.5, places=1)
        self.assertIn(":imu:", payload["source"])

    def test_parse_edge_actuals(self) -> None:
        self.assertEqual(parse_edge_actuals("[IMU] actual_distance_cm=12.3"), {"actual_distance_cm": 12.3})
        self.assertEqual(parse_edge_actuals("nothing here"), {})


if __name__ == "__main__":
    unittest.main()
