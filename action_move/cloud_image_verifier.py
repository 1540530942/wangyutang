from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageStat


ACTION_SERVER = "https://www.wangyutang.cn/action"
CAMERA_SERVER = "https://www.wangyutang.cn/camera"
DEFAULT_ACTIONS = [
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
]


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def request_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 12) -> dict[str, Any]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_capture(previous_task_id: str = "") -> dict[str, Any]:
    task = request_json(f"{CAMERA_SERVER}/api/capture", method="POST", payload={"mode": "single"})["task"]
    deadline = time.time() + 25
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        latest = request_json(f"{CAMERA_SERVER}/api/latest")
        if latest.get("task_id") == task["id"] and latest.get("task_id") != previous_task_id:
            return latest
        time.sleep(0.5)
    return latest


def download_latest(path: Path) -> int:
    data = urllib.request.urlopen(f"{CAMERA_SERVER}/api/latest.jpg?t={int(time.time() * 1000)}", timeout=12).read()
    path.write_bytes(data)
    return len(data)


def create_action(action: str) -> dict[str, Any]:
    try:
        return request_json(
            f"{ACTION_SERVER}/api/tasks",
            method="POST",
            payload={"action": action, "source": "cloud-image-verifier", "ttl_seconds": 30},
        )["task"]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"create action {action} failed: {exc.code} {body}") from exc


def wait_action(task_id: str) -> dict[str, Any]:
    deadline = time.time() + 45
    last: dict[str, Any] = {}
    while time.time() < deadline:
        tasks = request_json(f"{ACTION_SERVER}/api/tasks")["tasks"]
        for task in tasks:
            if task.get("id") == task_id:
                last = task
                if task.get("status") in {"complete", "failed", "rejected", "expired"}:
                    return task
        time.sleep(0.35)
    return last


def crop_for_motion(image: Image.Image) -> Image.Image:
    width, height = image.size
    return image.crop((16, 16, width - 16, max(17, height - 80)))


def image_metrics(before_path: Path, after_path: Path) -> dict[str, Any]:
    before_img = Image.open(before_path).convert("L")
    after_img = Image.open(after_path).convert("L")
    before = crop_for_motion(before_img)
    after = crop_for_motion(after_img)
    before_arr = np.asarray(before, dtype=np.int16)
    after_arr = np.asarray(after, dtype=np.int16)
    diff = np.abs(after_arr - before_arr)
    changed = diff > 8
    before_stat = ImageStat.Stat(before)
    after_stat = ImageStat.Stat(after)
    return {
        "image_size": after_img.size,
        "before_luma_mean": round(float(before_stat.mean[0]), 2),
        "after_luma_mean": round(float(after_stat.mean[0]), 2),
        "mean_abs_diff": round(float(diff.mean()), 2),
        "changed_pixels_gt8": int(changed.sum()),
        "changed_percent_gt8": round(float(changed.mean() * 100), 2),
        "valid_after_frame": after_path.stat().st_size > 12000 and float(after_stat.mean[0]) > 5,
    }


def verify_action(action: str, run_dir: Path, previous_task_id: str, threshold: float) -> tuple[dict[str, Any], str]:
    action_dir = run_dir / action
    action_dir.mkdir(parents=True, exist_ok=True)

    before_meta = request_capture(previous_task_id)
    before_path = action_dir / "before.jpg"
    before_bytes = download_latest(before_path)
    time.sleep(0.25)

    task = create_action(action)
    final_task = wait_action(task["id"])
    time.sleep(0.9)

    after_meta = request_capture(str(before_meta.get("task_id") or ""))
    after_path = action_dir / "after.jpg"
    after_bytes = download_latest(after_path)

    metrics = image_metrics(before_path, after_path)
    result = {
        "action": action,
        "task": final_task,
        "before": {
            "task_id": before_meta.get("task_id"),
            "frame_id": before_meta.get("frame_id"),
            "downloaded_bytes": before_bytes,
            "image": str(before_path),
        },
        "after": {
            "task_id": after_meta.get("task_id"),
            "frame_id": after_meta.get("frame_id"),
            "downloaded_bytes": after_bytes,
            "image": str(after_path),
        },
        "metrics": metrics,
        "image_changed": metrics["valid_after_frame"] and metrics["changed_percent_gt8"] >= threshold,
        "task_completed": final_task.get("status") == "complete",
    }
    return result, str(after_meta.get("task_id") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify action_move cloud buttons with before/after camera images.")
    parser.add_argument("actions", nargs="*", default=DEFAULT_ACTIONS)
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent / "cloud_image_verify")
    parser.add_argument("--changed-threshold", type=float, default=2.0)
    args = parser.parse_args()

    run_dir = args.out_dir / stamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    latest = request_json(f"{CAMERA_SERVER}/api/latest")
    previous_task_id = str(latest.get("task_id") or "")
    results: list[dict[str, Any]] = []

    for action in args.actions:
        result, previous_task_id = verify_action(action, run_dir, previous_task_id, args.changed_threshold)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    summary = {
        "run_dir": str(run_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "threshold": args.changed_threshold,
        "results": results,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
