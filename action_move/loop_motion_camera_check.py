from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from urllib import error as urlerror
from urllib import request


BASE_ACTION = "https://www.wangyutang.cn/action"
BASE_CAMERA = "https://www.wangyutang.cn/camera"
BASE_DIR = Path(__file__).resolve().parent
OUT_ROOT = BASE_DIR / "movement_verify"

SAFE_SETTINGS = {
    "unit_distance_cm": 1.0,
    "turn_angle_deg": 5.0,
    "sensitivity": 0.5,
    "voice_volume_percent": 0.0,
    "rgb_red": 0,
    "rgb_green": 0,
    "rgb_blue": 0,
}

ACTIONS = ["reset_pose", "move_forward", "move_backward", "move_left", "move_right"]


def exception_info(exc: BaseException) -> dict:
    info = {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exception_only(type(exc), exc),
    }
    if isinstance(exc, urlerror.HTTPError):
        info.update({"url": exc.url, "code": exc.code, "reason": exc.reason})
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if body:
            info["body"] = body[:2000]
    elif isinstance(exc, urlerror.URLError):
        info["reason"] = str(exc.reason)
    return info


def http_json(url: str, method: str = "GET", payload: dict | None = None, timeout: float = 20) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_bytes(url: str, timeout: float = 20) -> bytes:
    with request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def post_settings() -> dict:
    return http_json(f"{BASE_ACTION}/api/settings", "POST", SAFE_SETTINGS)


def health() -> dict:
    return http_json(f"{BASE_ACTION}/api/health", timeout=10)["device"]


def capture(out_dir: Path, label: str) -> dict:
    task = http_json(
        f"{BASE_CAMERA}/api/capture",
        "POST",
        {"kind": "camera", "mode": "single", "query_gpio": 26},
    )["task"]
    task_id = str(task["id"])
    latest = {}
    for _ in range(24):
        time.sleep(0.5)
        latest = http_json(f"{BASE_CAMERA}/api/latest?kind=camera", timeout=10)
        if latest.get("task_id") == task_id and latest.get("has_image"):
            break
    image = http_bytes(f"{BASE_CAMERA}/api/latest.jpg?kind=camera&t={time.time()}")
    path = out_dir / f"{label}.jpg"
    path.write_bytes(image)
    return {"task_id": task_id, "latest": latest, "path": str(path), "bytes": len(image)}


def create_action(action: str) -> dict:
    return http_json(
        f"{BASE_ACTION}/api/tasks",
        "POST",
        {"action": action, "source": "codex-loop-camera-check", "ttl_seconds": 45},
    )["task"]


def wait_task(task_id: str) -> dict:
    last = {}
    for _ in range(90):
        time.sleep(0.5)
        last = http_json(f"{BASE_ACTION}/api/tasks/{task_id}", timeout=10)["task"]
        if last.get("status") in {"complete", "failed", "rejected", "expired"}:
            return last
    return last


def image_diff(before_path: str, after_path: str) -> dict:
    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception as exc:  # noqa: BLE001 - diagnostics should continue without PIL.
        return {"error": f"PIL unavailable: {exc}"}
    before = Image.open(before_path).convert("RGB").resize((160, 120))
    after = Image.open(after_path).convert("RGB").resize((160, 120))
    diff = ImageChops.difference(before, after)
    stat = ImageStat.Stat(diff)
    mean = sum(stat.mean) / 3.0
    pixels = list(diff.getdata())
    changed = sum(1 for pixel in pixels if sum(pixel) > 24)
    return {
        "mean_abs_diff": round(mean, 3),
        "changed_percent_gt8": round(changed * 100.0 / len(pixels), 3),
    }


def run_once() -> dict:
    out_dir = OUT_ROOT / ("loop_" + time.strftime("%Y%m%d_%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "started_at": time.time(),
        "out_dir": str(out_dir),
        "settings": SAFE_SETTINGS,
        "steps": [],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        initial_health = health()
        summary["initial_health"] = initial_health
        if not initial_health.get("online"):
            summary["status"] = "skipped_offline"
            return summary

        summary["settings_post"] = post_settings()
        for action in ACTIONS:
            phase = "before_health"
            step = {"action": action}
            try:
                before_health = health()
                step["before_health"] = before_health
                phase = "before_capture"
                before = capture(out_dir, f"{action}_before")
                step["before_capture"] = before
                phase = "create_action"
                task = create_action(action)
                step["task_id"] = task["id"]
                phase = "wait_task"
                result = wait_task(str(task["id"]))
                step["result"] = result
                time.sleep(1.0)
                phase = "after_health"
                after_health = health()
                step["after_health"] = after_health
                phase = "after_capture"
                after = capture(out_dir, f"{action}_after") if after_health.get("online") else None
                step["after_capture"] = after
                if after:
                    step["camera_diff"] = image_diff(before["path"], after["path"])
                summary["steps"].append(step)
                (out_dir / "summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if action != "reset_pose" and (
                    result.get("status") != "complete" or not after_health.get("online")
                ):
                    summary["status"] = "stopped_after_failure"
                    summary["failed_action"] = action
                    break
            except Exception as exc:  # noqa: BLE001 - diagnostics must survive partial outages.
                step["status"] = "exception"
                step["failed_phase"] = phase
                step["error"] = exception_info(exc)
                try:
                    step["after_exception_health"] = health()
                except Exception as health_exc:  # noqa: BLE001
                    step["after_exception_health_error"] = exception_info(health_exc)
                summary["steps"].append(step)
                summary["status"] = "stopped_after_exception"
                summary["failed_action"] = action
                summary["failed_phase"] = phase
                break
        else:
            summary["status"] = "complete"
    except Exception as exc:  # noqa: BLE001 - keep the scheduler log structured.
        summary["status"] = "error"
        summary["error"] = exception_info(exc)
    finally:
        summary["finished_at"] = time.time()
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    result = run_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") != "complete":
        sys.exit(1)
