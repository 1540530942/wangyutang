"""WonderEchoPro Pi-side listener — WebSocket streaming mode.

Flow (default):
  poll /api/settings  ->  when manual_recording_enabled + input_mode==wonderechopro
  ->  arecord raw PCM16 pipe  ->  WebSocket /ws/audio (512-sample chunks, 16 kHz)
  ->  server Silero VAD cuts sentences  ->  ASR -> wake -> robot_sandbox -> TTS
  ->  binary TTS WAV frames back  ->  aplay local speaker

The streaming path is identical to the browser web-mode path; both end up in
the same /ws/audio pipeline.

Legacy fallback (--segment flag):
  Fixed-length 4-second WAV upload via POST /api/audio/segment.

Requirements: websockets>=10 (pip install websockets)

Run:
  python3 wonderecho_listener.py --config config.json
  python3 wonderecho_listener.py --config config.json --segment   # legacy
"""
from __future__ import annotations

import argparse
import asyncio
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

DEFAULT_CONFIG = SCRIPT_DIR / "config.example.json"
CHUNK_SAMPLES = 512
CHUNK_BYTES = CHUNK_SAMPLES * 2  # PCM16 mono
SETTINGS_POLL_INTERVAL = 3.0
_FINAL_STATUS_SKIP = {"completed", "done", "emergency_stop", "dry_run", "rejected", ""}


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib-only, used for settings poll + legacy segment mode)
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


def get_cloud_settings(server: str, token: str) -> dict[str, Any]:
    result = _get_json(f"{server.rstrip('/')}/api/settings", token)
    if result is None:
        return {"manual_recording_enabled": False, "input_mode": "wonderechopro"}
    return result.get("settings", result) if isinstance(result, dict) else {"manual_recording_enabled": False}


# ---------------------------------------------------------------------------
# TTS local playback
# ---------------------------------------------------------------------------

def _play_tts_bytes(wav_bytes: bytes, device: str = "") -> None:
    player = shutil.which("aplay") or shutil.which("paplay") or ""
    if not player:
        print("[WARN] tts_play: no audio player (aplay/paplay)", flush=True)
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        fh.write(wav_bytes)
        tmp_path = Path(fh.name)
    try:
        cmd = [player, "-q"]
        if device and Path(player).name == "aplay":
            cmd.extend(["-D", device])
        cmd.append(str(tmp_path))
        subprocess.run(cmd, capture_output=True, timeout=15)
    finally:
        tmp_path.unlink(missing_ok=True)


def _play_tts_base64(b64: str, device: str = "") -> None:
    _play_tts_bytes(base64.b64decode(b64), device)


# ---------------------------------------------------------------------------
# WebSocket streaming session
# ---------------------------------------------------------------------------

def _ws_url(server: str) -> str:
    """Convert http(s) server URL to ws(s) WebSocket URL for /ws/audio."""
    s = server.rstrip("/")
    if s.startswith("https://"):
        return s.replace("https://", "wss://", 1) + "/ws/audio"
    if s.startswith("http://"):
        return s.replace("http://", "ws://", 1) + "/ws/audio"
    return f"wss://{s}/ws/audio"


