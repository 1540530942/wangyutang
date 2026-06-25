#!/usr/bin/env python3
"""Run inside the turbopi Docker container on Raspberry Pi.

Subscribes to /ros_robot_controller/imu_raw and /cmd_vel,
then streams data to the pose_tracker server.

Usage:
    docker exec turbopi bash -c "
        source /opt/ros/humble/setup.bash &&
        source /home/ubuntu/ros2_ws/install/setup.bash &&
        python3 /path/to/pi_imu_sender.py --server http://<server-ip>:8300
    "
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
import urllib.request


def _post(server: str, endpoint: str, data: dict) -> None:
    try:
        payload = json.dumps(data).encode()
        req = urllib.request.Request(
            f"{server}{endpoint}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception as exc:
        print(f"[warn] POST {endpoint}: {exc}", file=sys.stderr)


def _sender_thread(server: str, q: queue.Queue) -> None:
    while True:
        endpoint, data = q.get()
        _post(server, endpoint, data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8300")
    args = parser.parse_args()

    import rclpy
    from geometry_msgs.msg import Twist
    from rclpy.node import Node
    from sensor_msgs.msg import Imu

    q: queue.Queue = queue.Queue(maxsize=50)
    t = threading.Thread(target=_sender_thread, args=(args.server, q), daemon=True)
    t.start()

    rclpy.init()
    node = Node("pose_tracker_sender")

    def on_imu(msg: Imu) -> None:
        try:
            q.put_nowait(("/api/imu", {
                "orientation": {
                    "x": msg.orientation.x,
                    "y": msg.orientation.y,
                    "z": msg.orientation.z,
                    "w": msg.orientation.w,
                }
            }))
        except queue.Full:
            pass

    def on_cmd_vel(msg: Twist) -> None:
        try:
            q.put_nowait(("/api/cmd_vel", {
                "linear_x": msg.linear.x,
                "linear_y": msg.linear.y,
                "angular_z": msg.angular.z,
            }))
        except queue.Full:
            pass

    node.create_subscription(Imu, "/ros_robot_controller/imu_raw", on_imu, 10)
    node.create_subscription(Twist, "/cmd_vel", on_cmd_vel, 10)

    print(f"[pose_tracker] streaming to {args.server}")
    rclpy.spin(node)


if __name__ == "__main__":
    main()
