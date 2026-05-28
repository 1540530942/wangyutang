from __future__ import annotations

import argparse
import io
import os
import signal
import shlex
import subprocess
import sys
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests


DEFAULT_SERVER = os.environ.get("CAMERA_SNAPSHOT_SERVER", "http://127.0.0.1:8099")
DEFAULT_TOKEN = os.environ.get("CAMERA_SNAPSHOT_TOKEN", "")
DEFAULT_QUERY_GPIO = int(os.environ.get("CAMERA_SNAPSHOT_DEFAULT_GPIO", "26"))


running = True
lock_handle: object | None = None


def handle_signal(signum: int, frame: object) -> None:
    global running
    running = False


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def acquire_single_instance_lock(lock_file: str) -> None:
    global lock_handle
    if os.name != "posix":
        return
    import fcntl

    handle = open(lock_file, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError(f"another pi_camera_sender.py instance already holds {lock_file}") from None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    lock_handle = handle


@dataclass
class CameraBackend:
    name: str

    def capture_jpeg(self) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        return


class Picamera2Backend(CameraBackend):
    def __init__(self, width: int, height: int, quality: int) -> None:
        super().__init__("picamera2")
        from picamera2 import Picamera2

        self.quality = quality
        self.camera = Picamera2()
        config = self.camera.create_still_configuration(main={"size": (width, height), "format": "RGB888"})
        self.camera.configure(config)
        self.camera.start()
        time.sleep(1.0)

    def capture_jpeg(self) -> bytes:
        try:
            stream = io.BytesIO()
            self.camera.capture_file(stream, format="jpeg")
            data = stream.getvalue()
            if data.startswith(b"\xff\xd8"):
                return data
        except Exception as exc:
            print(f"[WARN] picamera2 jpeg encoder unavailable: {exc}", flush=True)

        from PIL import Image

        frame = self.camera.capture_array()
        image = Image.fromarray(frame).convert("RGB")
        stream = io.BytesIO()
        image.save(stream, "JPEG", quality=self.quality)
        data = stream.getvalue()
        if not data.startswith(b"\xff\xd8"):
            raise RuntimeError("picamera2 did not produce JPEG data")
        return data

    def close(self) -> None:
        self.camera.stop()


class OpenCvBackend(CameraBackend):
    def __init__(self, camera_index: int, width: int, height: int, quality: int) -> None:
        super().__init__("opencv")
        import cv2

        self.cv2 = cv2
        self.quality = quality
        self.camera_index = camera_index
        self.cap = None
        candidates = [camera_index] + [idx for idx in range(0, 6) if idx != camera_index]
        for candidate in candidates:
            cap = cv2.VideoCapture(candidate)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            if not cap.isOpened():
                cap.release()
                continue
            ok, frame = cap.read()
            if ok and frame is not None:
                self.camera_index = candidate
                self.cap = cap
                print(f"[INFO] opencv camera index: {candidate}", flush=True)
                break
            cap.release()
        if self.cap is None:
            raise RuntimeError(f"could not open a camera near index {camera_index}")

    def capture_jpeg(self) -> bytes:
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("camera read failed")
        ok, encoded = self.cv2.imencode(".jpg", frame, [int(self.cv2.IMWRITE_JPEG_QUALITY), self.quality])
        if not ok:
            raise RuntimeError("jpeg encode failed")
        return encoded.tobytes()

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()


ROS_SETUP = "source /opt/ros/humble/setup.bash && source /home/ubuntu/ros2_ws/install/setup.bash"


class WebVideoServerBackend(CameraBackend):
    def __init__(
        self,
        snapshot_url: str,
        timeout: float,
        ros_container: str,
        ros_image_topic: str,
        autostart_usb_cam: bool,
    ) -> None:
        super().__init__("web-video-server")
        self.snapshot_url = snapshot_url
        self.timeout = timeout
        self.ros_container = ros_container
        self.ros_image_topic = ros_image_topic
        self.autostart_usb_cam = autostart_usb_cam
        self.session = requests.Session()
        try:
            self.capture_jpeg()
        except Exception as first_exc:
            if not self.autostart_usb_cam:
                raise
            diagnostics = self.ensure_ros_camera_publisher()
            try:
                self.capture_jpeg()
            except Exception as second_exc:
                raise RuntimeError(
                    f"web_video_server unavailable after ROS camera check: {second_exc}; {diagnostics}"
                ) from second_exc

    def capture_jpeg(self) -> bytes:
        response = self.session.get(self.snapshot_url, timeout=self.timeout)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        data = response.content
        if "image/jpeg" not in content_type and "image/jpg" not in content_type:
            raise RuntimeError(f"snapshot endpoint returned {content_type or 'unknown content type'}")
        if not data.startswith(b"\xff\xd8"):
            raise RuntimeError("snapshot endpoint did not return JPEG data")
        return data

    def close(self) -> None:
        self.session.close()

    def ensure_ros_camera_publisher(self) -> str:
        if not self.ros_container:
            return "ROS container is not configured"
        topic_info = self._docker_ros(f"ros2 topic info {shlex.quote(self.ros_image_topic)} -v", timeout=8)
        if not topic_info.returncode and "Publisher count: 0" not in topic_info.stdout:
            return f"{self.ros_image_topic} already has a publisher"

        video_check = self._docker_shell("test -e /dev/video0 && echo /dev/video0-present || true", timeout=5)
        if "/dev/video0-present" not in video_check.stdout:
            return (
                f"{self.ros_image_topic} has no publisher and /dev/video0 is not present in "
                f"container {self.ros_container}"
            )

        start = subprocess.run(
            [
                "docker",
                "exec",
                "-u",
                "ubuntu",
                "-d",
                self.ros_container,
                "bash",
                "-lc",
                (
                    f"{ROS_SETUP} && export need_compile=False && "
                    "ros2 launch peripherals usb_cam.launch.py "
                    ">> /tmp/camera_snapshot_usb_cam.log 2>&1"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if start.returncode:
            return f"failed to start usb_cam.launch.py: {start.stderr.strip() or start.stdout.strip()}"

        deadline = time.time() + 8
        while time.time() < deadline:
            time.sleep(1)
            topic_info = self._docker_ros(f"ros2 topic info {shlex.quote(self.ros_image_topic)} -v", timeout=8)
            if not topic_info.returncode and "Publisher count: 0" not in topic_info.stdout:
                return f"started usb_cam publisher for {self.ros_image_topic}"
        return f"started usb_cam.launch.py but {self.ros_image_topic} still has no publisher"

    def _docker_ros(self, command: str, timeout: float) -> subprocess.CompletedProcess[str]:
        return self._docker_shell(f"{ROS_SETUP} && {command}", timeout=timeout)

    def _docker_shell(self, command: str, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["docker", "exec", self.ros_container, "bash", "-lc", command],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(
                args=["docker", "exec", self.ros_container],
                returncode=1,
                stdout="",
                stderr=str(exc),
            )


class RpicamStillBackend(CameraBackend):
    def __init__(self, width: int, height: int, quality: int, timeout: float) -> None:
        super().__init__("rpicam-still")
        self.width = width
        self.height = height
        self.quality = quality
        self.timeout = timeout
        self.command = self._find_command()
        if not self.command:
            raise RuntimeError("rpicam-still/libcamera-still not found")

    @staticmethod
    def _find_command() -> str:
        for command in ("rpicam-still", "libcamera-still"):
            if subprocess.run(["sh", "-lc", f"command -v {command}"], capture_output=True, text=True).returncode == 0:
                return command
        return ""

    def capture_jpeg(self) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
            jpg_path = Path(handle.name)
        jpg_path.unlink(missing_ok=True)
        try:
            subprocess.run(
                [
                    self.command,
                    "--nopreview",
                    "--timeout",
                    "1000",
                    "--width",
                    str(self.width),
                    "--height",
                    str(self.height),
                    "--quality",
                    str(self.quality),
                    "-o",
                    str(jpg_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            data = jpg_path.read_bytes()
            if not data.startswith(b"\xff\xd8"):
                raise RuntimeError("rpicam-still did not produce JPEG data")
            return data
        finally:
            try:
                jpg_path.unlink()
            except FileNotFoundError:
                pass


def stamp_image(image: object, label: str, quality: int) -> bytes:
    from PIL import ImageDraw

    image = image.convert("RGB")
    draw = ImageDraw.Draw(image)
    text_box = draw.textbbox((0, 0), label)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    padding = 10
    x = 16
    y = max(16, image.height - text_height - padding * 2 - 16)
    draw.rectangle(
        [x - padding, y - padding, x + text_width + padding, y + text_height + padding],
        fill=(0, 0, 0),
    )
    draw.text((x, y), label, fill=(255, 255, 255))
    stream = io.BytesIO()
    image.save(stream, "JPEG", quality=quality)
    data = stream.getvalue()
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError("watermarked image was not JPEG")
    return data


def stamp_jpeg(jpeg: bytes, label_prefix: str, quality: int) -> bytes:
    from PIL import Image

    label = time.strftime(f"{label_prefix} %Y-%m-%d %H:%M:%S")
    return stamp_image(Image.open(io.BytesIO(jpeg)), label, quality)


def build_camera(args: argparse.Namespace) -> CameraBackend:
    if args.backend in {"auto", "web-video-server"}:
        try:
            return WebVideoServerBackend(
                args.web_video_snapshot_url,
                args.web_video_timeout,
                args.ros_container,
                args.ros_image_topic,
                args.ros_usb_cam_autostart,
            )
        except Exception as exc:
            if args.backend == "web-video-server":
                raise
            print(f"[WARN] web_video_server unavailable: {exc}", flush=True)

    if args.backend in {"auto", "rpicam-still"}:
        try:
            return RpicamStillBackend(args.width, args.height, args.quality, args.rpicam_timeout)
        except Exception as exc:
            if args.backend == "rpicam-still":
                raise
            print(f"[WARN] rpicam-still unavailable: {exc}", flush=True)

    if args.backend in {"auto", "picamera2"}:
        try:
            return Picamera2Backend(args.width, args.height, args.quality)
        except Exception as exc:
            if args.backend == "picamera2":
                raise
            print(f"[WARN] picamera2 unavailable: {exc}", flush=True)

    if args.backend in {"auto", "opencv"}:
        try:
            return OpenCvBackend(args.camera_index, args.width, args.height, args.quality)
        except Exception as exc:
            if args.backend == "opencv":
                raise
            print(f"[WARN] opencv unavailable: {exc}", flush=True)

    raise RuntimeError(f"unsupported backend: {args.backend}")


def capture_screenshot_jpeg(quality: int, label_prefix: str = "Screenshot") -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        png_path = Path(handle.name)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
        jpg_path = Path(handle.name)
    png_path.unlink(missing_ok=True)
    jpg_path.unlink(missing_ok=True)
    try:
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
        wayland_socket = next((path.name for path in Path(env["XDG_RUNTIME_DIR"]).glob("wayland-*")), "")
        if wayland_socket:
            env["WAYLAND_DISPLAY"] = wayland_socket
            subprocess.run(
                ["grim", str(png_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            from PIL import Image
            from PIL import ImageDraw

            image = Image.open(png_path).convert("RGB")
        else:
            env.setdefault("DISPLAY", ":0")
            subprocess.run(
                ["scrot", "-q", str(quality), str(jpg_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            from PIL import Image
            from PIL import ImageDraw

            image = Image.open(jpg_path).convert("RGB")
        label = time.strftime(f"{label_prefix} %Y-%m-%d %H:%M:%S")
        data = stamp_image(image, label, quality)
        if not data:
            raise RuntimeError("screen capture produced an empty file")
        if not data.startswith(b"\xff\xd8"):
            raise RuntimeError("screen capture was not JPEG")
        return data
    finally:
        for path in (png_path, jpg_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def capture_camera_or_screenshot_fallback(
    camera: CameraBackend | None,
    args: argparse.Namespace,
) -> tuple[bytes, CameraBackend | None, str, str]:
    try:
        if camera is None:
            camera = build_camera(args)
            print(f"[INFO] camera backend: {camera.name}", flush=True)
        jpeg = camera.capture_jpeg()
        capture_source = camera.name
        try:
            camera.close()
        except Exception as close_exc:
            print(f"[WARN] camera close failed: {close_exc}", flush=True)
        return stamp_jpeg(jpeg, "Camera", args.quality), None, capture_source, ""
    except Exception as exc:
        error = str(exc)
        print(f"[WARN] camera capture unavailable, falling back to screenshot: {error}", flush=True)
        if camera is not None:
            try:
                camera.close()
            except Exception as close_exc:
                print(f"[WARN] camera close failed: {close_exc}", flush=True)
        return capture_screenshot_jpeg(args.quality, "Camera fallback"), None, "screenshot-fallback", error


def fetch_control(session: requests.Session, server: str) -> dict[str, object]:
    try:
        response = session.get(f"{server}/api/control", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        print(f"[WARN] control poll failed: {exc}", flush=True)
        return {"task": None}


def run_text(command: list[str], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
        return (result.stdout or result.stderr).strip()
    except Exception as exc:
        return f"{exc.__class__.__name__}: {exc}"


def collect_inspection(device_id: str, task_id: str) -> dict[str, object]:
    hostname = run_text(["hostname"])
    uptime = run_text(["uptime", "-p"])
    throttled = run_text(["vcgencmd", "get_throttled"])
    temp_raw = run_text(["vcgencmd", "measure_temp"])
    temperature_c = None
    if temp_raw.startswith("temp="):
        try:
            temperature_c = float(temp_raw.split("=", 1)[1].split("'")[0])
        except ValueError:
            temperature_c = None
    wifi_ssid = run_text(["iwgetid", "-r"])
    ip_address = run_text(["sh", "-lc", "ip -4 -o addr show wlan0 | awk '{print $4}' | head -1"])
    gateway = run_text(["sh", "-lc", "ip route | awk '/^default/ {print $3; exit}'"])
    disk = run_text(["sh", "-lc", "df -h / | awk 'NR==2 {print $5 \" used, \" $4 \" free\"}'"])
    sender_service = run_text(["systemctl", "is-active", "camera-snapshot-sender.service"])
    load_average = run_text(["sh", "-lc", "cut -d' ' -f1-3 /proc/loadavg"])
    return {
        "device_id": device_id,
        "task_id": task_id,
        "hostname": hostname,
        "uptime": uptime,
        "temperature_c": temperature_c,
        "throttled": throttled,
        "wifi_ssid": wifi_ssid,
        "ip_address": ip_address,
        "gateway": gateway,
        "disk": disk,
        "sender_service": sender_service,
        "load_average": load_average,
    }


def normalize_gpio(value: object, default: int = DEFAULT_QUERY_GPIO) -> int:
    try:
        gpio = int(value)
    except (TypeError, ValueError):
        return default
    if 0 <= gpio <= 53:
        return gpio
    return default


def read_gpio_status(gpio: int) -> dict[str, object]:
    sampled_at = time.time()
    try:
        result = subprocess.run(
            ["pinctrl", "get", str(gpio)],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except FileNotFoundError:
        return {
            "available": False,
            "gpio": gpio,
            "source": "pinctrl",
            "sampled_at": sampled_at,
            "error": "pinctrl not found",
        }
    except Exception as exc:
        return {"available": False, "gpio": gpio, "source": "pinctrl", "sampled_at": sampled_at, "error": str(exc)}

    raw = result.stdout.strip()
    parts = raw.split()
    level = ""
    for part in parts:
        if part in {"hi", "lo"}:
            level = part
            break
    return {
        "available": bool(level),
        "gpio": gpio,
        "source": "pinctrl",
        "level": level,
        "value": 1 if level == "hi" else 0 if level == "lo" else None,
        "sampled_at": sampled_at,
        "raw": raw,
    }


def upload_frame(
    session: requests.Session,
    server: str,
    token: str,
    device_id: str,
    frame_id: int,
    task_id: str,
    jpeg: bytes,
    gpio_status: dict[str, object],
    capture_source: str,
    capture_error: str = "",
) -> None:
    headers = {
        "Content-Type": "image/jpeg",
        "X-Device-ID": device_id,
        "X-Frame-ID": str(frame_id),
        "X-Task-ID": task_id,
        "X-Capture-Source": capture_source,
        "X-Capture-Error": capture_error[:300],
    }
    if gpio_status:
        headers["X-Gpio-Available"] = "1" if gpio_status.get("available") else "0"
        headers["X-Gpio-Number"] = str(gpio_status.get("gpio") or "")
        headers["X-Gpio-Level"] = str(gpio_status.get("level") or "")
        value = gpio_status.get("value")
        headers["X-Gpio-Value"] = "" if value is None else str(value)
        headers["X-Gpio-Source"] = str(gpio_status.get("source") or "")
        sampled_at = gpio_status.get("sampled_at")
        headers["X-Gpio-Sampled-At"] = "" if sampled_at is None else str(sampled_at)
        headers["X-Gpio-Raw"] = str(gpio_status.get("raw") or gpio_status.get("error") or "")[:300]
        if gpio_status.get("gpio") == 16:
            headers["X-Led1-Available"] = headers["X-Gpio-Available"]
            headers["X-Led1-Gpio"] = headers["X-Gpio-Number"]
            headers["X-Led1-Level"] = headers["X-Gpio-Level"]
            headers["X-Led1-Value"] = headers["X-Gpio-Value"]
            headers["X-Led1-Source"] = headers["X-Gpio-Source"]
            headers["X-Led1-Sampled-At"] = headers["X-Gpio-Sampled-At"]
            headers["X-Led1-Raw"] = headers["X-Gpio-Raw"]
    if token:
        headers["X-Camera-Token"] = token
    response = session.post(f"{server}/api/frame", headers=headers, data=jpeg, timeout=15)
    response.raise_for_status()


def upload_gpio_status(
    session: requests.Session,
    server: str,
    token: str,
    device_id: str,
    gpio_status: dict[str, object],
) -> None:
    payload = {**gpio_status, "device_id": device_id}
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Camera-Token"] = token
    response = session.post(f"{server}/api/gpio", headers=headers, json=payload, timeout=5)
    response.raise_for_status()


def upload_inspection(
    session: requests.Session,
    server: str,
    token: str,
    inspection: dict[str, object],
) -> None:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Camera-Token"] = token
    response = session.post(f"{server}/api/inspection", headers=headers, json=inspection, timeout=8)
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload Raspberry Pi camera snapshots to the cloud dashboard.")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Camera snapshot server base URL.")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Optional upload token matching .camera_token on server.")
    parser.add_argument("--device-id", default=os.environ.get("CAMERA_DEVICE_ID", "turbopi"))
    parser.add_argument("--backend", choices=["auto", "web-video-server", "rpicam-still", "picamera2", "opencv"], default="auto")
    parser.add_argument(
        "--web-video-snapshot-url",
        default=os.environ.get("CAMERA_WEB_VIDEO_SNAPSHOT_URL", "http://127.0.0.1:8080/snapshot?topic=/image_raw"),
        help="web_video_server snapshot URL for an already-running ROS camera stream.",
    )
    parser.add_argument("--web-video-timeout", type=float, default=2.0)
    parser.add_argument("--ros-container", default=os.environ.get("CAMERA_ROS_CONTAINER", "turbopi"))
    parser.add_argument("--ros-image-topic", default=os.environ.get("CAMERA_ROS_IMAGE_TOPIC", "/image_raw"))
    parser.add_argument(
        "--no-ros-usb-cam-autostart",
        dest="ros_usb_cam_autostart",
        action="store_false",
        help="Do not try to start TurboPi peripherals usb_cam.launch.py when /image_raw has no publisher.",
    )
    parser.set_defaults(ros_usb_cam_autostart=os.environ.get("CAMERA_ROS_USB_CAM_AUTOSTART", "1") != "0")
    parser.add_argument("--rpicam-timeout", type=float, default=10.0)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--quality", type=int, default=78)
    parser.add_argument("--idle-poll-ms", type=int, default=1000)
    parser.add_argument("--lock-file", default="/tmp/camera_snapshot_sender.lock")
    args = parser.parse_args()

    server = args.server.rstrip("/")
    acquire_single_instance_lock(args.lock_file)
    session = requests.Session()
    camera: CameraBackend | None = None
    print(f"[INFO] server: {server}", flush=True)

    frame_id = 0
    completed_tasks: set[str] = set()
    last_gpio_upload_at = 0.0
    try:
        while running:
            control = fetch_control(session, server)
            task = control.get("task")
            query_gpio = DEFAULT_QUERY_GPIO
            if isinstance(task, dict):
                query_gpio = normalize_gpio(task.get("query_gpio"))
            now = time.time()
            if now - last_gpio_upload_at >= 1.0:
                try:
                    upload_gpio_status(session, server, args.token, args.device_id, read_gpio_status(query_gpio))
                    last_gpio_upload_at = now
                except Exception as exc:
                    print(f"[WARN] gpio status upload failed: {exc}", flush=True)
            if not isinstance(task, dict) or not task.get("id"):
                time.sleep(max(args.idle_poll_ms, 250) / 1000)
                continue

            task_id = str(task["id"])
            if task_id in completed_tasks or task.get("status") in {"complete", "expired", "stopped"}:
                time.sleep(max(args.idle_poll_ms, 250) / 1000)
                continue

            max_frames = int(task.get("max_frames") or 1)
            interval_ms = int(task.get("interval_ms") or 0)
            deadline_at = float(task.get("deadline_at") or 0)
            mode = str(task.get("mode") or "single")
            uploaded = 0
            print(
                f"[INFO] running task {task_id} mode={mode} "
                f"max_frames={max_frames} query_gpio={query_gpio}",
                flush=True,
            )

            while running and (max_frames == 0 or uploaded < max_frames) and time.time() <= deadline_at:
                latest_control = fetch_control(session, server)
                latest_task = latest_control.get("task")
                if not isinstance(latest_task, dict) or latest_task.get("id") != task_id:
                    break
                if latest_task.get("status") in {"complete", "expired", "stopped"}:
                    break

                frame_id += 1
                try:
                    if mode in {"screenshot", "inspect"}:
                        jpeg = capture_screenshot_jpeg(args.quality, "Screen")
                        capture_source = mode
                        capture_error = ""
                    elif mode == "face":
                        jpeg = capture_screenshot_jpeg(args.quality, "Face")
                        capture_source = "face-screenshot"
                        capture_error = ""
                    elif mode == "single":
                        jpeg, camera, capture_source, capture_error = capture_camera_or_screenshot_fallback(camera, args)
                        if capture_source == "screenshot-fallback":
                            print(f"[INFO] task {task_id} used {capture_source}", flush=True)
                    else:
                        if camera is None:
                            camera = build_camera(args)
                            print(f"[INFO] camera backend: {camera.name}", flush=True)
                        jpeg = camera.capture_jpeg()
                        jpeg = stamp_jpeg(jpeg, "Camera", args.quality)
                        capture_source = camera.name
                        capture_error = ""
                    gpio_status = read_gpio_status(query_gpio)
                    if mode == "inspect":
                        upload_inspection(
                            session,
                            server,
                            args.token,
                            collect_inspection(args.device_id, task_id),
                        )
                    upload_frame(session, server, args.token, args.device_id, frame_id, task_id, jpeg, gpio_status, capture_source, capture_error)
                    uploaded += 1
                    total_label = "continuous" if max_frames == 0 else str(max_frames)
                    print(f"[ OK ] uploaded task {task_id} frame {uploaded}/{total_label}", flush=True)
                except Exception as exc:
                    print(f"[WARN] upload failed: {exc}", flush=True)
                    if camera is not None:
                        try:
                            camera.close()
                        except Exception as close_exc:
                            print(f"[WARN] camera close failed: {close_exc}", flush=True)
                        camera = None
                    try:
                        upload_gpio_status(session, server, args.token, args.device_id, read_gpio_status(query_gpio))
                        last_gpio_upload_at = time.time()
                    except Exception as heartbeat_exc:
                        print(f"[WARN] gpio status upload failed: {heartbeat_exc}", flush=True)
                    if mode in {"screenshot", "face", "inspect"}:
                        break
                    if max_frames != 0:
                        break
                    time.sleep(max(args.idle_poll_ms, 250) / 1000)
                if (max_frames == 0 or uploaded < max_frames) and interval_ms > 0:
                    time.sleep(max(interval_ms, 150) / 1000)

            completed_tasks.add(task_id)
            time.sleep(max(args.idle_poll_ms, 250) / 1000)
    finally:
        if camera is not None:
            camera.close()
        print("[INFO] stopped", flush=True)


if __name__ == "__main__":
    main()