async def _run_ws_session(config: dict[str, Any]) -> None:
    """Open one WebSocket session: stream arecord PCM until manual_recording_enabled turns off."""
    try:
        import websockets  # type: ignore
    except ImportError:
        print("[ERROR] websockets not installed. Run: pip install websockets", flush=True)
        return

    server = str(config.get("server") or "")
    token = str(config.get("token") or "")
    device_id = str(config.get("device_id") or "turbopi-01")
    tts_device = str(config.get("tts_device") or "")
    recorder_cfg = config.get("recorder", {})
    alsa_device = str(recorder_cfg.get("device") or "")

    ws_url = _ws_url(server)
    session_id = f"pi-{int(time.time() * 1000)}"

    arecord_cmd = ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"]
    if alsa_device:
        arecord_cmd += ["-D", alsa_device]
    arecord_cmd.append("-")

    extra_headers = {}
    if token:
        extra_headers["X-Audio-Token"] = token

    print(f"[INFO] WS connect {ws_url} session={session_id}", flush=True)

    async with websockets.connect(ws_url, additional_headers=extra_headers, ping_interval=20, ping_timeout=30) as ws:
        await ws.send(json.dumps({
            "type": "start_stream",
            "session_id": session_id,
            "device_id": device_id,
            "sample_rate": 16000,
            "route": True,
        }))

        # wait for stream_ready
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
            if isinstance(msg, str):
                data = json.loads(msg)
                if data.get("type") == "stream_ready":
                    print("[INFO] stream_ready — streaming audio", flush=True)
                    break

        proc = await asyncio.create_subprocess_exec(
            *arecord_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async def _recv_loop() -> None:
            async for msg in ws:
                if isinstance(msg, bytes) and len(msg) > 0:
                    threading.Thread(target=_play_tts_bytes, args=(msg, tts_device), daemon=True).start()
                elif isinstance(msg, str):
                    try:
                        ev = json.loads(msg)
                    except json.JSONDecodeError:
                        continue
                    t = ev.get("type", "")
                    if t == "result":
                        text = ev.get("text") or ""
                        tts_text = ev.get("tts_text") or ""
                        wake = ev.get("wake_status") or "—"
                        print(json.dumps({"text": text, "tts_text": tts_text, "wake": wake}, ensure_ascii=False), flush=True)
                    elif t in ("speech_start", "speech_end", "vad", "asr_started"):
                        pass  # low-noise events
                    elif t == "stream_stopped":
                        break

        recv_task = asyncio.create_task(_recv_loop())

        try:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(CHUNK_BYTES)
                if not chunk:
                    break
                if len(chunk) < CHUNK_BYTES:
                    chunk = chunk + b"\x00" * (CHUNK_BYTES - len(chunk))
                await ws.send(chunk)
        finally:
            proc.kill()
            await proc.wait()
            try:
                await ws.send(json.dumps({"type": "stop_stream"}))
                await asyncio.sleep(0.3)
            except Exception:
                pass
            recv_task.cancel()
            try:
                await recv_task
            except (asyncio.CancelledError, Exception):
                pass

    print("[INFO] WS session ended", flush=True)


# ---------------------------------------------------------------------------
# Settings-aware main loop
# ---------------------------------------------------------------------------

async def _ws_main(config: dict[str, Any]) -> None:
    server = str(config.get("server") or "")
    token = str(config.get("token") or "")
    session_task: asyncio.Task[None] | None = None

    while True:
        try:
            settings = get_cloud_settings(server, token)
            input_mode = str(settings.get("input_mode") or "wonderechopro")
            manual_enabled = bool(settings.get("manual_recording_enabled"))

            should_run = input_mode == "wonderechopro" and manual_enabled

            if should_run and (session_task is None or session_task.done()):
                print("[INFO] starting WS session (manual_recording_enabled=true)", flush=True)
                session_task = asyncio.create_task(_run_ws_session(config))

            if not should_run and session_task is not None and not session_task.done():
                print("[INFO] stopping WS session (manual_recording_enabled=false)", flush=True)
                session_task.cancel()
                try:
                    await session_task
                except (asyncio.CancelledError, Exception):
                    pass
                session_task = None

        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] settings poll failed: {exc}", flush=True)

        await asyncio.sleep(SETTINGS_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Legacy segment mode (--segment flag)
# ---------------------------------------------------------------------------

def _post_segment(server: str, token: str, wav_path: Path, device_id: str) -> dict[str, Any] | None:
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
    headers: dict[str, str] = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    if token:
        headers["X-Audio-Token"] = token
    req = urllib.request.Request(
        f"{server.rstrip('/')}/api/audio/segment", data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"[WARN] segment upload failed: {exc}", flush=True)
        return None


def _segment_loop(config: dict[str, Any], once: bool, loop_gap: float) -> int:
    from recorder import record_wav  # noqa: PLC0415

    server = str(config.get("server") or "")
    token = str(config.get("token") or "")
    device_id = str(config.get("device_id") or "turbopi-01")
    tts_device = str(config.get("tts_device") or "")

    while True:
        try:
            settings = get_cloud_settings(server, token)
            if not once and not settings.get("manual_recording_enabled"):
                time.sleep(max(loop_gap, 1.5))
                continue
            wav_path = record_wav(config.get("recorder", {}))
            result = _post_segment(server, token, wav_path, device_id)
            tts_b64 = str((result or {}).get("tts_audio_base64") or "")
            tts_text = str((result or {}).get("tts_text") or "")
            if tts_b64 and tts_text not in _FINAL_STATUS_SKIP:
                threading.Thread(target=_play_tts_base64, args=(tts_b64, tts_device), daemon=True).start()
            print(json.dumps(result or {}, ensure_ascii=False), flush=True)
            if once:
                return 0
            time.sleep(max(loop_gap, 0.0))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
            if once:
                return 1
            time.sleep(1.0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="WonderEchoPro Pi-side listener.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--segment", action="store_true", help="Legacy: fixed 4s WAV upload mode.")
    parser.add_argument("--once", action="store_true", help="Legacy segment mode: record once and exit.")
    parser.add_argument("--loop-gap", type=float, default=0.5, help="Legacy: seconds between poll cycles.")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"[ERROR] config not found: {args.config}", flush=True)
        return 1

    config = json.loads(args.config.read_text(encoding="utf-8"))

    if args.segment:
        return _segment_loop(config, once=args.once, loop_gap=args.loop_gap)

    asyncio.run(_ws_main(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
