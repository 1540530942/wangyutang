from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from typing import Any


TERMINAL_STATUSES = {"complete", "failed", "rejected", "expired"}


def request_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def action(action_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return request_json(method, f"{action_url.rstrip('/')}{path}", payload)


def slam(slam_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return request_json(method, f"{slam_url.rstrip('/')}{path}", payload)


def create_and_wait(action_url: str, skill_id: str, source: str) -> dict[str, Any]:
    created = action(
        action_url,
        "POST",
        "/api/tasks",
        {
            "action": skill_id,
            "source": source,
            "note": "slam_mapping real-car verification",
            "ttl_seconds": 60,
        },
    )
    task = created.get("task") or {}
    task_id = str(task.get("id") or "")
    if not task_id:
        raise RuntimeError(f"missing task id in response: {created}")
    deadline = time.time() + 90.0
    last_status = ""
    while time.time() < deadline:
        current = action(action_url, "GET", f"/api/tasks/{task_id}").get("task") or {}
        status = str(current.get("status") or "")
        if status != last_status:
            print(json.dumps({"task_id": task_id, "action": skill_id, "status": status}, ensure_ascii=False))
            last_status = status
        if status in TERMINAL_STATUSES:
            if status != "complete":
                raise RuntimeError(f"task {task_id} ended with {status}: {current.get('error')}")
            return current
        time.sleep(0.5)
    raise TimeoutError(f"task {task_id} did not finish")


def assert_close(name: str, actual: float, expected: float, tolerance: float) -> None:
    if abs(actual - expected) > tolerance:
        raise AssertionError(f"{name}: expected {expected} +/- {tolerance}, got {actual}")


def occupied_count(state: dict[str, Any]) -> int:
    grid = ((state.get("map") or {}).get("values") or [])
    return sum(1 for row in grid for value in row if value == 100)


def run(args: argparse.Namespace) -> dict[str, Any]:
    action_url = args.action_url.rstrip("/")
    slam_url = args.slam_url.rstrip("/")

    health = action(action_url, "GET", "/api/health")
    device = health.get("device") or {}
    if not device.get("online"):
        raise RuntimeError(f"robot is offline: {device}")
    if device.get("current_task_id"):
        raise RuntimeError(f"robot already has current task: {device.get('current_task_id')}")

    original_settings = (action(action_url, "GET", "/api/settings").get("settings") or {}).copy()
    verify_settings = dict(original_settings)
    verify_settings.update(
        {
            "unit_distance_cm": 20.0,
            "turn_angle_deg": 90.0,
            "sensitivity": float(args.sensitivity),
            "voice_volume_percent": 0.0,
        }
    )
    for key in ("rgb_red", "rgb_green", "rgb_blue"):
        verify_settings.setdefault(key, int(original_settings.get(key) or 0))

    report: dict[str, Any] = {"device": device, "checks": []}
    try:
        action(action_url, "POST", "/api/settings", verify_settings)
        slam(slam_url, "POST", "/api/reset")

        forward_task = create_and_wait(action_url, "move_forward", "slam-real-verify-forward-20cm")
        state1 = slam(slam_url, "GET", "/api/state")
        pose1 = state1.get("pose") or {}
        assert_close("forward x_m", float(pose1.get("x_m")), 0.2, args.position_tolerance_m)
        assert_close("forward distance_travelled_cm", float(pose1.get("distance_travelled_cm")), 20.0, args.distance_tolerance_cm)
        report["checks"].append({"name": "forward_20cm_pose", "task": forward_task, "state": state1})

        turn_task = create_and_wait(action_url, "turn_left", "slam-real-verify-turn-left-90deg")
        second_forward_task = create_and_wait(action_url, "move_forward", "slam-real-verify-forward-after-turn")
        state2 = slam(slam_url, "GET", "/api/state")
        pose2 = state2.get("pose") or {}
        assert_close("turn+forward x_m", float(pose2.get("x_m")), 0.2, args.position_tolerance_m)
        assert_close("turn+forward y_m", float(pose2.get("y_m")), 0.2, args.position_tolerance_m)
        assert_close("turn+forward yaw_deg", float(pose2.get("yaw_deg")), 90.0, args.yaw_tolerance_deg)
        report["checks"].append(
            {
                "name": "turn_left_90_then_forward_axis_change",
                "tasks": [turn_task, second_forward_task],
                "state": state2,
            }
        )

        slam(slam_url, "POST", "/api/config", {"map_enabled": True, "resolution_m": 0.1, "size_m": 4.0})
        scan_state = slam(
            slam_url,
            "POST",
            "/api/scan",
            {"ranges_m": [0.8], "angle_min_rad": 0.0, "angle_increment_rad": 0.0174532925},
        )
        state3 = slam(slam_url, "GET", "/api/state?include_map=true")
        count = occupied_count(state3)
        if count < 1:
            raise AssertionError("front 80cm scan did not mark any occupancy cell as 100")
        report["checks"].append({"name": "front_80cm_scan_marks_obstacle", "scan_response": scan_state, "occupied_count": count})
        report["ok"] = True
        return report
    finally:
        if original_settings:
            action(action_url, "POST", "/api/settings", original_settings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strict real-car SLAM verification through action_move and slam_mapping.")
    parser.add_argument("--action-url", default="https://www.wangyutang.cn/action")
    parser.add_argument("--slam-url", default="https://www.wangyutang.cn/slam")
    parser.add_argument("--sensitivity", type=float, default=1.0)
    parser.add_argument("--position-tolerance-m", type=float, default=0.03)
    parser.add_argument("--distance-tolerance-cm", type=float, default=3.0)
    parser.add_argument("--yaw-tolerance-deg", type=float, default=2.0)
    args = parser.parse_args()
    try:
        report = run(args)
    except Exception as exc:  # noqa: BLE001 - CLI should report the exact failed invariant.
        print(f"SLAM_REAL_VERIFY_FAILED={exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("SLAM_REAL_VERIFY_OK=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
