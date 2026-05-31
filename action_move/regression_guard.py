from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
ACTION_SERVER = "https://www.wangyutang.cn/action"
CAMERA_SERVER = "https://www.wangyutang.cn/camera"
REQUIRED_SKILLS = {
    "emergency_stop",
    "reset_pose",
    "remote_shutdown",
    "rgb_on",
    "rgb_off",
    "look_left",
    "look_right",
    "look_up",
    "look_down",
    "move_forward",
    "move_backward",
    "move_left",
    "move_right",
    "turn_left",
    "turn_right",
}


class Guard:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        if condition:
            print(f"[PASS] {message}")
        else:
            print(f"[FAIL] {message}")
            self.failures.append(message)

    def finish(self) -> int:
        if self.failures:
            print("\nFailures:")
            for item in self.failures:
                print(f"- {item}")
            return 1
        print("\nAll guard checks passed.")
        return 0


def request_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 12) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"detail": body}
        return exc.code, parsed


def local_guard(guard: Guard) -> None:
    sys.path.insert(0, str(PROJECT_DIR))
    from fastapi.testclient import TestClient
    from action_move.server import app, tasks

    tasks.clear()
    client = TestClient(app)

    health = client.get("/api/health")
    guard.check(health.status_code == 200 and health.json()["status"] == "ok", "local action health works")

    skills = client.get("/api/skills").json()["skills"]
    skill_ids = {item["id"] for item in skills}
    guard.check(REQUIRED_SKILLS.issubset(skill_ids), "local skill catalog contains required skills")

    bad_shutdown = client.post("/api/tasks", json={"action": "remote_shutdown", "verification_code": "000"})
    guard.check(bad_shutdown.status_code == 403, "remote shutdown rejects wrong verification code")

    good_shutdown = client.post("/api/tasks", json={"action": "remote_shutdown", "verification_code": "123", "source": "guard-local"})
    guard.check(
        good_shutdown.status_code == 200 and good_shutdown.json()["task"]["skill_id"] == "remote_shutdown",
        "remote shutdown accepts verification code 123 locally",
    )

    tasks.clear()
    rgb_task = client.post("/api/tasks", json={"action": "rgb_on", "source": "guard-local"})
    guard.check(rgb_task.status_code == 200 and rgb_task.json()["task"]["skill_id"] == "rgb_on", "RGB task can be created locally")

    tasks.clear()
    first_motion = client.post("/api/tasks", json={"action": "move_forward", "source": "guard-local"})
    second_motion = client.post("/api/tasks", json={"action": "move_backward", "source": "guard-local"})
    guard.check(first_motion.status_code == 200 and second_motion.status_code == 409, "motion queue rejects overlapping motion")


def static_guard(guard: Guard) -> None:
    index = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    style = (BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")

    for token in ("cameraPreviewToggle", "cameraPreviewImage", "cameraSymbol", "相机小窗", "rgbColorInput", "rgbRedInput", "rgb_on", "rgb_off"):
        guard.check(token in index, f"index contains {token}")
    for token in ("monitorCameraOnce", "CAMERA_HEARTBEAT_MS", "/camera/api/latest", "changedRatio", "hexToRgb", "rgbColorInput"):
        guard.check(token in app_js, f"app.js contains {token}")
    for token in (".camera-preview", ".camera-symbol", ".toggle-line", ".rgb-panel", ".rgb-swatch"):
        guard.check(token in style, f"style contains {token}")

    # Cheap syntax sentries for environments without node.
    guard.check(app_js.count("{") == app_js.count("}"), "app.js brace count is balanced")
    guard.check(app_js.count("(") == app_js.count(")"), "app.js parenthesis count is balanced")
    guard.check("remote_shutdown" in app_js and "verification_code" in app_js, "shutdown prompt payload is present")


def cloud_guard(guard: Guard) -> None:
    status, health = request_json(f"{ACTION_SERVER}/api/health")
    guard.check(status == 200 and health.get("status") == "ok", "cloud action health works")
    device = health.get("device", {})
    for key in ("hostname", "ip_address", "wifi_ssid", "gateway"):
        guard.check(key in device, f"cloud action health exposes device.{key}")

    status, skills = request_json(f"{ACTION_SERVER}/api/skills")
    skill_ids = {item["id"] for item in skills.get("skills", [])}
    guard.check(status == 200 and REQUIRED_SKILLS.issubset(skill_ids), "cloud skill catalog contains required skills")

    status, bad_shutdown = request_json(
        f"{ACTION_SERVER}/api/tasks",
        method="POST",
        payload={"action": "remote_shutdown", "verification_code": "000", "source": "guard-cloud"},
    )
    guard.check(status == 403, "cloud remote shutdown rejects wrong verification code")

    action_html = urllib.request.urlopen(f"{ACTION_SERVER}/", timeout=12).read().decode("utf-8")
    app_js = urllib.request.urlopen(f"{ACTION_SERVER}/static/app.js", timeout=12).read().decode("utf-8")
    guard.check(
        "cameraPreviewToggle" in action_html and "远程关机" in action_html and "rgbColorInput" in action_html,
        "cloud page contains camera toggle, shutdown button, and RGB controls",
    )
    guard.check(
        "monitorCameraOnce" in app_js and "CAMERA_HEARTBEAT_MS" in app_js and "hexToRgb" in app_js,
        "cloud app.js contains camera monitor and RGB helpers",
    )

    status, camera = request_json(f"{CAMERA_SERVER}/api/health")
    guard.check(status == 200 and camera.get("status") == "ok" and camera.get("has_image") is True, "cloud camera health has image")


def main() -> int:
    parser = argparse.ArgumentParser(description="Regression guard for action_move without sending motion commands.")
    parser.add_argument("--local", action="store_true", help="Run local FastAPI/static checks.")
    parser.add_argument("--cloud", action="store_true", help="Run cloud endpoint checks. Does not create valid shutdown tasks.")
    args = parser.parse_args()
    if not args.local and not args.cloud:
        args.local = True
        args.cloud = True

    guard = Guard()
    if args.local:
        local_guard(guard)
        static_guard(guard)
    if args.cloud:
        cloud_guard(guard)
    return guard.finish()


if __name__ == "__main__":
    raise SystemExit(main())
