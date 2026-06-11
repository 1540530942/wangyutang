from __future__ import annotations

import json
import math
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from audio_recognition.web import server
from audio_recognition.tools.executors import execute_planned_task
from audio_recognition.harness.react_loop import route_transcript, transcribe_audio_path
from audio_recognition.legacy.planner import build_task_planner


BASE_DIR = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "audio_cases.json"
CATALOG_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "skill_catalog.fixture.json"


class FixtureAudioProvider:
    def __init__(self, transcripts: dict[str, str]):
        self.transcripts = transcripts

    def transcribe(self, wav_path: str | Path) -> dict[str, object]:
        fixture_id = Path(wav_path).stem
        return {"text": self.transcripts.get(fixture_id, ""), "raw": {"fixture_id": fixture_id}}


def llm_response(payload: dict) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"text": json.dumps(payload, ensure_ascii=False)}
    return response


def action_response(skill_id: str, *, text: str = "", order: int = 1) -> Mock:
    return llm_response(
        {
            "protocol_version": "react_v1_single_tool",
            "tool_call": {
                "tool": "dispatch_action",
                "args": {
                    "skill_id": skill_id,
                    "order": order,
                    "duration_ms": 800,
                    "wait_until": "completed",
                    "confidence": 0.9,
                    "text": text or skill_id,
                },
            },
        }
    )


def finish_response(order: int = 2) -> Mock:
    return llm_response({"protocol_version": "react_v1_single_tool", "type": "finish", "final": "done"})


def write_fixture_wav(target: Path, seconds: float = 0.25, sample_rate: int = 16000) -> None:
    frames = int(seconds * sample_rate)
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(frames):
            sample = int(1600 * math.sin(2 * math.pi * 440 * (index / sample_rate)))
            handle.writeframesraw(sample.to_bytes(2, byteorder="little", signed=True))


class AudioRegressionSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        cls.transcripts = {case["id"]: case["transcript"] for case in cls.cases}
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="audio-regression-"))
        for case in cls.cases:
            write_fixture_wav(cls.temp_dir / f"{case['id']}.wav")
        cls.provider = FixtureAudioProvider(cls.transcripts)
        cls.router_config = {"skill_catalog": str(CATALOG_PATH.resolve())}
        cls.react_router_config = {
            **cls.router_config,
            "react_agent": {"mode": "llm", "llm": {"endpoint": "http://llm.local", "model": "qwen3.5-9b"}},
        }

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_case_count_is_20_plus(self) -> None:
        self.assertGreaterEqual(len(self.cases), 20)

    def test_audio_cases_return_real_asr_without_legacy_planner(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["id"]):
                result = transcribe_audio_path(
                    base_dir=BASE_DIR,
                    wav_path=self.temp_dir / f"{case['id']}.wav",
                    provider=self.provider,
                    router_config=self.router_config,
                )
                self.assertEqual(result["text"], case["transcript"])
                self.assertEqual(result["raw"]["fixture_id"], case["id"])
                self.assertEqual(result["skill_id"], "")
                self.assertIsNone(result["plan"])

    def test_rule_planner_builds_expected_plan(self) -> None:
        planner = build_task_planner(self.router_config, self.router_config["skill_catalog"])
        plan = planner.plan("前进")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.skill_id, "move_forward")
        self.assertEqual(plan.route, "action")
        self.assertEqual(plan.planner, "rule")

    def test_route_transcript_returns_none_for_unknown_text(self) -> None:
        routed = route_transcript(
            base_dir=BASE_DIR,
            text="今天上海天气怎么样",
            router_config=self.router_config,
            cloud_config={"action_enabled": True, "face_enabled": True},
            route_action=True,
            source="test",
        )
        self.assertEqual(routed["skill_id"], "")
        self.assertIsNone(routed["plan"])

    def test_execute_planned_task_routes_action(self) -> None:
        with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
            routed = route_transcript(
                base_dir=BASE_DIR,
                text="左转",
                router_config=self.react_router_config,
                cloud_config={"action_enabled": True, "action_server": "http://action.local"},
                route_action=False,
                source="test",
            )
        with patch("audio_recognition.tools.executors.create_action_task", return_value={"task": {"id": "t1"}}) as create_action_task:
            result = execute_planned_task(
                build_task_planner(self.router_config, self.router_config["skill_catalog"]).plan("前进"),
                "前进",
                {"action_enabled": True, "action_server": "http://action.local"},
                "test",
            )
        create_action_task.assert_called_once()
        self.assertEqual(result["action_task"], {"task": {"id": "t1"}})
        self.assertEqual(routed["skill_id"], "turn_left")

    def test_execute_planned_task_routes_face(self) -> None:
        plan = build_task_planner(self.router_config, self.router_config["skill_catalog"]).plan("笑一笑")
        with patch("audio_recognition.tools.executors.create_face_task", return_value={"state": {"emotion": "happy"}}) as create_face_task:
            result = execute_planned_task(
                plan,
                "笑一笑",
                {"face_enabled": True, "face_server": "http://face.local"},
                "test",
            )
        create_face_task.assert_called_once()
        self.assertEqual(result["face_task"], {"state": {"emotion": "happy"}})

    def test_api_recognize_text_exposes_plan_and_skill(self) -> None:
        with TestClient(server.app) as client, patch("audio_recognition.web.server.ACTION_SERVER", "http://action.local"), patch(
            "audio_recognition.web.server.route_transcript",
            return_value={
                "skill_id": "move_forward",
                "plan": {"skill_id": "move_forward", "route": "action", "planner": "rule"},
                "action_task": {"task": {"id": "task-1"}},
                "action_error": "",
                "face_task": None,
                "face_error": "",
            },
        ):
            response = client.post(
                "/api/recognize-text",
                json={"device_id": "test", "text": "前进", "route_action": True},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["skill"]["id"], "move_forward")
        self.assertEqual(payload["plan"]["route"], "action")
