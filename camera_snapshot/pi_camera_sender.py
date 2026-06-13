from __future__ import annotations

import argparse
import io
import json
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
DEFAULT_FACE_RENDER_URL = os.environ.get("CAMERA_SNAPSHOT_FACE_RENDER_URL", "https://www.wangyutang.cn/face/api/face/render.jpg")
DEFAULT_SONAR_CONTAINER = os.environ.get("CAMERA_SONAR_CONTAINER", "turbopi")
DEFAULT_SONAR_UPLOAD_INTERVAL_SECONDS = float(os.environ.get("CAMERA_SONAR_UPLOAD_INTERVAL_SECONDS", "1.0"))
CAPTURE_KINDS = ("screen", "face", "camera")


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


def systemctl_is_active(service: str) -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False,
            timeout=3,
        ).returncode == 0
    except Exception:
        return False


def systemctl_action(action: str, service: str) -> bool:
    try:
        return subprocess.run(
            ["sudo", "-n", "systemctl", action, service],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        ).returncode == 0
    except Exception as exc:
        print(f"[WARN] systemctl {action} {service} failed: {exc}", flush=True)
        return False


def wait_service_state(service: str, active: bool, timeout_seconds: float = 5.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if systemctl_is_active(service) == active:
            return
        time.sleep(0.2)


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


def capture_camera_frame(camera: CameraBackend | None, args: argparse.Namespace) -> tuple[bytes, CameraBackend | None, str]:
    try:
        if camera is None:
            camera = build_camera(args)
            print(f"[INFO] camera backend: {camera.name}", flush=True)
        jpeg = camera.capture_jpeg()
        capture_source = camera.name
        if args.close_camera_after_frame:
            try:
                camera.close()
            except Exception as close_exc:
                print(f"[WARN] camera close failed: {close_exc}", flush=True)
            camera = None
        return stamp_jpeg(jpeg, "Camera", args.quality), camera, capture_source
    except Exception:
        if camera is not None:
            try:
                camera.close()
            except Exception as close_exc:
                print(f"[WARN] camera close failed: {close_exc}", flush=True)
        raise


def capture_camera_or_screenshot_fallback(
    camera: CameraBackend | None,
    args: argparse.Namespace,
) -> tuple[bytes, CameraBackend | None, str, str]:
    jpeg, camera, source = capture_camera_frame(camera, args)
    return jpeg, camera, source, ""


def capture_desktop_screenshot_jpeg(args: argparse.Namespace) -> bytes:
    service = str(getattr(args, "screen_hide_service", "") or "")
    was_active = False
    if service and getattr(args, "screen_hide_face_service", True):
        was_active = systemctl_is_active(service)
        if was_active:
            print(f"[INFO] stopping {service} before screen capture", flush=True)
            if systemctl_action("stop", service):
                wait_service_state(service, False, timeout_seconds=5.0)
            else:
                print(f"[WARN] could not stop {service}; screen capture may include foreground face window", flush=True)
    try:
        return capture_screenshot_jpeg(args.quality, "Screen")
    finally:
        if service and was_active:
            print(f"[INFO] restarting {service} after screen capture", flush=True)
            if systemctl_action("start", service):
                wait_service_state(service, True, timeout_seconds=8.0)
            else:
                print(f"[WARN] could not restart {service}; run sudo systemctl start {service}", flush=True)


def capture_face_render_jpeg(session: requests.Session, args: argparse.Namespace) -> bytes:
    response = session.get(args.face_render_url, timeout=args.face_render_timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    data = response.content
    if "image/jpeg" not in content_type and "image/jpg" not in content_type:
        raise RuntimeError(f"face render endpoint returned {content_type or 'unknown content type'}")
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError("face render endpoint did not return JPEG data")
    return stamp_jpeg(data, "Face", args.quality)


def fetch_control(session: requests.Session, server: str) -> dict[str, object]:
    try:
        response = session.get(f"{server}/api/control", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        print(f"[WARN] control poll failed: {exc}", flush=True)
        return {"task": None, "tasks": {}}


def control_tasks(control: dict[str, object]) -> dict[str, dict[str, object] | None]:
    raw_tasks = control.get("tasks")
    tasks: dict[str, dict[str, object] | None] = {kind: None for kind in CAPTURE_KINDS}
    if isinstance(raw_tasks, dict):
        for kind in ("camera", "screen", "face"):
            task = raw_tasks.get(kind)
            tasks[kind] = task if isinstance(task, dict) else None
    else:
        task = control.get("task")
        if isinstance(task, dict):
            kind = normalize_capture_kind(task.get("kind") or kind_from_mode(str(task.get("mode") or "single")))
            tasks[kind] = task
    return tasks


def kind_from_mode(mode: str) -> str:
    if mode == "screenshot" or mode == "inspect":
        return "screen"
    if mode == "face":
        return "face"
    return "camera"


def normalize_capture_kind(value: object) -> str:
    kind = str(value or "camera").strip().lower()
    if kind == "screenshot":
        return "screen"
    if kind in {"camera", "screen", "face"}:
        return kind
    return "camera"


def is_task_active(task: dict[str, object] | None) -> bool:
    return bool(task and task.get("id") and task.get("status") not in {"complete", "expired", "stopped", "failed"})


def run_text(command: list[str], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
        return (result.stdout or result.stderr).strip()
    except Exception as exc:
        return f"{exc.__class__.__name__}: {exc}"


def parse_cpu_usage(stat_before: str, stat_after: str) -> float | None:
    try:
        before = [int(value) for value in stat_before.split()[1:]]
        after = [int(value) for value in stat_after.split()[1:]]
    except (IndexError, ValueError):
        return None
    if len(before) < 5 or len(after) < 5:
        return None
    total_delta = sum(after) - sum(before)
    idle_delta = (after[3] + after[4]) - (before[3] + before[4])
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 1)


def collect_cpu_usage_percent() -> float | None:
    first = run_text(["sh", "-lc", "grep '^cpu ' /proc/stat"], timeout=1)
    time.sleep(0.2)
    second = run_text(["sh", "-lc", "grep '^cpu ' /proc/stat"], timeout=1)
    return parse_cpu_usage(first, second)


def collect_inspection(device_id: str, task_id: str) -> dict[str, object]:
    hostname = run_text(["hostname"])
    uptime = run_text(["uptime", "-p"])
    throttled = run_text(["sh", "-lc", "command -v vcgencmd >/dev/null && vcgencmd get_throttled || echo unavailable"])
    temp_raw = run_text(["sh", "-lc", "if command -v vcgencmd >/dev/null; then vcgencmd measure_temp; elif [ -r /sys/class/thermal/thermal_zone0/temp ]; then awk '{printf \"temp=%.1f\\047C\\n\", $1/1000}' /sys/class/thermal/thermal_zone0/temp; fi"])
    temperature_c = None
    if temp_raw.startswith("temp="):
        try:
            temperature_c = float(temp_raw.split("=", 1)[1].split("'")[0])
        except ValueError:
            temperature_c = None
    wifi_ssid = run_text(["sh", "-lc", "iwgetid -r 2>/dev/null || nmcli -t -f active,ssid dev wifi 2>/dev/null | awk -F: '$1==\"yes\" {print $2; exit}'"])
    ip_address = run_text(["sh", "-lc", "ip -4 -o addr show scope global | awk '{print $4}' | head -1"])
    gateway = run_text(["sh", "-lc", "ip route | awk '/^default/ {print $3; exit}'"])
    disk = run_text(["sh", "-lc", "df -h / | awk 'NR==2 {print $5 \" used, \" $4 \" free\"}'"])
    sender_service = run_text(["systemctl", "is-active", "camera-snapshot-sender.service"])
    load_average = run_text(["sh", "-lc", "cut -d' ' -f1-3 /proc/loadavg"])
    cpu_frequency_mhz = run_text(["sh", "-lc", "if command -v vcgencmd >/dev/null; then vcgencmd measure_clock arm | awk -F= '{printf \"%.0f\", $2/1000000}'; elif [ -r /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq ]; then awk '{printf \"%.0f\", $1/1000}' /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq; fi"])
    memory = run_text(["sh", "-lc", "free -h | awk '/^Mem:/ {print $3 \" used, \" $7 \" available\"}'"])
    return {
        "device_id": device_id,
        "task_id": task_id,
        "hostname": hostname,
        "uptime": uptime,
        "temperature_c": temperature_c,
        "cpu_usage_percent": collect_cpu_usage_percent(),
        "cpu_frequency_mhz": cpu_frequency_mhz,
        "memory": memory,
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
    capture_kind: str,
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
        "X-Capture-Kind": capture_kind,
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


def read_sonar_status(container: str) -> dict[str, object]:
    sampled_at = time.time()
    code = r"""
import json
import sys
import time

sys.path.insert(0, "/home/ubuntu/ros2_ws/src/driver/sdk")
from sdk.sonar import Sonar

sonar = Sonar()
values = []
for _ in range(7):
    value = int(sonar.getDistance())
    if 0 < value <= 5000:
        values.append(value)
    time.sleep(0.04)

if values:
    raw_mm = min(values)
    payload = {
        "available": True,
        "front_distance_estimate_cm": raw_mm / 10.0,
        "confidence": min(1.0, len(values) / 5.0),
        "source": "turbopi-sonar-sdk",
        "raw": ",".join(str(value) for value in values),
    }
else:
    payload = {
        "available": False,
        "front_distance_estimate_cm": None,
        "confidence": 0.0,
        "source": "turbopi-sonar-sdk",
        "raw": "",
    }
print(json.dumps(payload), flush=True)
"""
    try:
        result = subprocess.run(
            ["docker", "exec", "-u", "ubuntu", container, "python3", "-c", code],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        data = json.loads(result.stdout.strip().splitlines()[-1])
        if isinstance(data, dict):
            data["sampled_at"] = sampled_at
            return data
    except Exception as exc:
        return {
            "available": False,
            "front_distance_estimate_cm": None,
            "confidence": 0.0,
            "source": "turbopi-sonar-sdk",
            "sampled_at": sampled_at,
            "error": str(exc),
        }
    return {
        "available": False,
        "front_distance_estimate_cm": None,
        "confidence": 0.0,
        "source": "turbopi-sonar-sdk",
        "sampled_at": sampled_at,
        "error": "invalid sonar payload",
    }


def upload_sonar_status(
    session: requests.Session,
    server: str,
    token: str,
    device_id: str,
    sonar_status: dict[str, object],
) -> None:
    payload = {**sonar_status, "device_id": device_id}
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Camera-Token"] = token
    response = session.post(f"{server}/api/sonar", headers=headers, json=payload, timeout=5)
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


def upload_task_status(
    session: requests.Session,
    server: str,
    token: str,
    device_id: str,
    task_id: str,
    kind: str,
    status: str,
    capture_source: str,
    capture_error: str,
) -> None:
    payload = {
        "device_id": device_id,
        "task_id": task_id,
        "kind": kind,
        "status": status,
        "capture_source": capture_source,
        "capture_error": capture_error[:300],
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Camera-Token"] = token
    response = session.post(f"{server}/api/task-status", headers=headers, json=payload, timeout=8)
    response.raise_for_status()


def should_send_task_frame(task: dict[str, object], sent_at_by_task: dict[str, float]) -> bool:
    task_id = str(task.get("id") or "")
    interval_ms = int(task.get("interval_ms") or 0)
    if interval_ms <= 0:
        return True
    return time.time() - sent_at_by_task.get(task_id, 0) >= interval_ms / 1000


def handle_task(
    kind: str,
    task: dict[str, object],
    args: argparse.Namespace,
    session: requests.Session,
    server: str,
    frame_id: int,
    camera: CameraBackend | None,
) -> tuple[int, CameraBackend | None, bool]:
    task_id = str(task["id"])
    query_gpio = normalize_gpio(task.get("query_gpio"))
    mode = str(task.get("mode") or "single")
    print(f"[INFO] running task {task_id} kind={kind} mode={mode} query_gpio={query_gpio}", flush=True)

    inspection_payload: dict[str, object] | None = None
    if kind == "screen" and mode == "inspect":
        try:
            inspection_payload = collect_inspection(args.device_id, task_id)
        except Exception as exc:
            print(f"[WARN] collect inspection for {task_id} failed: {exc}", flush=True)

    try:
        if kind == "screen":
            jpeg = capture_desktop_screenshot_jpeg(args)
            capture_source = "inspect" if mode == "inspect" else "screenshot"
        elif kind == "face":
            jpeg = capture_face_render_jpeg(session, args)
            capture_source = "smile-face-render"
        else:
            jpeg, camera, capture_source = capture_camera_frame(camera, args)

        gpio_meta = read_gpio_status(query_gpio)
        frame_id += 1
        upload_frame(session, server, args.token, args.device_id, frame_id, task_id, kind, jpeg, gpio_meta, capture_source)
        print(f"[ OK ] uploaded task {task_id} kind={kind}", flush=True)
        return frame_id, camera, True
    finally:
        if inspection_payload is not None:
            try:
                upload_inspection(session, server, args.token, inspection_payload)
            except Exception as exc:
                print(f"[WARN] upload inspection for {task_id} failed: {exc}", flush=True)


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
    parser.add_argument("--sonar-container", default=DEFAULT_SONAR_CONTAINER)
    parser.add_argument("--sonar-upload-interval-seconds", type=float, default=DEFAULT_SONAR_UPLOAD_INTERVAL_SECONDS)
    parser.add_argument("--ros-image-topic", default=os.environ.get("CAMERA_ROS_IMAGE_TOPIC", "/image_raw"))
    parser.add_argument(
        "--ros-usb-cam-autostart",
        dest="ros_usb_cam_autostart",
        action="store_true",
        help="Try to start TurboPi peripherals usb_cam.launch.py when /image_raw has no publisher.",
    )
    parser.add_argument(
        "--no-ros-usb-cam-autostart",
        dest="ros_usb_cam_autostart",
        action="store_false",
        help="Do not try to start TurboPi peripherals usb_cam.launch.py when /image_raw has no publisher.",
    )
    parser.set_defaults(ros_usb_cam_autostart=os.environ.get("CAMERA_ROS_USB_CAM_AUTOSTART", "0") == "1")
    parser.add_argument("--rpicam-timeout", type=float, default=10.0)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--quality", type=int, default=78)
    parser.add_argument("--face-render-url", default=DEFAULT_FACE_RENDER_URL)
    parser.add_argument("--face-render-timeout", type=float, default=5.0)
    parser.add_argument("--screen-hide-service", default=os.environ.get("CAMERA_SNAPSHOT_SCREEN_HIDE_SERVICE", "smile-face-lcd.service"))
    parser.add_argument("--no-screen-hide-face-service", dest="screen_hide_face_service", action="store_false")
    parser.set_defaults(screen_hide_face_service=os.environ.get("CAMERA_SNAPSHOT_SCREEN_HIDE_FACE_SERVICE", "1") != "0")
    parser.add_argument("--idle-poll-ms", type=int, default=1000)
    parser.add_argument("--lock-file", default="/tmp/camera_snapshot_sender.lock")
    parser.add_argument("--keep-camera-open", dest="close_camera_after_frame", action="store_false")
    parser.set_defaults(close_camera_after_frame=True)
    args = parser.parse_args()

    server = args.server.rstrip("/")
    acquire_single_instance_lock(args.lock_file)
    session = requests.Session()
    camera: CameraBackend | None = None
    print(f"[INFO] server: {server}", flush=True)

    frame_id = 0
    completed_tasks: set[str] = set()
    failed_one_shots: set[str] = set()
    sent_at_by_task: dict[str, float] = {}
    last_gpio_upload_at = 0.0
    last_sonar_upload_at = 0.0
    try:
        while running:
            control = fetch_control(session, server)
            tasks = control_tasks(control)
            active_tasks = [task for task in tasks.values() if is_task_active(task)]
            query_gpio = normalize_gpio(active_tasks[0].get("query_gpio")) if active_tasks else DEFAULT_QUERY_GPIO
            now = time.time()
            if now - last_gpio_upload_at >= 1.0:
                try:
                    upload_gpio_status(session, server, args.token, args.device_id, read_gpio_status(query_gpio))
                    last_gpio_upload_at = now
                except Exception as exc:
                    print(f"[WARN] gpio status upload failed: {exc}", flush=True)
            if args.sonar_upload_interval_seconds > 0 and now - last_sonar_upload_at >= args.sonar_upload_interval_seconds:
                try:
                    upload_sonar_status(
                        session,
                        server,
                        args.token,
                        args.device_id,
                        read_sonar_status(args.sonar_container),
                    )
                    last_sonar_upload_at = now
                except Exception as exc:
                    print(f"[WARN] sonar status upload failed: {exc}", flush=True)

            did_work = False
            for kind in CAPTURE_KINDS:
                task = tasks.get(kind)
                if not is_task_active(task):
                    continue
                task_id = str(task["id"])
                if task_id in completed_tasks or task_id in failed_one_shots:
                    continue
                if time.time() > float(task.get("deadline_at") or 0):
                    continue
                if not should_send_task_frame(task, sent_at_by_task):
                    continue

                try:
                    frame_id, camera, uploaded = handle_task(kind, task, args, session, server, frame_id, camera)
                    if uploaded:
                        sent_at_by_task[task_id] = time.time()
                        did_work = True
                        if int(task.get("max_frames") or 1) > 0:
                            completed_tasks.add(task_id)
                except Exception as exc:
                    print(f"[WARN] {kind} task {task_id} failed: {exc}", flush=True)
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
                    if int(task.get("max_frames") or 1) > 0:
                        try:
                            upload_task_status(
                                session,
                                server,
                                args.token,
                                args.device_id,
                                task_id,
                                kind,
                                "failed",
                                f"{kind}-error",
                                str(exc),
                            )
                        except Exception as status_exc:
                            print(f"[WARN] task status upload failed: {status_exc}", flush=True)
                        failed_one_shots.add(task_id)

            time.sleep(0.05 if did_work else max(args.idle_poll_ms, 250) / 1000)
    finally:
        if camera is not None:
            camera.close()
        print("[INFO] stopped", flush=True)


if __name__ == "__main__":
    main()
