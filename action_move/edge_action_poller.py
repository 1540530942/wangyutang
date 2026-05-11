from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SERVER = "https://www.wangyutang.cn/action"


def request_json(url: str, method: str = "GET", payload: dict | None = None, token: str = "", timeout: float = 12) -> dict:
    data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Action-Token"] = token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def run_action(action: str) -> tuple[bool, str, str]:
    command = ["python3", str(BASE_DIR / "action_move_executor.py"), action]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=60)
    return completed.returncode == 0, completed.stdout, completed.stderr


def heartbeat(server: str, token: str, device_id: str, status: str, detail: str = "", current_task_id: str = "") -> None:
    request_json(
        f"{server.rstrip('/')}/api/device/heartbeat",
        method="POST",
        payload={
            "device_id": device_id,
            "status": status,
            "detail": detail[:300],
            "current_task_id": current_task_id,
        },
        token=token,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll TurboPi Action Move cloud tasks and execute local skills.")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--token", default="")
    parser.add_argument("--device-id", default="turbopi-01")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        try:
            heartbeat(args.server, args.token, args.device_id, "idle")
            data = request_json(f"{args.server.rstrip('/')}/api/tasks/next", token=args.token)
            task = data.get("task")
            if task:
                task_id = str(task["id"])
                action = str(task["skill_id"])
                heartbeat(args.server, args.token, args.device_id, "running", current_task_id=task_id)
                ok, stdout, stderr = run_action(action)
                request_json(
                    f"{args.server.rstrip('/')}/api/tasks/result",
                    method="POST",
                    payload={
                        "task_id": task_id,
                        "device_id": args.device_id,
                        "status": "complete" if ok else "failed",
                        "output": stdout[-5000:],
                        "error": stderr[-2000:],
                    },
                    token=args.token,
                )
        except (urllib.error.URLError, TimeoutError, subprocess.SubprocessError, OSError) as exc:
            print(f"[WARN] {exc}", flush=True)
        if args.once:
            return 0
        time.sleep(max(args.interval, 0.5))


if __name__ == "__main__":
    raise SystemExit(main())
