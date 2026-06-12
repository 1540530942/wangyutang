from __future__ import annotations

import argparse
import base64
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from audio_recognition.harness.react_loop import route_transcript, transcribe_audio_path
from audio_recognition.transport.model_provider import build_provider
from audio_recognition.transport.recorder import record_wav


PACKAGE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PACKAGE_DIR / "config.json"


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_cloud_settings(server: str, token: str) -> dict[str, Any]:
    if not server:
        return {"manual_recording_enabled": False}
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Audio-Token"] = token
    request = urllib.request.Request(f"{server.rstrip('/')}/api/settings", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"[WARN] settings fetch failed, keep listener idle: {exc}", flush=True)
        return {"manual_recording_enabled": False}
    settings = payload.get("settings", payload)
    return settings if isinstance(settings, dict) else {"manual_recording_enabled": False}


def post_audio_result(server: str, token: str, payload: dict[str, Any]) -> None:
    if not server:
        return
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Audio-Token"] = token
    request = urllib.request.Request(
        f"{server.rstrip('/')}/api/results",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        print(f"[WARN] audio result upload failed: {exc}", flush=True)


def post_pipeline_event(
    server: str,
    token: str,
    device_id: str,
    stage: str,
    status: str = "ok",
    message: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    if not server:
        return
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Audio-Token"] = token
    payload = {
        "device_id": device_id,
        "stage": stage,
        "status": status,
        "message": message,
        "details": details or {},
        "reported_at": time.time(),
    }
    request = urllib.request.Request(
        f"{server.rstrip('/')}/api/events",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        print(f"[WARN] pipeline event upload failed: {exc}", flush=True)


def process_once(config: dict[str, Any]) -> dict[str, Any]:
    cloud = config.get("cloud", {})
    audio_server = str(cloud.get("audio_server") or "")
    audio_token = str(cloud.get("audio_token") or "")
    device_id = str(config.get("device_id") or "turbopi-01")

    post_pipeline_event(audio_server, audio_token, device_id, "recording", "running", "capturing local WAV with arecord")
    wav_path = record_wav(config.get("recorder", {}))
    post_pipeline_event(
        audio_server,
        audio_token,
        device_id,
        "recording",
        "ok",
        "WAV captured",
        {"wav_path": str(wav_path)},
    )

    post_pipeline_event(audio_server, audio_token, device_id, "audio_conversion", "running", "normalizing audio payload for ASR provider")
    return process_wav(config, wav_path)


def process_wav(config: dict[str, Any], wav_path: str | Path) -> dict[str, Any]:
    cloud = config.get("cloud", {})
    audio_server = str(cloud.get("audio_server") or "")
    audio_token = str(cloud.get("audio_token") or "")
    device_id = str(config.get("device_id") or "turbopi-01")
    wav_path = Path(wav_path)
    provider_config = dict(config.get("model_provider", {}))
    provider = build_provider(provider_config)
    try:
        routed_audio = transcribe_audio_path(
            base_dir=BASE_DIR,
            wav_path=wav_path,
            provider=provider,
            router_config=config.get("router", {}),
        )
        asr_error = str(routed_audio.get("error") or "")
        post_pipeline_event(
            audio_server,
            audio_token,
            device_id,
            "model_asr",
            "ok" if not asr_error else "empty",
            "model returned real transcript" if not asr_error else "model returned no transcript",
            {"text": routed_audio.get("text", ""), "error": asr_error, "asr_provider": "common_api", "plan": routed_audio.get("plan")},
        )
    except Exception as exc:  # noqa: BLE001 - report real captured audio even if ASR fails
        routed_audio = {"text": "", "raw": {}, "error": str(exc), "skill_id": "", "plan": None}
        post_pipeline_event(
            audio_server,
            audio_token,
            device_id,
            "model_asr",
            "failed",
            str(exc),
            {"wav_path": str(wav_path), "asr_provider": "common_api"},
        )

    text = str(routed_audio.get("text") or "")
    plan = routed_audio.get("plan")
    routed = route_transcript(
        base_dir=BASE_DIR,
        text=text,
        router_config=config.get("router", {}),
        cloud_config=cloud,
        route_action=False,
        source="edge_audio_listener",
    )

    payload = {
        "device_id": device_id,
        "text": text or "noise_or_unrecognized_audio",
        "wav_path": str(wav_path),
        "skill_id": str(routed.get("skill_id") or ""),
        "audio_base64": base64.b64encode(wav_path.read_bytes()).decode("ascii"),
        "audio_mime": "audio/wav",
        "audio_filename": wav_path.name,
        "raw": {
            "asr": routed_audio.get("raw", {}),
            "asr_error": routed_audio.get("error", ""),
            "captured_audio": True,
            "recognized": bool(text),
            "plan": plan,
            "action_task": routed.get("action_task"),
            "action_error": routed.get("action_error", ""),
            "face_task": routed.get("face_task"),
            "face_error": routed.get("face_error", ""),
        },
        "reported_at": time.time(),
    }
    post_audio_result(audio_server, audio_token, payload)
    post_pipeline_event(
        audio_server,
        audio_token,
        device_id,
        "text_display",
        "ok",
        "recognized text uploaded",
        {"text": text, "skill_id": payload["skill_id"], "plan": plan},
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="WonderEchoPro manual capture -> ASR -> text result.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--once", action="store_true", help="Record once immediately.")
    parser.add_argument("--record-loop", action="store_true", help="Continuously poll cloud settings and record when manual capture is enabled.")
    parser.add_argument("--record-loop-gap", type=float, default=0.5, help="Seconds to wait between record-loop captures.")
    parser.add_argument("--record-loop-async", action="store_true", default=True, help="Process ASR in the background so recording does not pause.")
    parser.add_argument("--max-background-jobs", type=int, default=2, help="Maximum simultaneous ASR jobs in record-loop mode.")
    args = parser.parse_args()

    config = load_config(args.config)
    cloud = config.get("cloud", {})
    audio_server = str(cloud.get("audio_server") or "")
    audio_token = str(cloud.get("audio_token") or "")
    device_id = str(config.get("device_id") or "turbopi-01")
    background_jobs: list[threading.Thread] = []

    def cleanup_background_jobs() -> None:
        background_jobs[:] = [job for job in background_jobs if job.is_alive()]

    def process_wav_background(wav_path: Path) -> None:
        try:
            post_pipeline_event(audio_server, audio_token, device_id, "audio_conversion", "running", "normalizing audio payload for ASR provider")
            payload = process_wav(config, wav_path)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001 - keep continuous capture alive
            post_pipeline_event(audio_server, audio_token, device_id, "edge_pipeline", "failed", str(exc), {"wav_path": str(wav_path)})
            print(json.dumps({"ok": False, "wav_path": str(wav_path), "error": str(exc)}, ensure_ascii=False), flush=True)

    while True:
        try:
            settings = get_cloud_settings(audio_server, audio_token)
            input_mode = str(settings.get("input_mode") or "wonderechopro")
            manual_enabled = bool(settings.get("manual_recording_enabled"))
            if input_mode != "wonderechopro":
                cleanup_background_jobs()
                time.sleep(max(args.record_loop_gap, 1.5))
                continue
            if args.record_loop and not manual_enabled:
                cleanup_background_jobs()
                time.sleep(max(args.record_loop_gap, 1.5))
                continue
            if args.once:
                post_pipeline_event(audio_server, audio_token, device_id, "manual_capture", "manual", "manual one-shot recording requested")
            elif args.record_loop:
                post_pipeline_event(audio_server, audio_token, device_id, "manual_capture", "running", "manual recording")
            else:
                if not manual_enabled:
                    cleanup_background_jobs()
                    time.sleep(max(args.record_loop_gap, 1.5))
                    continue
                post_pipeline_event(audio_server, audio_token, device_id, "manual_capture", "running", "manual recording")
            if args.record_loop and args.record_loop_async:
                post_pipeline_event(audio_server, audio_token, device_id, "recording", "running", "capturing local WAV with arecord")
                wav_path = record_wav(config.get("recorder", {}))
                post_pipeline_event(
                    audio_server,
                    audio_token,
                    device_id,
                    "recording",
                    "ok",
                    "WAV captured",
                    {"wav_path": str(wav_path), "async_asr": True},
                )
                cleanup_background_jobs()
                if len(background_jobs) >= max(args.max_background_jobs, 1):
                    post_pipeline_event(
                        audio_server,
                        audio_token,
                        device_id,
                        "model_asr",
                        "skipped",
                        "too many background ASR jobs running",
                        {"active_jobs": len(background_jobs), "wav_path": str(wav_path)},
                    )
                else:
                    job = threading.Thread(target=process_wav_background, args=(wav_path,), daemon=True)
                    job.start()
                    background_jobs.append(job)
            else:
                payload = process_once(config)
                print(json.dumps(payload, ensure_ascii=False), flush=True)
            if args.once:
                return 0
            if args.record_loop:
                time.sleep(max(args.record_loop_gap, 0.0))
        except Exception as exc:  # noqa: BLE001 - keep the edge listener alive and visible
            post_pipeline_event(audio_server, audio_token, device_id, "edge_pipeline", "failed", str(exc))
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
            if args.once:
                return 1
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
