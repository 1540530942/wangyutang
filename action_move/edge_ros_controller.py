from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import math
import rclpy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu
from rclpy.node import Node
from ros_robot_controller_msgs.msg import PWMServoState, RGBState, RGBStates, SetPWMServoState

try:
    from sdk.sonar import Sonar
except Exception:
    Sonar = None  # type: ignore[assignment]


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG = BASE_DIR / "skill_catalog.json"


def load_catalog(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def flatten_skills(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for skill in catalog.get("skills", []):
        keys = [skill["id"], skill["name_zh"], *skill.get("aliases", [])]
        for key in keys:
            result[str(key).strip().lower()] = skill
    return result


def resolve_skill(catalog: dict[str, Any], text: str) -> dict[str, Any]:
    key = text.strip().lower()
    skills = flatten_skills(catalog)
    if key in skills:
        return skills[key]
    for alias, skill in skills.items():
        if alias and alias in key:
            return skill
    raise KeyError(f"unknown action: {text}")


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def merged_defaults(catalog: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = dict(catalog.get("defaults", {}))
    for key in ("unit_distance_cm", "turn_angle_deg", "sensitivity", "rgb_red", "rgb_green", "rgb_blue"):
        if params and key in params:
            defaults[key] = params[key]
    defaults["unit_distance_cm"] = clamp(float(defaults.get("unit_distance_cm", 5.0)), 1.0, 50.0)
    defaults["turn_angle_deg"] = clamp(float(defaults.get("turn_angle_deg", 5.0)), 1.0, 90.0)
    defaults["sensitivity"] = clamp(float(defaults.get("sensitivity", 1.0)), 0.2, 2.0)
    defaults["rgb_red"] = int(round(clamp(float(defaults.get("rgb_red", 0)), 0.0, 255.0)))
    defaults["rgb_green"] = int(round(clamp(float(defaults.get("rgb_green", 0)), 0.0, 255.0)))
    defaults["rgb_blue"] = int(round(clamp(float(defaults.get("rgb_blue", 0)), 0.0, 255.0)))
    return defaults


def unit_duration_ms(defaults: dict[str, Any], kind: str) -> int:
    sensitivity = max(float(defaults.get("sensitivity", 1.0)), 0.2)
    if kind == "turn":
        unit = float(defaults.get("turn_angle_deg", 5.0))
        base = float(defaults.get("turn_duration_ms_at_5deg", 450))
        lower = float(defaults.get("min_turn_duration_ms", 180))
        upper = float(defaults.get("max_turn_duration_ms", 2500))
    else:
        unit = float(defaults.get("unit_distance_cm", 5.0))
        base = float(defaults.get("move_duration_ms_at_5cm", 800))
        lower = float(defaults.get("min_move_duration_ms", 180))
        upper = float(defaults.get("max_move_duration_ms", 3000))
    return int(round(clamp(base * (unit / 5.0) / sensitivity, lower, upper)))


def cmd_vel_topics(defaults: dict[str, Any]) -> list[str]:
    topics = defaults.get("cmd_vel_topics")
    if not isinstance(topics, list) or not topics:
        topics = [defaults.get("cmd_vel_topic", "/cmd_vel")]
    result: list[str] = []
    for topic in topics:
        text = str(topic or "").strip()
        if text and text not in result:
            result.append(text)
    return result or ["/cmd_vel"]


class TurboPiController(Node):
    def __init__(self, catalog_path: Path) -> None:
        super().__init__("action_move_edge_controller")
        self.catalog_path = catalog_path
        self.lock = threading.Lock()
        self.publish_lock = threading.Lock()
        catalog = load_catalog(catalog_path)
        defaults = catalog.get("defaults", {})
        self.cmd_vel_topics = cmd_vel_topics(defaults)
        self.pwm_servo_topic = str(defaults.get("pwm_servo_topic", "/ros_robot_controller/pwm_servo/set_state"))
        self.rgb_topic = str(defaults.get("rgb_topic", "/ros_robot_controller/set_rgb"))
        self.sonar_rgb_enabled = bool(defaults.get("sonar_rgb_enabled", True))
        self.sonar_rgb_indices = defaults.get("sonar_rgb_indices", [0, 1])
        self.cmd_vel_pubs = {
            topic: self.create_publisher(Twist, topic, 10)
            for topic in self.cmd_vel_topics
        }
        self.servo_pub = self.create_publisher(SetPWMServoState, self.pwm_servo_topic, 10)
        self.rgb_pub = self.create_publisher(RGBStates, self.rgb_topic, 10)
        self.sonar_rgb = None
        self.sonar_distance = None
        self.stop_event = threading.Event()
        self.last_action = ""
        self.last_executed_at = 0.0
        center = int(defaults.get("pwm_center", 1500))
        self.servo_positions: dict[int, int] = {1: center, 2: center}
        self._imu_lock = threading.Lock()
        self._imu_samples: list[tuple[float, float, float, float, float]] = []
        self.create_subscription(Imu, "/ros_robot_controller/imu_raw", self._imu_callback, 20)


    def _imu_callback(self, msg: Imu) -> None:
        t = time.monotonic()
        with self._imu_lock:
            self._imu_samples.append((
                t,
                msg.linear_acceleration.x,
                msg.linear_acceleration.y,
                msg.linear_acceleration.z,
                msg.angular_velocity.z,
            ))

    def _measure_imu_actuals(
        self, samples: list, skill_type: str, twist: dict[str, Any]
    ) -> dict[str, float]:
        if len(samples) < 4:
            return {}
        samples = sorted(samples, key=lambda s: s[0])
        # Integrate gyro z for yaw (reliable for short bursts)
        dyaw_rad = 0.0
        for i in range(1, len(samples)):
            dt = samples[i][0] - samples[i - 1][0]
            if 0.0 < dt < 0.2:
                dyaw_rad += samples[i][4] * dt
        result: dict[str, float] = {}
        if skill_type == "base_turn":
            result["actual_yaw_deg"] = round(abs(math.degrees(dyaw_rad)), 2)
        elif skill_type == "base_move":
            # Double-integrate the dominant translation axis (noisy but directional)
            lx = float(twist.get("linear_x", 0.0))
            ly = float(twist.get("linear_y", 0.0))
            axis_idx = 1 if abs(lx) >= abs(ly) else 2  # 1=ax, 2=ay
            # Remove mean (static bias / gravity projection)
            axis_vals = [s[axis_idx] for s in samples]
            bias = sum(axis_vals) / len(axis_vals)
            vel = 0.0
            pos = 0.0
            for i in range(1, len(samples)):
                dt = samples[i][0] - samples[i - 1][0]
                if 0.0 < dt < 0.2:
                    a = samples[i][axis_idx] - bias
                    vel += a * dt
                    pos += vel * dt
            dist_cm = abs(pos) * 100.0
            # Only report if result is physically plausible (1–60 cm)
            if 1.0 <= dist_cm <= 60.0:
                result["actual_distance_cm"] = round(dist_cm, 2)
        return result

    def execute(self, action: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.time()
        catalog = load_catalog(self.catalog_path)
        skill = resolve_skill(catalog, action)
        defaults = merged_defaults(catalog, settings)
        output: list[str] = [
            f"[INFO] {skill['name_zh']} -> {skill['id']}",
            "[INFO] transport=persistent_ros_controller",
            "[INFO] unit_distance_cm={unit_distance_cm} turn_angle_deg={turn_angle_deg} sensitivity={sensitivity}".format(
                **defaults
            ),
        ]
        if skill["type"] == "base_stop":
            self.stop_event.set()
            self.publish_stop(int(defaults.get("stop_publish_times", 3)))
            self.last_action = skill["id"]
            self.last_executed_at = time.time()
            elapsed = round(time.time() - started, 3)
            output.append(f"[INFO] elapsed_seconds={elapsed}")
            return {
                "ok": True,
                "skill_id": skill["id"],
                "name_zh": skill["name_zh"],
                "elapsed_seconds": elapsed,
                "output": "\n".join(output),
            }
        if skill["type"] == "reset_pose":
            self.stop_event.set()
            self.publish_stop(int(defaults.get("stop_publish_times", 3)))
        with self.lock:
            try:
                if skill["type"] == "reset_pose":
                    self.publish_servo_reset(defaults)
                    self.request_camera_capture(defaults)
                elif skill["type"] == "camera_servo":
                    output.append(self.publish_servo(skill, defaults))
                    self.request_camera_capture(defaults)
                elif skill["type"] == "rgb_light":
                    self.publish_rgb(skill, defaults)
                elif skill["type"] == "front_distance":
                    distance = self.read_front_distance(defaults)
                    output.extend(
                        [
                            "[INFO] front_distance_estimate_cm={front_distance_estimate_cm}".format(**distance),
                            "[INFO] raw_mm_samples={raw_mm_samples}".format(**distance),
                            "[INFO] confidence={confidence}".format(**distance),
                        ]
                    )
                elif skill["type"] in {"base_move", "base_turn"}:
                    self.stop_event.clear()
                    kind = "turn" if skill["type"] == "base_turn" else "move"
                    duration_ms = unit_duration_ms(defaults, kind)
                    output.append(f"[INFO] duration_ms={duration_ms}")
                    with self._imu_lock:
                        self._imu_samples.clear()
                    output.extend(
                        self.publish_twist_burst(
                            skill["twist"],
                            duration_ms,
                            int(defaults.get("stop_publish_times", 3)),
                            defaults,
                        )
                    )
                    with self._imu_lock:
                        imu_snap = list(self._imu_samples)
                    actuals = self._measure_imu_actuals(imu_snap, skill["type"], skill.get("twist", {}))
                    if "actual_distance_cm" in actuals:
                        output.append(f"[IMU] actual_distance_cm={actuals['actual_distance_cm']}")
                    if "actual_yaw_deg" in actuals:
                        output.append(f"[IMU] actual_yaw_deg={actuals['actual_yaw_deg']}")
                    # IMU unavailable — echo the commanded value as the best estimate
                    if not actuals:
                        if skill["type"] == "base_turn":
                            commanded_deg = float(defaults.get("turn_angle_deg", 0.0))
                            if commanded_deg > 0.0:
                                output.append(f"[CMD] actual_yaw_deg={round(commanded_deg, 2)}")
                        elif skill["type"] == "base_move":
                            commanded_cm = float(defaults.get("unit_distance_cm", 0.0))
                            if commanded_cm > 0.0:
                                output.append(f"[CMD] actual_distance_cm={round(commanded_cm, 2)}")
                else:
                    raise ValueError(f"unsupported skill type: {skill['type']}")
                self.last_action = skill["id"]
                self.last_executed_at = time.time()
                elapsed = round(time.time() - started, 3)
                output.append(f"[INFO] elapsed_seconds={elapsed}")
                return {
                    "ok": True,
                    "skill_id": skill["id"],
                    "name_zh": skill["name_zh"],
                    "elapsed_seconds": elapsed,
                    "output": "\n".join(output),
                }
            except Exception:
                if skill["type"] in {"base_move", "base_turn"}:
                    self.publish_stop(5)
                raise

    def make_twist(self, twist: dict[str, Any]) -> Twist:
        message = Twist()
        message.linear.x = float(twist.get("linear_x", 0.0))
        message.linear.y = float(twist.get("linear_y", 0.0))
        message.linear.z = 0.0
        message.angular.x = 0.0
        message.angular.y = 0.0
        message.angular.z = float(twist.get("angular_z", 0.0))
        return message

    def scale_twist(self, twist: Twist, scale: float) -> Twist:
        message = Twist()
        message.linear.x = twist.linear.x * scale
        message.linear.y = twist.linear.y * scale
        message.linear.z = 0.0
        message.angular.x = 0.0
        message.angular.y = 0.0
        message.angular.z = twist.angular.z * scale
        return message

    def publish_twist_burst(
        self,
        twist: dict[str, Any],
        duration_ms: int,
        stop_times: int,
        defaults: dict[str, Any],
    ) -> list[str]:
        message = self.make_twist(twist)
        rate_hz = 20.0
        interval = 1.0 / rate_hz
        started = time.monotonic()
        deadline = started + max(duration_ms, 0) / 1000.0
        ramp_seconds = max(float(defaults.get("move_ramp_ms", 0)), 0.0) / 1000.0
        start_scale = clamp(float(defaults.get("move_start_scale", 1.0)), 0.1, 1.0)
        topic_counts = self.cmd_vel_subscription_counts()
        while time.monotonic() < deadline and not self.stop_event.is_set():
            elapsed = time.monotonic() - started
            if ramp_seconds > 0 and elapsed < ramp_seconds:
                scale = start_scale + (1.0 - start_scale) * (elapsed / ramp_seconds)
                publish_message = self.scale_twist(message, scale)
            else:
                publish_message = message
            with self.publish_lock:
                for publisher in self.cmd_vel_pubs.values():
                    publisher.publish(publish_message)
                rclpy.spin_once(self, timeout_sec=0.0)
            time.sleep(interval)
        self.publish_stop(stop_times)
        return [
            "[INFO] cmd_vel_topics=" + ",".join(self.cmd_vel_pubs.keys()),
            "[INFO] cmd_vel_subscription_counts="
            + ",".join(f"{topic}:{count}" for topic, count in topic_counts.items()),
            f"[INFO] move_ramp_ms={int(ramp_seconds * 1000)} move_start_scale={start_scale}",
        ]

    def publish_stop(self, times: int = 3) -> None:
        stop = Twist()
        for _ in range(max(times, 1)):
            with self.publish_lock:
                for publisher in self.cmd_vel_pubs.values():
                    publisher.publish(stop)
                rclpy.spin_once(self, timeout_sec=0.0)
            time.sleep(0.03)

    def cmd_vel_subscription_counts(self) -> dict[str, int]:
        return {
            topic: publisher.get_subscription_count()
            for topic, publisher in self.cmd_vel_pubs.items()
        }

    def resolve_servo_position(self, servo: dict[str, Any], defaults: dict[str, Any]) -> tuple[int, int, int | None]:
        servo_id = int(servo["id"])
        fallback = int(servo.get("default_position", defaults.get("pwm_center", 1500)))
        current = int(self.servo_positions.get(servo_id, fallback))
        minimum = int(servo.get("min", defaults.get("camera_servo_min", 1000)))
        maximum = int(servo.get("max", defaults.get("camera_servo_max", 2000)))
        if "delta" in servo:
            delta = int(servo.get("delta", 0))
            return servo_id, int(round(clamp(current + delta, minimum, maximum))), delta
        return servo_id, int(round(clamp(float(servo["position"]), minimum, maximum))), None

    def publish_servo(self, skill: dict[str, Any], defaults: dict[str, Any]) -> str:
        servo = skill["servo"]
        servo_id, position, delta = self.resolve_servo_position(servo, defaults)
        state = PWMServoState()
        state.id = [servo_id]
        state.position = [position]
        state.offset = []
        message = SetPWMServoState()
        message.duration = float(defaults.get("servo_duration_s", 0.35))
        message.state = [state]
        self.servo_pub.publish(message)
        rclpy.spin_once(self, timeout_sec=0.0)
        time.sleep(message.duration)
        self.servo_positions[servo_id] = position
        delta_text = "absolute" if delta is None else f"delta={delta}"
        return f"[INFO] servo_id={servo_id} position={position} {delta_text}"

    def publish_servo_reset(self, defaults: dict[str, Any]) -> None:
        center = int(defaults.get("pwm_center", 1500))
        message = SetPWMServoState()
        message.duration = float(defaults.get("servo_duration_s", 0.35))
        message.state = []
        for servo_id in (1, 2):
            state = PWMServoState()
            state.id = [servo_id]
            state.position = [center]
            state.offset = []
            message.state.append(state)
            self.servo_positions[servo_id] = center
        self.servo_pub.publish(message)
        rclpy.spin_once(self, timeout_sec=0.0)
        time.sleep(message.duration)

    def publish_rgb(self, skill: dict[str, Any], defaults: dict[str, Any]) -> None:
        mode = str(skill.get("rgb", {}).get("mode", "settings"))
        if mode == "off":
            red = green = blue = 0
        else:
            red = int(defaults.get("rgb_red", 0))
            green = int(defaults.get("rgb_green", 0))
            blue = int(defaults.get("rgb_blue", 0))
        indices = defaults.get("rgb_led_indices", [1, 2])
        message = RGBStates()
        message.states = []
        for index in indices if isinstance(indices, list) else [1, 2]:
            state = RGBState()
            state.index = int(index)
            state.red = red
            state.green = green
            state.blue = blue
            message.states.append(state)
        self.rgb_pub.publish(message)
        rclpy.spin_once(self, timeout_sec=0.0)
        self.apply_sonar_rgb(red, green, blue)

    def apply_sonar_rgb(self, red: int, green: int, blue: int) -> None:
        if not self.sonar_rgb_enabled:
            return
        if Sonar is None:
            raise RuntimeError("sonar RGB SDK is unavailable")
        if self.sonar_rgb is None:
            self.sonar_rgb = Sonar()
        indices = self.sonar_rgb_indices if isinstance(self.sonar_rgb_indices, list) else [0, 1]
        self.sonar_rgb.setRGBMode(0)
        for index in indices:
            self.sonar_rgb.setPixelColor(int(index), (red, green, blue))

    def read_front_distance(self, defaults: dict[str, Any]) -> dict[str, Any]:
        if Sonar is None:
            raise RuntimeError("sonar distance SDK is unavailable")
        if self.sonar_distance is None:
            self.sonar_distance = Sonar()
        samples = int(defaults.get("sonar_distance_samples", 7))
        interval_ms = int(defaults.get("sonar_distance_sample_interval_ms", 40))
        values: list[int] = []
        for _ in range(max(samples, 1)):
            value = int(self.sonar_distance.getDistance())
            if 0 < value <= 5000:
                values.append(value)
            time.sleep(max(interval_ms, 0) / 1000.0)
        if not values:
            raise RuntimeError("sonar distance returned no valid samples")
        raw_mm = min(values) if bool(defaults.get("sonar_distance_conservative", True)) else int(round(statistics.median(values)))
        return {
            "front_distance_estimate_cm": round(raw_mm / 10.0, 2),
            "raw_mm": raw_mm,
            "raw_mm_samples": ",".join(str(value) for value in values),
            "confidence": round(min(1.0, len(values) / max(samples, 1)), 3),
        }

    def request_camera_capture(self, defaults: dict[str, Any]) -> None:
        if not bool(defaults.get("capture_after_servo", True)):
            return
        settle_ms = int(defaults.get("capture_settle_ms", 500))
        server = str(defaults.get("camera_server", "")).rstrip("/")
        if not server:
            return

        def worker() -> None:
            try:
                time.sleep(max(settle_ms, 0) / 1000.0)
                body = json.dumps({"mode": "single"}).encode("utf-8")
                request = urllib.request.Request(
                    f"{server}/api/capture",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(request, timeout=3).read()
            except Exception:
                return

        threading.Thread(target=worker, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    controller: TurboPiController

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_json({"error": "not_found"}, status=404)
            return
        self.send_json(
            {
                "status": "ok",
                "service": "TurboPi Action Move Edge ROS Controller",
                "last_action": self.controller.last_action,
                "last_executed_at": self.controller.last_executed_at,
            }
        )

    def do_POST(self) -> None:
        if self.path != "/execute":
            self.send_json({"error": "not_found"}, status=404)
            return
        try:
            length = int(self.headers.get("content-length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            action = str(payload.get("action") or "").strip()
            settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
            if not action:
                self.send_json({"ok": False, "error": "missing action"}, status=400)
                return
            self.send_json(self.controller.execute(action, settings))
        except KeyError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent ROS2 controller for TurboPi action_move.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()

    rclpy.init()
    Handler.controller = TurboPiController(args.catalog)
    spin_thread = threading.Thread(
        target=lambda: rclpy.spin(Handler.controller),
        daemon=True,
        name="rclpy_spin",
    )
    spin_thread.start()
    server = ReusableThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        Handler.controller.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
