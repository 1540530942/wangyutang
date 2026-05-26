from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG = BASE_DIR / "skill_catalog.json"
ROS_SETUP = "source /opt/ros/humble/setup.bash && source /home/ubuntu/ros2_ws/install/setup.bash"


def load_catalog(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def run_in_container(container: str, command: str, dry_run: bool) -> None:
    docker_user = "ubuntu"
    docker_command = ["docker", "exec", "-u", docker_user, container, "bash", "-lc", command]
    if dry_run:
        print(" ".join(docker_command))
        return
    subprocess.run(docker_command, check=True, timeout=12)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def merged_defaults(catalog: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = dict(catalog.get("defaults", {}))
    for key in ("unit_distance_cm", "turn_angle_deg", "sensitivity"):
        if params and key in params:
            defaults[key] = params[key]
    defaults["unit_distance_cm"] = clamp(float(defaults.get("unit_distance_cm", 5.0)), 1.0, 50.0)
    defaults["turn_angle_deg"] = clamp(float(defaults.get("turn_angle_deg", 5.0)), 1.0, 90.0)
    defaults["sensitivity"] = clamp(float(defaults.get("sensitivity", 1.0)), 0.2, 2.0)
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


def publish_stop(defaults: dict[str, Any], dry_run: bool) -> None:
    topic = str(defaults.get("cmd_vel_topic", "/cmd_vel"))
    times = int(defaults.get("stop_publish_times", 3))
    stop_msg = "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
    command = (
        f"{ROS_SETUP} && "
        f"ros2 topic pub --times {max(times, 1)} --rate 10 --wait-matching-subscriptions 0 "
        f"{topic} geometry_msgs/msg/Twist '{stop_msg}'"
    )
    run_in_container(str(defaults.get("ros_container", "turbopi")), command, dry_run)


def execute_base_stop(defaults: dict[str, Any], dry_run: bool) -> None:
    publish_stop(defaults, dry_run)


def execute_reset_pose(defaults: dict[str, Any], dry_run: bool) -> None:
    publish_stop(defaults, dry_run)
    center = int(defaults.get("pwm_center", 1500))
    duration = float(defaults.get("servo_duration_s", 0.35))
    topic = str(defaults.get("pwm_servo_topic", "/ros_robot_controller/pwm_servo/set_state"))
    message = (
        "{"
        f"duration: {duration}, "
        f"state: [{{id: [1], position: [{center}], offset: []}}, "
        f"{{id: [2], position: [{center}], offset: []}}]"
        "}"
    )
    command = f"{ROS_SETUP} && ros2 topic pub --once --wait-matching-subscriptions 0 {topic} ros_robot_controller_msgs/msg/SetPWMServoState '{message}'"
    run_in_container(str(defaults.get("ros_container", "turbopi")), command, dry_run)


def execute_remote_shutdown(dry_run: bool) -> None:
    command = ["sudo", "shutdown", "-h", "now"]
    if dry_run:
        print(" ".join(command))
        return
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def execute_camera_servo(skill: dict[str, Any], defaults: dict[str, Any], dry_run: bool) -> None:
    servo = skill["servo"]
    duration = float(defaults.get("servo_duration_s", 0.35))
    topic = str(defaults.get("pwm_servo_topic", "/ros_robot_controller/pwm_servo/set_state"))
    message = (
        "{"
        f"duration: {duration}, "
        f"state: [{{id: [{int(servo['id'])}], position: [{int(servo['position'])}], offset: []}}]"
        "}"
    )
    command = f"{ROS_SETUP} && ros2 topic pub --once --wait-matching-subscriptions 0 {topic} ros_robot_controller_msgs/msg/SetPWMServoState '{message}'"
    run_in_container(str(defaults.get("ros_container", "turbopi")), command, dry_run)


def execute_base_move(skill: dict[str, Any], defaults: dict[str, Any], dry_run: bool) -> None:
    twist = skill["twist"]
    duration_ms = unit_duration_ms(defaults, "move")
    rate = 10
    times = max(1, round(duration_ms / 1000 * rate))
    topic = str(defaults.get("cmd_vel_topic", "/cmd_vel"))
    move_msg = (
        "{"
        f"linear: {{x: {float(twist['linear_x'])}, y: {float(twist['linear_y'])}, z: 0.0}}, "
        f"angular: {{x: 0.0, y: 0.0, z: {float(twist['angular_z'])}}}"
        "}"
    )
    stop_msg = "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
    command = (
        f"{ROS_SETUP} && "
        f"ros2 topic pub --times {times} --rate {rate} --wait-matching-subscriptions 0 {topic} geometry_msgs/msg/Twist '{move_msg}' && "
        f"ros2 topic pub --once --wait-matching-subscriptions 0 {topic} geometry_msgs/msg/Twist '{stop_msg}'"
    )
    run_in_container(str(defaults.get("ros_container", "turbopi")), command, dry_run)


def execute_base_turn(skill: dict[str, Any], defaults: dict[str, Any], dry_run: bool) -> None:
    twist = skill["twist"]
    duration_ms = unit_duration_ms(defaults, "turn")
    rate = 10
    times = max(1, round(duration_ms / 1000 * rate))
    topic = str(defaults.get("cmd_vel_topic", "/cmd_vel"))
    move_msg = (
        "{"
        f"linear: {{x: {float(twist['linear_x'])}, y: {float(twist['linear_y'])}, z: 0.0}}, "
        f"angular: {{x: 0.0, y: 0.0, z: {float(twist['angular_z'])}}}"
        "}"
    )
    stop_msg = "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
    command = (
        f"{ROS_SETUP} && "
        f"ros2 topic pub --times {times} --rate {rate} --wait-matching-subscriptions 0 {topic} geometry_msgs/msg/Twist '{move_msg}' && "
        f"ros2 topic pub --once --wait-matching-subscriptions 0 {topic} geometry_msgs/msg/Twist '{stop_msg}'"
    )
    run_in_container(str(defaults.get("ros_container", "turbopi")), command, dry_run)


def execute_skill(skill: dict[str, Any], catalog: dict[str, Any], dry_run: bool, params: dict[str, Any] | None = None) -> None:
    defaults = merged_defaults(catalog, params)
    print(
        "[INFO] unit_distance_cm={unit_distance_cm} turn_angle_deg={turn_angle_deg} sensitivity={sensitivity}".format(
            **defaults
        )
    )
    if skill["type"] == "camera_servo":
        execute_camera_servo(skill, defaults, dry_run)
        if bool(defaults.get("capture_after_servo", True)):
            request_camera_capture(defaults, dry_run)
    elif skill["type"] == "base_move":
        execute_base_move(skill, defaults, dry_run)
        if bool(defaults.get("capture_after_move", False)):
            request_camera_capture(defaults, dry_run)
    elif skill["type"] == "base_turn":
        execute_base_turn(skill, defaults, dry_run)
        if bool(defaults.get("capture_after_move", False)):
            request_camera_capture(defaults, dry_run)
    elif skill["type"] == "base_stop":
        execute_base_stop(defaults, dry_run)
    elif skill["type"] == "reset_pose":
        execute_reset_pose(defaults, dry_run)
        if bool(defaults.get("capture_after_servo", True)):
            request_camera_capture(defaults, dry_run)
    elif skill["type"] == "system_shutdown":
        execute_remote_shutdown(dry_run)
    else:
        raise ValueError(f"unsupported skill type: {skill['type']}")


def request_camera_capture(defaults: dict[str, Any], dry_run: bool) -> None:
    settle_ms = int(defaults.get("capture_settle_ms", 500))
    server = str(defaults.get("camera_server", "")).rstrip("/")
    if not server:
        return
    if dry_run:
        print(f"POST {server}/api/capture {{\"mode\":\"single\"}}")
        return
    code = (
        "import json,time,urllib.request;"
        f"time.sleep({max(settle_ms, 0) / 1000!r});"
        "body=json.dumps({'mode':'single'}).encode('utf-8');"
        f"req=urllib.request.Request({server + '/api/capture'!r},data=body,headers={{'Content-Type':'application/json'}},method='POST');"
        "urllib.request.urlopen(req,timeout=3).read()"
    )
    try:
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
        )
    except Exception as exc:
        print(f"[WARN] camera capture request skipped: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute TurboPi camera-look and base-move skills.")
    parser.add_argument("action", help="Skill id or Chinese phrase, for example: look_left")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--params-json", default="{}", help="Cloud settings snapshot for this one-unit action.")
    parser.add_argument("--dry-run", action="store_true", help="Print the docker/ROS command without executing it.")
    args = parser.parse_args()

    catalog = load_catalog(args.catalog)
    try:
        skill = resolve_skill(catalog, args.action)
    except KeyError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        params = json.loads(args.params_json or "{}")
    except json.JSONDecodeError as exc:
        print(f"invalid --params-json: {exc}", file=sys.stderr)
        return 2

    print(f"[INFO] {skill['name_zh']} -> {skill['id']}")
    execute_skill(skill, catalog, args.dry_run, params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
