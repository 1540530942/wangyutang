from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import pi_camera_sender
import server


def make_jpeg_bytes() -> bytes:
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (16, 16), (34, 90, 140)).save(stream, "JPEG", quality=80)
    return stream.getvalue()


JPEG_BYTES = make_jpeg_bytes()


class FakeCamera(pi_camera_sender.CameraBackend):
    def __init__(self) -> None:
        super().__init__("fake-camera")
        self.closed = False

    def capture_jpeg(self) -> bytes:
        return JPEG_BYTES

    def close(self) -> None:
        self.closed = True


class CameraSnapshotSmokeTests(unittest.TestCase):
    def test_server_records_capture_sources_for_each_mode(self) -> None:
        with TestClient(server.app) as client:
            for mode, source in (
                ("single", "fake-camera"),
                ("screenshot", "screenshot"),
                ("face", "face-screenshot"),
            ):
                task = client.post("/api/capture", json={"mode": mode, "query_gpio": 26}).json()["task"]
                response = client.post(
                    "/api/frame",
                    content=JPEG_BYTES,
                    headers={
                        "Content-Type": "image/jpeg",
                        "X-Device-ID": "smoke-pi",
                        "X-Frame-ID": "1",
                        "X-Task-ID": task["id"],
                        "X-Capture-Source": source,
                    },
                )
                self.assertEqual(response.status_code, 200)
                latest = client.get("/api/latest").json()
                self.assertEqual(latest["task_id"], task["id"])
                self.assertEqual(latest["capture_source"], source)

    def test_sender_reports_real_camera_source_when_camera_capture_works(self) -> None:
        args = SimpleNamespace(quality=78)
        with patch.object(pi_camera_sender, "build_camera", return_value=FakeCamera()):
            jpeg, camera, source, error = pi_camera_sender.capture_camera_or_screenshot_fallback(None, args)
        self.assertIsNone(camera)
        self.assertEqual(source, "fake-camera")
        self.assertEqual(error, "")
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))

    def test_sender_reports_screenshot_fallback_when_camera_capture_fails(self) -> None:
        args = SimpleNamespace(quality=78)
        with patch.object(pi_camera_sender, "build_camera", side_effect=RuntimeError("no camera")), patch.object(
            pi_camera_sender,
            "capture_screenshot_jpeg",
            return_value=JPEG_BYTES,
        ):
            jpeg, camera, source, error = pi_camera_sender.capture_camera_or_screenshot_fallback(None, args)
        self.assertIsNone(camera)
        self.assertEqual(source, "screenshot-fallback")
        self.assertIn("no camera", error)
        self.assertEqual(jpeg, JPEG_BYTES)


if __name__ == "__main__":
    unittest.main()
