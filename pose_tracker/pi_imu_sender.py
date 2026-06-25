#!/usr/bin/env python3
"""Run inside turbopi Docker container on Raspberry Pi.

Maintains a persistent WebSocket connection to the pose_tracker server.
Streams IMU + cmd_vel at 20 Hz only when the server commands "start".

Usage (inside turbopi container):
    source /opt/ros/humble/setup.bash
    source /home/ubuntu/ros2_ws/install/setup.bash
    pip install websocket-client
    python3 pi_imu_sender.py --server ws://<server-ip>:8300
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time

import websocket  # pip install websocket-client

SEND_INTERVAL = 0.05  # 20 Hz


class PiSender:
    def __init__(self, server_url: str) -> None:
        ws_url = server_url.rstrip("/")
        if ws_url.startswith("http://"):
            ws_url = "ws://" + ws_url[7:]
        elif ws_url.startswith("https://"):
            ws_url = "wss://" + ws_url[8:]
        self._url = ws_url + "/ws/pi"
        self.active = False
        self._ws: websocket.WebSocketApp | None = None
        self._last_imu_t = 0.0
        self._last_vel_t = 0.0
        self._lock = threading.Lock()

    # ── ROS callbacks (called from rclpy thread) ──────────────────────────────

    def on_imu(self, qx: float, qy: float, qz: float, qw: float) -> None:
        if not self.active:
            return
        now = time.time()
        if now - self._last_imu_t < SEND_INTERVAL:
            return
        self._last_imu_t = now
        self._send({"type": "imu", "orientation": {"x": qx, "y": qy, "z": qz, "w": qw}})

    def on_cmd_vel(self, lx: float, ly: float, az: float) -> None:
        if not self.active:
            return
        now = time.time()
        if now - self._last_vel_t < SEND_INTERVAL:
            return
        self._last_vel_t = now
        self._send({"type": "cmd_vel", "linear_x": lx, "linear_y": ly, "angular_z": az})

    # ── WebSocket events (called from websocket thread) ───────────────────────

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        print(f"[pose_tracker] connected → {self._url}")

    def _on_message(self, ws: websocket.WebSocketApp, msg: str) -> None:
        try:
            data = json.loads(msg)
            cmd = data.get("cmd")
            if cmd == "start":
                self.active = True
                print("[pose_tracker] streaming ON")
            elif cmd == "stop":
                self.active = False
                print("[pose_tracker] streaming OFF")
        except Exception:
            pass

    def _on_close(self, ws: websocket.WebSocketApp, code: int, msg: str) -> None:
        self.active = False
        print("[pose_tracker] disconnected, retrying…")

    def _send(self, data: dict) -> None:
        ws = self._ws
        if ws and ws.sock and ws.sock.connected:
            try:
                ws.send(json.dumps(data))
            except Exception as exc:
                print(f"[warn] send: {exc}", file=sys.stderr)

    # ── connection loop (runs in its own thread) ──────────────────────────────

    def run(self) -> None:
        while True:
            self._ws = websocket.WebSocketApp(
                self._url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_close=self._on_close,
            )
            self._ws.run_forever(reconnect=5)
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="ws://127.0.0.1:8300",
                        help="pose_tracker server URL (ws:// or http://)")
    args = parser.parse_args()

    sender = PiSender(args.server)
    threading.Thread(target=sender.run, daemon=True).start()

    import rclpy
    from geometry_msgs.msg import Twist
    from rclpy.node import Node
    from sensor_msgs.msg import Imu

    rclpy.init()
    node = Node("pose_tracker_sender")

    def on_imu(msg: Imu) -> None:
        sender.on_imu(msg.orientation.x, msg.orientation.y,
                      msg.orientation.z, msg.orientation.w)

    def on_cmd_vel(msg: Twist) -> None:
        sender.on_cmd_vel(msg.linear.x, msg.linear.y, msg.angular.z)

    node.create_subscription(Imu, "/ros_robot_controller/imu_raw", on_imu, 10)
    node.create_subscription(Twist, "/cmd_vel", on_cmd_vel, 10)

    print(f"[pose_tracker] ROS subscribed, server={args.server}")
    rclpy.spin(node)


if __name__ == "__main__":
    main()
