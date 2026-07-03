from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from audio_recognition.web import server
from audio_recognition.storage.case_store import append_case, find_case, load_cases, write_audio_bytes


class IntermediateStoreTest(unittest.TestCase):
    def test_audio_bytes_and_case_are_persisted_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            audio = write_audio_bytes(
                data_dir=data_dir,
                case_id="case-1",
                content=b"RIFFfake",
                original_filename="../capture.wav",
                content_type="audio/wav",
            )
            case = append_case(
                data_dir,
                {
                    "case_id": "case-1",
                    "source": "test",
                    "device_id": "unit",
                    "text": "forward",
                    "audio": audio,
                    "raw_asr": {"text": "forward"},
                    "plan": {"skill_id": "move_forward"},
                    "skill_id": "move_forward",
                },
            )

            self.assertEqual(case["case_id"], "case-1")
            self.assertTrue((data_dir / audio["audio_path"]).exists())
            self.assertEqual(load_cases(data_dir, limit=10)[0]["text"], "forward")
            self.assertEqual(find_case(data_dir, "case-1")["skill_id"], "move_forward")


class OfflineSimulationApiTest(unittest.TestCase):
    def test_simulate_text_does_not_persist_synthetic_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("audio_recognition.web.server.DATA_DIR", Path(tmp)), patch(
            "audio_recognition.web.server.route_transcript",
            return_value={
                "skill_id": "move_forward",
                "plan": {"skill_id": "move_forward", "route": "action"},
                "action_task": None,
                "action_error": "",
                "face_task": None,
                "face_error": "",
            },
        ) as route_transcript:
            with TestClient(server.app) as client:
                response = client.post("/api/simulate/text", json={"device_id": "unit", "text": "forward"})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["skill"]["id"], "move_forward")
            self.assertEqual(payload["simulation"]["dispatch"], "disabled")
            route_transcript.assert_called_once()
            self.assertEqual(load_cases(Path(tmp), limit=10), [])


class LabModelSelectionTest(unittest.TestCase):
    def test_router_and_cloud_config_follow_common_lab_lv_selection(self) -> None:
        selection = {
            "llm": {
                "provider": "lv",
                "endpoint": "/common/api/chat/qwen3/completions",
                "model": "Qwen3.5-35B-A3B-Q4_K_M.gguf",
            },
            "vision": {
                "provider": "lv",
                "endpoint": "/common/api/vision/lv/analyze-json",
                "model": "qwen25vl7b-q4km.gguf",
            },
        }
        with patch("audio_recognition.web.server.fetch_lab_model_selection", return_value=selection):
            router = server.build_router_config()
            cloud = server.build_cloud_config(server.fetch_lab_model_selection())

        llm = router["react_agent"]["llm"]
        self.assertEqual(llm["provider"], "lv")
        self.assertEqual(llm["model"], "Qwen3.5-35B-A3B-Q4_K_M.gguf")
        self.assertEqual(llm["endpoint"], "https://www.wangyutang.cn/common/api/chat/qwen3/completions")
        self.assertEqual(cloud["vision_provider"], "lv")
        self.assertEqual(cloud["vision_llm_model"], "qwen25vl7b-q4km.gguf")
        self.assertEqual(cloud["vision_analyze_url"], "https://www.wangyutang.cn/common/api/vision/lv/analyze-json")


if __name__ == "__main__":
    unittest.main()
