from __future__ import annotations

import math
import unittest

from slam_core import SlamMapper


class SlamMapperTest(unittest.TestCase):
    def test_forward_20cm_reports_actual_pose_delta(self) -> None:
        mapper = SlamMapper()

        snap = mapper.update_odometry(dx_m=0.2, source="move_forward_20cm")

        self.assertFalse(snap["map_available"])
        self.assertEqual(snap["pose"]["distance_travelled_cm"], 20.0)
        self.assertEqual(snap["pose"]["x_m"], 0.2)
        self.assertEqual(snap["pose"]["y_m"], 0.0)
        self.assertEqual(snap["pose"]["source"], "move_forward_20cm")

    def test_turn_left_90deg_then_forward_changes_coordinate_axis(self) -> None:
        mapper = SlamMapper()

        mapper.update_odometry(dx_m=0.0, dyaw_rad=math.pi / 2.0, source="turn_left_90deg")
        snap = mapper.update_odometry(dx_m=0.2, source="forward_after_left_turn")

        self.assertAlmostEqual(snap["pose"]["x_m"], 0.0, places=4)
        self.assertEqual(snap["pose"]["y_m"], 0.2)
        self.assertEqual(snap["pose"]["yaw_deg"], 90.0)
        self.assertEqual(snap["pose"]["distance_travelled_cm"], 20.0)

    def test_front_80cm_scan_marks_occupancy_grid_obstacle(self) -> None:
        mapper = SlamMapper()
        mapper.configure({"map_enabled": True, "resolution_m": 0.1, "size_m": 2.0})

        snap = mapper.update_scan([0.8], angle_min_rad=0.0, angle_increment_rad=math.radians(1))

        occupied = sum(1 for row in snap["map"]["values"] for value in row if value == 100)
        self.assertTrue(snap["map_available"])
        self.assertEqual(occupied, 1)


if __name__ == "__main__":
    unittest.main()
