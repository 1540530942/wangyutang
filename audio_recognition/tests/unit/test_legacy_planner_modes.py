from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from audio_recognition.legacy.planner import LlmTaskPlanner, PlannerError, build_task_planner


BASE_DIR = Path(__file__).resolve().parents[2]
CATALOG_PATH = str((Path(__file__).resolve().parents[1] / "fixtures" / "skill_catalog.fixture.json").resolve())


class PlannerModesTest(unittest.TestCase):
    def test_llm_planner_parses_openai_style_json_content(self) -> None:
        planner = LlmTaskPlanner(
            CATALOG_PATH,
            {"endpoint": "http://planner.local/v1/chat/completions", "model": "test-model"},
        )
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"skill_id": "move_forward", "confidence": 0.92, "reason": "matched"})}}]
        }
        response.raise_for_status.return_value = None
        with patch("audio_recognition.legacy.planner.requests.post", return_value=response):
            plan = planner.plan("前进")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.skill_id, "move_forward")
        self.assertEqual(plan.planner, "llm")

    def test_llm_planner_rejects_unsupported_skill(self) -> None:
        planner = LlmTaskPlanner(
            CATALOG_PATH,
            {"endpoint": "http://planner.local/v1/chat/completions", "model": "test-model"},
        )
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"skill_id": "remote_shutdown", "confidence": 0.99})}}]
        }
        response.raise_for_status.return_value = None
        with patch("audio_recognition.legacy.planner.requests.post", return_value=response):
            with self.assertRaises(PlannerError):
                planner.plan("关机")

    def test_hybrid_planner_falls_back_to_rule(self) -> None:
        planner = build_task_planner(
            {
                "skill_catalog": CATALOG_PATH,
                "planner": {
                    "mode": "hybrid",
                    "llm": {"endpoint": "http://planner.local/v1/chat/completions", "model": "test-model"},
                },
            },
            CATALOG_PATH,
        )
        with patch("audio_recognition.legacy.planner.requests.post", side_effect=RuntimeError("planner offline")):
            plan = planner.plan("前进")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.skill_id, "move_forward")
        self.assertEqual(plan.planner, "rule")
