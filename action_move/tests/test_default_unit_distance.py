from __future__ import annotations

import json
import unittest
from pathlib import Path

import action_move_executor
import server

try:
    import edge_ros_controller
except ModuleNotFoundError:
    edge_ros_controller = None


BASE_DIR = Path(__file__).resolve().parents[1]


class DefaultUnitDistanceTest(unittest.TestCase):
    def test_catalog_and_server_default_to_ten_cm(self) -> None:
        catalog = json.loads((BASE_DIR / "skill_catalog.json").read_text(encoding="utf-8"))

        self.assertEqual(catalog["defaults"]["unit_distance_cm"], 10.0)
        self.assertEqual(server.DEFAULT_SETTINGS["unit_distance_cm"], 10.0)
        self.assertEqual(server.normalize_settings()["unit_distance_cm"], 10.0)

    def test_executor_fallback_defaults_to_ten_cm(self) -> None:
        self.assertEqual(action_move_executor.merged_defaults({})["unit_distance_cm"], 10.0)
        self.assertEqual(action_move_executor.unit_duration_ms({"unit_distance_cm": 10.0}, "move"), 1600)

    def test_edge_controller_fallback_defaults_to_ten_cm(self) -> None:
        if edge_ros_controller is None:
            self.skipTest("edge_ros_controller requires ROS packages")
        self.assertEqual(edge_ros_controller.merged_defaults({})["unit_distance_cm"], 10.0)
        self.assertEqual(edge_ros_controller.unit_duration_ms({"unit_distance_cm": 10.0}, "move"), 1600)


if __name__ == "__main__":
    unittest.main()
