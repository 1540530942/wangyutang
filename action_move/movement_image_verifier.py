from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import paramiko
from PIL import Image, ImageStat


SERVER = "https://www.wangyutang.cn/camera"
ACTION_DIR = "/home/pi/action_move"
DEFAULT_ACTIONS = ["move_forward", "move_backward", "move_left", "move_right"]


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def fetch_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_capture(prev_task_id: str | None = None) -> dict[str, Any]:
    body = json.dumps({"mode": "single"}).encode("utf-8")
    request = urllib.request.Request(
        f"{SERVER}/api/capture",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(request, timeout=8).read()
    deadline = time.time() + 25
    latest = fetch_json(f"{SERVER}/api/latest")
    while time.time() < deadline:
        latest = fetch_json(f"{SERVER}/api/latest")
        if latest.get("task_id") and latest.get("task_id") != prev_task_id:
            return latest
        time.sleep(0.8)
    return latest


def download_latest(path: Path) -> int:
    data = urllib.request.urlopen(f"{SERVER}/api/latest.jpg?t={int(time.time() * 1000)}", timeout=10).read()
    path.write_bytes(data)
    return len(data)


def run_pi_action(host: str, user: str, password: str, action: str) -> str:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=10, banner_timeout=10, auth_timeout=10)
    command = f"cd {ACTION_DIR} && python3 action_move_executor.py {action}"
    stdin, stdout, stderr = ssh.exec_command(command, timeout=60)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    ssh.close()
    return out + (f"\nSTDERR:\n{err}" if err else "")


def crop_for_motion(image: Image.Image) -> Image.Image:
    width, height = image.size
    # Drop the watermark band and a small border where compression noise is common.
    return image.crop((16, 16, width - 16, max(17, height - 80)))


def image_metrics(before_path: Path, after_path: Path) -> dict[str, Any]:
    before = crop_for_motion(Image.open(before_path).convert("L"))
    after = crop_for_motion(Image.open(after_path).convert("L"))
    before_arr = np.asarray(before, dtype=np.int16)
    after_arr = np.asarray(after, dtype=np.int16)
    diff = np.abs(after_arr - before_arr)
    changed = diff > 8
    before_stat = ImageStat.Stat(before)
    after_stat = ImageStat.Stat(after)
    return {
        "image_size": Image.open(after_path).size,
        "before_luma_mean": round(float(before_stat.mean[0]), 2),
        "after_luma_mean": round(float(after_stat.mean[0]), 2),
        "mean_abs_diff": round(float(diff.mean()), 2),
        "changed_pixels_gt8": int(changed.sum()),
        "changed_percent_gt8": round(float(changed.mean() * 100), 2),
        "valid_after_frame": after_path.stat().st_size > 12000 and float(after_stat.mean[0]) > 5,
    }


def verify_action(action: str, out_dir: Path, args: argparse.Namespace, previous_task_id: str | None) -> tuple[dict[str, Any], str]:
    action_dir = out_dir / action
    action_dir.mkdir(parents=True, exist_ok=True)

    before_meta = request_capture(previous_task_id)
    before_path = action_dir / "before.jpg"
    before_bytes = download_latest(before_path)

    time.sleep(args.settle_before)
    command_output = run_pi_action(args.host, args.user, args.password, action)
    time.sleep(args.settle_after)

    after_meta = request_capture(str(before_meta.get("task_id") or ""))
    after_path = action_dir / "after.jpg"
    after_bytes = download_latest(after_path)

    metrics = image_metrics(before_path, after_path)
    result = {
        "action": action,
        "before": {
            "task_id": before_meta.get("task_id"),
            "frame_id": before_meta.get("frame_id"),
            "content_length": before_meta.get("content_length"),
            "downloaded_bytes": before_bytes,
            "image": str(before_path),
        },
        "after": {
            "task_id": after_meta.get("task_id"),
            "frame_id": after_meta.get("frame_id"),
            "content_length": after_meta.get("content_length"),
            "downloaded_bytes": after_bytes,
            "image": str(after_path),
        },
        "metrics": metrics,
        "command_output": command_output.strip(),
        "control_observed": "publishing #" in command_output,
        "image_changed": metrics["valid_after_frame"] and metrics["changed_percent_gt8"] >= args.changed_threshold,
    }
    return result, str(after_meta.get("task_id") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify movement control behavior with before/after camera images.")
    parser.add_argument("actions", nargs="*", default=DEFAULT_ACTIONS)
    parser.add_argument("--host", default="raspberrypi.local")
    parser.add_argument("--user", default="pi")
    parser.add_argument("--password", default="raspberrypi")
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent / "movement_verify")
    parser.add_argument("--changed-threshold", type=float, default=2.0)
    parser.add_argument("--settle-before", type=float, default=0.5)
    parser.add_argument("--settle-after", type=float, default=1.0)
    args = parser.parse_args()

    run_dir = args.out_dir / stamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    latest = fetch_json(f"{SERVER}/api/latest")
    previous_task_id = str(latest.get("task_id") or "")
    results: list[dict[str, Any]] = []

    for action in args.actions:
        result, previous_task_id = verify_action(action, run_dir, args, previous_task_id)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    summary = {
        "run_dir": str(run_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
