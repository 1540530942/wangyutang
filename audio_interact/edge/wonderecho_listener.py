"""WonderEchoPro Pi-side listener for audio_interact.

Flow:
  poll /api/settings on audio_interact cloud
  -> when manual_recording_enabled and input_mode==wonderechopro
  -> arecord WAV segment
  -> POST /api/audio/segment to audio_interact
  -> play tts_audio_base64 locally via aplay/paplay

No audio_recognition package dependency.  Requires only stdlib + recorder.

Run:
  python3 wonderecho_listener.py --config config.json
  python3 wonderecho_listener.py --config config.json --once
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from recorder import record_wav  # noqa: E402

DEFAULT_CONFIG = SCRIPT_DIR / "config.example.json"
_FINAL_STATUS_SKIP = {"completed", "done", "emergency_stop", "dry_run", "rejected", ""}


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib-only)
# ---------------------------------------------------------------------------

def _get_json(url: str, token: str, timeout: float = 8.0) -> dict[str, Any] | None:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Audio-Token"] = token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"[WARN] GET {url} failed: {exc}", flush=True)
        return None


def _post_segment(server: str, token: str, wav_path: Path, device_id: str) -> dict[str, Any] | None:
    """Multipart POST of WAV file to /api/audio/segment."""
    boundary = f"----WLBoundary{int(time.time() * 1000)}"
    body_parts: list[bytes] = []

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    body_parts.append(_field("device_id", device_id))
    body_parts.append(_field("session_id", f"wep-{int(time.time() * 1000)}"))

    wav_data = wav_path.read_bytes()
    body_parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{wav_path.name}"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode("utf-8")
        + wav_data
        + b"\r\n"
    )
    body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(body_parts)

    headers: dict[str, str] = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))}
    if token:
        headers["X-Audio-Token"] = token

    req = urllib.request.Request(f"{server.rstrip('/')}/api/audio/segment", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"[WARN] segment upload failed: {exc}", flush=True)
        return None


# ---------------------------------------------------------------------------
# TTS local playback
# ---------------------------------------------------------------------------

def _play_tts_base64(b64: str, device: str = "") -> None:
    player = shutil.which("aplay") or shutil.which("paplay") or ""
    if not player:
        print("[WARN] tts_play: no audio player found (aplay/paplay)", flush=True)
        return
    wav_bytes = base64.b64decode(b64)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        fh.write(wav_bytes)
        tmp_path = Path(fh.name)
    try:
        cmd = [player, "-q"]
        if device and Path(player).name == "aplay":
            cmd.extend(["-D", device])
        cmd.append(str(tmp_path))
        subprocess.run(cmd, capture_output=True, timeout=12)
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def get_cloud_settings(server: str, token: str) -> dict[str, Any]:
    result = _get_json(f"{server.rstrip('/')}/api/settings", token)
    if result is None:
        return {"manual_recording_enabled": False, "input_mode": "wonderechopro"}
    return result.get("settings", result) if isinstance(result, dict) else {"manual_recording_enabled": False}


def process_once(config: dict[str, Any]) -> dict[str, Any] | None:
    server = str(config.get("server") or "")
    token = str(config.get("token") or "")
    device_id = str(config.get("device_id") or "turbopi-01")
    tts_device = str(config.get("tts_device") or "")

    print(f"[INFO] recording WAV for device_id={device_id}", flush=True)
    wav_path = record_wav(config.get("recorder", {}))
    print(f"[INFO] recorded {wav_path}", flush=True)

    result = _post_segment(server, token, wav_path, device_id)
    if result is None:
        return None

    text = str(result.get("text") or "")
    tts_text = str(result.get("tts_text") or "")
    tts_b64 = str(result.get("tts_audio_base64") or "")
    print(json.dumps({"text": text, "tts_text": tts_text, "ok": result.get("ok"), "wake_status": result.get("wake_status")}, ensure_ascii=False), flush=True)

    if tts_b64 and tts_text not in _FINAL_STATUS_SKIP:
        threading.Thread(target=_play_tts_base64, args=(tts_b64, tts_device), daemon=True).start()

    try:
        wav_path.unlink(missing_ok=True)
    except OSError:
        pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="WonderEchoPro -> audio_interact listener (Pi-side).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--once", action="store_true", help="Record once and exit.")
    parser.add_argument("--loop-gap", type=float, default=0.5, help="Seconds between poll cycles.")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"[ERROR] config not found: {args.config}", flush=True)
        return 1

    config = json.loads(args.config.read_text(encoding="utf-8"))
    server = str(config.get("server") or "")
    token = str(config.get("token") or "")
    background_jobs: list[threading.Thread] = []

    def run_bg(wav_path: Path) -> None:
        try:
            result = _post_segment(server, token, wav_path, str(config.get("device_id") or "turbopi-01"))
            tts_b64 = str((result or {}).get("tts_audio_base64") or "")
            tts_text = str((result or {}).get("tts_text") or "")
            if tts_b64 and tts_text not in _FINAL_STATUS_SKIP:
                _play_tts_base64(tts_b64, str(config.get("tts_device") or ""))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] bg process failed: {exc}", flush=True)

    while True:
        try:
            settings = get_cloud_settings(server, token)
            input_mode = str(settings.get("input_mode") or "wonderechopro")
            manual_enabled = bool(settings.get("manual_recording_enabled"))

            background_jobs[:] = [j for j in background_jobs if j.is_alive()]

            if input_mode != "wonderechopro":
                time.sleep(max(args.loop_gap, 1.5))
                continue
            if not args.once and not manual_enabled:
                time.sleep(max(args.loop_gap, 1.5))
                continue

            wav_path = record_wav(config.get("recorder", {}))
            print(f"[INFO] recorded {wav_path}", flush=True)

            if args.once:
                result = _post_segment(server, token, wav_path, str(config.get("device_id") or "turbopi-01"))
                tts_b64 = str((result or {}).get("tts_audio_base64") or "")
                tts_text = str((result or {}).get("tts_text") or "")
                if tts_b64 and tts_text not in _FINAL_STATUS_SKIP:
                    _play_tts_base64(tts_b64, str(config.get("tts_device") or ""))
                print(json.dumps(result or {}, ensure_ascii=False), flush=True)
                return 0

            job = threading.Thread(target=run_bg, args=(wav_path,), daemon=True)
            job.start()
            background_jobs.append(job)
            time.sleep(max(args.loop_gap, 0.0))

        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
            if args.once:
                return 1
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
