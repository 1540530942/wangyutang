from __future__ import annotations

import io
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

MODULE_DIR = Path(__file__).resolve().parents[1]
PLATFORM_DIR = MODULE_DIR.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
if str(PLATFORM_DIR) not in sys.path:
    sys.path.append(str(PLATFORM_DIR))
if str(PLATFORM_DIR / "smile_face") not in sys.path:
    sys.path.append(str(PLATFORM_DIR / "smile_face"))

import pi_camera_sender
import server
import smile_face.server as smile_server


def make_jpeg_bytes(color: tuple[int, int, int] = (34, 90, 140)) -> bytes:
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(stream, "JPEG", quality=80)
    return stream.getvalue()


JPEG_BYTES = make_jpeg_bytes()
ALT_JPEG_BYTES = make_jpeg_bytes((120, 30, 40))


class FakeCamera(pi_camera_sender.CameraBackend):
    def __init__(self) -> None:
        super().__init__("fake-camera")
        self.closed = False

    def capture_jpeg(self) -> bytes:
        return JPEG_BYTES

    def close(self) -> None:
        self.closed = True


class CameraSnapshotSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        for kind in server.CAPTURE_KINDS:
            server.state["tasks"][kind] = None
            server.latest_meta_by_kind[kind].clear()
            server.latest_meta_by_kind[kind].update(server.default_latest_meta(kind))
            server.latest_image_path(kind).unlink(missing_ok=True)
        server.state["updated_at"] = 0.0

    def test_static_page_exposes_independent_capture_cards(self) -> None:
        index_html = (MODULE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (MODULE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        static_text = index_html + app_js

        for label in ("鎽勫儚鏈烘埅鍥?, "灞忓箷鎴浘", "琛ㄦ儏鎴浘", "杩滅▼宸℃"):
            self.assertIn(label, index_html)
        for status_text in ("鐪熷疄鎽勫儚澶寸敾闈?, "妗岄潰鎴浘", "绂诲睆娓叉煋", "绛夊緟涓婁紶瓒呮椂"):
            self.assertIn(status_text, app_js)
        for mojibake in ("锟?, "閸?, "閹?, "缁?, "閻?, "閺嶆垼甯?"):
            self.assertNotIn(mojibake, static_text)

    def test_server_stores_latest_frames_per_kind_without_overwrite(self) -> None:
        with TestClient(server.app) as client:
            camera_task = client.post("/api/capture", json={"kind": "camera", "mode": "single", "query_gpio": 26}).json()["task"]
            screen_task = client.post("/api/capture", json={"kind": "screen", "mode": "single", "query_gpio": 26}).json()["task"]
            face_task = client.post("/api/capture", json={"kind": "face", "mode": "single", "query_gpio": 26}).json()["task"]

            for kind, source, task, payload in (
                ("camera", "fake-camera", camera_task, JPEG_BYTES),
                ("screen", "screenshot", screen_task, ALT_JPEG_BYTES),
                ("face", "smile-face-render", face_task, JPEG_BYTES),
            ):
                response = client.post(
                    "/api/frame",
                    content=payload,
                    headers={
                        "Content-Type": "image/jpeg",
                        "X-Device-ID": "smoke-pi",
                        "X-Frame-ID": kind,
                        "X-Task-ID": task["id"],
                        "X-Capture-Kind": kind,
                        "X-Capture-Source": source,
                    },
                )
                self.assertEqual(response.status_code, 200)

            latest_camera = client.get("/api/latest?kind=camera").json()
            latest_screen = client.get("/api/latest?kind=screen").json()
            latest_face = client.get("/api/latest?kind=face").json()
            self.assertEqual(latest_camera["task_id"], camera_task["id"])
            self.assertEqual(latest_screen["task_id"], screen_task["id"])
            self.assertEqual(latest_face["task_id"], face_task["id"])
            self.assertEqual(latest_camera["capture_source"], "fake-camera")
            self.assertEqual(latest_screen["capture_source"], "screenshot")
            self.assertEqual(latest_face["capture_source"], "smile-face-render")

            self.assertEqual(client.get("/api/latest.jpg?kind=camera").content, JPEG_BYTES)
            self.assertEqual(client.get("/api/latest.jpg?kind=screen").content, ALT_JPEG_BYTES)

    def test_control_and_stop_are_kind_scoped(self) -> None:
        with TestClient(server.app) as client:
            camera_task = client.post("/api/capture", json={"kind": "camera", "mode": "continuous", "interval_ms": 1000}).json()["task"]
            face_task = client.post("/api/capture", json={"kind": "face", "mode": "single"}).json()["task"]

            control = client.get("/api/control").json()
            self.assertEqual(control["task"]["id"], camera_task["id"])
            self.assertEqual(control["tasks"]["camera"]["id"], camera_task["id"])
            self.assertEqual(control["tasks"]["face"]["id"], face_task["id"])

            stopped = client.post("/api/stop?kind=face").json()
            self.assertEqual(stopped["tasks"]["face"]["status"], "stopped")
            self.assertEqual(stopped["tasks"]["camera"]["status"], "pending")

    def test_task_status_failure_is_kind_scoped_and_records_error(self) -> None:
        with TestClient(server.app) as client:
            camera_task = client.post("/api/capture", json={"kind": "camera", "mode": "single"}).json()["task"]
            screen_task = client.post("/api/capture", json={"kind": "screen", "mode": "single"}).json()["task"]

            failed = client.post(
                "/api/task-status",
                json={
                    "kind": "camera",
                    "task_id": camera_task["id"],
                    "status": "failed",
                    "device_id": "smoke-pi",
                    "capture_source": "camera-error",
                    "capture_error": "no camera",
                },
            ).json()

            self.assertEqual(failed["task"]["status"], "failed")
            latest_camera = client.get("/api/latest?kind=camera").json()
            latest_screen = client.get("/api/latest?kind=screen").json()
            self.assertEqual(latest_camera["task_id"], camera_task["id"])
            self.assertEqual(latest_camera["capture_error"], "no camera")
            self.assertEqual(latest_screen["task_id"], "")
            self.assertEqual(client.get("/api/control").json()["tasks"]["screen"]["id"], screen_task["id"])

    def test_legacy_mode_mapping_and_frame_kind_inference(self) -> None:
        with TestClient(server.app) as client:
            task = client.post("/api/capture", json={"mode": "face"}).json()["task"]
            self.assertEqual(task["kind"], "face")
            response = client.post(
                "/api/frame",
                content=JPEG_BYTES,
                headers={
                    "Content-Type": "image/jpeg",
                    "X-Task-ID": task["id"],
                    "X-Capture-Source": "face-screenshot",
                },
            )
            self.assertEqual(response.status_code, 200)
            latest = client.get("/api/latest?kind=face").json()
            self.assertEqual(latest["task_id"], task["id"])

    def test_sender_reports_real_camera_source_when_camera_capture_works(self) -> None:
        args = SimpleNamespace(quality=78, close_camera_after_frame=True)
        with patch.object(pi_camera_sender, "build_camera", return_value=FakeCamera()):
            jpeg, camera, source, error = pi_camera_sender.capture_camera_or_screenshot_fallback(None, args)
        self.assertIsNone(camera)
        self.assertEqual(source, "fake-camera")
        self.assertEqual(error, "")
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))

    def test_sender_does_not_fallback_to_screenshot_when_camera_capture_fails(self) -> None:
        args = SimpleNamespace(quality=78, close_camera_after_frame=True)
        with patch.object(pi_camera_sender, "build_camera", side_effect=RuntimeError("no camera")), patch.object(
            pi_camera_sender,
            "capture_screenshot_jpeg",
            return_value=JPEG_BYTES,
        ):
            with self.assertRaises(RuntimeError):
                pi_camera_sender.capture_camera_or_screenshot_fallback(None, args)

    def test_sender_upload_frame_sends_capture_kind_header(self) -> None:
        session = Mock()
        session.post.return_value.raise_for_status.return_value = None
        pi_camera_sender.upload_frame(
            session,
            "http://server",
            "",
            "smoke-pi",
            1,
            "task-1",
            "face",
            JPEG_BYTES,
            {},
            "smile-face-render",
        )
        headers = session.post.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Capture-Kind"], "face")
        self.assertEqual(headers["X-Capture-Source"], "smile-face-render")

    def test_sender_upload_task_status_reports_failure(self) -> None:
        session = Mock()
        session.post.return_value.raise_for_status.return_value = None
        pi_camera_sender.upload_task_status(
            session,
            "http://server",
            "",
            "smoke-pi",
            "task-1",
            "camera",
            "failed",
            "camera-error",
            "no camera",
        )
        self.assertEqual(session.post.call_args.args[0], "http://server/api/task-status")
        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["kind"], "camera")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["capture_error"], "no camera")

    def test_sender_reports_inspection_before_screen_capture(self) -> None:
        args = SimpleNamespace(quality=78, token="", device_id="smoke-pi")
        task = {"id": "inspect-1", "query_gpio": 26, "mode": "inspect"}
        events = []

        def fake_upload_inspection(*_args):
            events.append("inspection")

        def fake_capture(_args):
            events.append("capture")
            raise RuntimeError("display unavailable")

        with patch.object(pi_camera_sender, "upload_inspection", side_effect=fake_upload_inspection), patch.object(
            pi_camera_sender,
            "collect_inspection",
            return_value={"device_id": "smoke-pi", "task_id": "inspect-1", "cpu_usage_percent": 10.0},
        ), patch.object(pi_camera_sender, "capture_desktop_screenshot_jpeg", side_effect=fake_capture):
            with self.assertRaises(RuntimeError):
                pi_camera_sender.handle_task("screen", task, args, Mock(), "http://server", 0, None)

        self.assertEqual(events, ["capture", "inspection"])

    def test_parse_cpu_usage_uses_proc_stat_deltas(self) -> None:
        usage = pi_camera_sender.parse_cpu_usage("cpu  100 0 100 800 0", "cpu  150 0 150 900 0")
        self.assertEqual(usage, 50.0)

    def test_smile_face_render_endpoint_returns_jpeg(self) -> None:
        with TestClient(smile_server.app) as client:
            first = client.get("/api/face/render.jpg")
            second = client.get("/api/face/render.jpg")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["content-type"], "image/jpeg")
        self.assertTrue(first.content.startswith(b"\xff\xd8"))
        self.assertEqual(first.content, second.content)

    def test_ros_web_video_backend_reports_missing_usb_camera_device(self) -> None:
        backend = pi_camera_sender.WebVideoServerBackend.__new__(pi_camera_sender.WebVideoServerBackend)
        backend.ros_container = "turbopi"
        backend.ros_image_topic = "/image_raw"

        def fake_run(command, **kwargs):
            joined = " ".join(command)
            if "ros2 topic info" in joined:
                return subprocess.CompletedProcess(command, 0, stdout="Publisher count: 0\n", stderr="")
            if "test -e /dev/video0" in joined:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {command}")

        with patch.object(pi_camera_sender.subprocess, "run", side_effect=fake_run):
            diagnostics = backend.ensure_ros_camera_publisher()
        self.assertIn("/image_raw has no publisher", diagnostics)
        self.assertIn("/dev/video0 is not present", diagnostics)


if __name__ == "__main__":
    unittest.main()
