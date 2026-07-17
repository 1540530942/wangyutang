"""WonderEchoPro Pi-side listener — WebSocket streaming mode.

Flow:
  poll /api/settings  ->  when manual_recording_enabled + input_mode==wonderechopro
  ->  arecord raw PCM16 pipe  ->  WebSocket /ws/audio (512-sample chunks, 16 kHz)
  ->  server Silero VAD cuts sentences  ->  ASR -> wake -> robot_sandbox -> TTS
  ->  binary TTS WAV frames back  ->  aplay local speaker

Identical pipeline to browser web-mode.

Requirements: websockets>=10 (pip install websockets)

Run:
  python3 wonderecho_listener.py --config config.json
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave as wavemod
from pathlib import Path
from typing import Any

try:
    from speexdsp import EchoCanceller as _SpeexEC
    _SPEEX_AVAILABLE = True
except ImportError:
    _SpeexEC = None  # type: ignore
    _SPEEX_AVAILABLE = False

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

DEFAULT_CONFIG = SCRIPT_DIR / "config.example.json"
CHUNK_SAMPLES = 512
CHUNK_BYTES = CHUNK_SAMPLES * 2  # PCM16 mono
SETTINGS_POLL_INTERVAL = 3.0

_AEC_FILTER_LENGTH = 4096   # 256 ms at 16 kHz — covers room echo tail
_AEC_TAIL_FRAMES   = 13     # ~0.8 s of silence after aplay ends


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib-only, used for settings poll)
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

def _wav_to_pcm_frames(wav_bytes: bytes) -> list[bytes]:
    """Strip WAV header and split PCM into CHUNK_BYTES frames."""
    try:
        with wavemod.open(io.BytesIO(wav_bytes)) as wf:
            pcm = wf.readframes(wf.getnframes())
    except Exception:
        pcm = wav_bytes[44:] if len(wav_bytes) > 44 else b""
    frames: list[bytes] = []
    for i in range(0, len(pcm), CHUNK_BYTES):
        f = pcm[i:i + CHUNK_BYTES]
        if len(f) < CHUNK_BYTES:
            f = f + b"\x00" * (CHUNK_BYTES - len(f))
        frames.append(f)
    return frames


class _AECContext:
    """Per-session Acoustic Echo Canceller + TTS playback controller."""

    def __init__(self, sample_rate: int = 16000) -> None:
        self._sample_rate = sample_rate
        self._frame_secs = CHUNK_SAMPLES / sample_rate
        self._ref_q: queue.Queue[bytes] = queue.Queue(maxsize=600)
        self._play_proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        # speexdsp AEC (None → fallback to mute-during-TTS)
        if _SPEEX_AVAILABLE:
            self._ec = _SpeexEC.create(CHUNK_SAMPLES, _AEC_FILTER_LENGTH, sample_rate)
        else:
            self._ec = None
        self._mute_until: float = 0.0

    def process(self, mic_frame: bytes) -> bytes:
        """Return AEC-cleaned frame (or silence during TTS when no speexdsp)."""
        proc = self._play_proc
        playing = proc is not None and proc.poll() is None
        if self._ec is not None:
            if playing:
                # Only apply AEC while TTS is actively playing — avoids filtering speech
                try:
                    ref = self._ref_q.get_nowait()
                except queue.Empty:
                    ref = b"\x00" * CHUNK_BYTES
                return bytes(self._ec.process(mic_frame, ref))
            # Brief tail mute after TTS ends to let AEC settle
            if time.time() < self._mute_until:
                return b"\x00" * CHUNK_BYTES
            return mic_frame
        # No speexdsp: mute during and briefly after TTS
        if time.time() < self._mute_until:
            return b"\x00" * CHUNK_BYTES
        return mic_frame

    def play_tts(self, wav_bytes: bytes, device: str) -> None:
        """Play TTS audio; feed it as AEC reference in lock-step. Blocking — call in thread."""
        player = shutil.which("aplay") or shutil.which("paplay") or ""
        if not player:
            print("[WARN] tts_play: no audio player", flush=True)
            return
        frames = _wav_to_pcm_frames(wav_bytes)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            fh.write(wav_bytes)
            tmp_path = Path(fh.name)
        cmd = [player, "-q"]
        if device and Path(player).name == "aplay":
            cmd.extend(["-D", device])
        cmd.append(str(tmp_path))
        try:
            self._play_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if self._ec is not None:
                # Feed reference frames at real-time pace so AEC stays in sync
                for frame in frames:
                    if self._play_proc.poll() is not None:
                        break  # interrupted
                    try:
                        self._ref_q.put(frame, timeout=0.5)
                    except queue.Full:
                        pass
                    time.sleep(self._frame_secs)
                # Tail silence for echo decay
                for _ in range(_AEC_TAIL_FRAMES):
                    try:
                        self._ref_q.put(b"\x00" * CHUNK_BYTES, timeout=0.1)
                    except queue.Full:
                        break
            else:
                self._mute_until = time.time() + 60  # mute fallback
            try:
                self._play_proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._play_proc.kill()
        finally:
            self._mute_until = time.time() + 0.8  # brief tail silence after TTS ends
            self._play_proc = None
            tmp_path.unlink(missing_ok=True)

    def interrupt(self) -> None:
        """Kill ongoing TTS playback when user starts speaking."""
        proc = self._play_proc
        if proc and proc.poll() is None:
            print("[INFO] TTS interrupted by user speech", flush=True)
            proc.kill()
        # Drain stale reference frames so next user speech isn't filtered
        while not self._ref_q.empty():
            try:
                self._ref_q.get_nowait()
            except queue.Empty:
                break
        self._mute_until = 0.0


# ---------------------------------------------------------------------------
# WebSocket streaming session
# ---------------------------------------------------------------------------

def _ws_url(server: str) -> str:
    s = server.rstrip("/")
    if s.startswith("https://"):
        return s.replace("https://", "wss://", 1) + "/ws/audio"
    if s.startswith("http://"):
        return s.replace("http://", "ws://", 1) + "/ws/audio"
    return f"wss://{s}/ws/audio"


async def _run_ws_session(config: dict[str, Any]) -> None:
    try:
        import websockets  # type: ignore
    except ImportError:
        print("[ERROR] websockets not installed. Run: pip install websockets", flush=True)
        return

    server = str(config.get("server") or "")
    token = str(config.get("token") or "")
    device_id = str(config.get("device_id") or "turbopi-01")
    tts_device = str(config.get("tts_device") or "")
    alsa_device = str((config.get("recorder") or {}).get("device") or "")

    ws_url = _ws_url(server)
    session_id = f"pi-{int(time.time() * 1000)}"

    arecord_cmd = ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"]
    if alsa_device:
        arecord_cmd += ["-D", alsa_device]
    arecord_cmd.append("-")

    extra_headers = {"X-Audio-Token": token} if token else {}

    print(f"[INFO] WS connect {ws_url} session={session_id}", flush=True)

    aec_ctx = _AECContext()

    async with websockets.connect(ws_url, additional_headers=extra_headers, ping_interval=None) as ws:
        await ws.send(json.dumps({
            "type": "start_stream",
            "session_id": session_id,
            "device_id": device_id,
            "sample_rate": 16000,
            "route": True,
        }))

        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
            if isinstance(msg, str) and json.loads(msg).get("type") == "stream_ready":
                print("[INFO] stream_ready — streaming audio", flush=True)
                break

        proc = await asyncio.create_subprocess_exec(
            *arecord_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async def _recv_loop() -> None:
            last_turn_had_b64 = False
            async for msg in ws:
                if isinstance(msg, bytes) and len(msg) > 0:
                    # Skip binary frame if tts_audio_base64 already handled for this turn
                    if not last_turn_had_b64:
                        threading.Thread(target=aec_ctx.play_tts, args=(msg, tts_device), daemon=True).start()
                    last_turn_had_b64 = False
                elif isinstance(msg, str):
                    try:
                        ev = json.loads(msg)
                    except json.JSONDecodeError:
                        continue
                    ev_type = ev.get("type")
                    if ev_type == "speech_start":
                        aec_ctx.interrupt()
                    elif ev_type == "result":
                        print(json.dumps({
                            "text": ev.get("text") or "",
                            "tts_text": ev.get("tts_text") or "",
                            "wake": ev.get("wake_status") or "—",
                        }, ensure_ascii=False), flush=True)
                        tts_b64 = ev.get("tts_audio_base64")
                        if tts_b64:
                            try:
                                tts_wav = base64.b64decode(tts_b64)
                                threading.Thread(target=aec_ctx.play_tts, args=(tts_wav, tts_device), daemon=True).start()
                                last_turn_had_b64 = True
                            except Exception as exc:
                                print(f"[WARN] tts_play_failed: {exc}", flush=True)
                        else:
                            last_turn_had_b64 = False
                    elif ev_type == "stream_stopped":
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
                chunk = aec_ctx.process(chunk)
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
            should_run = (
                str(settings.get("input_mode") or "wonderechopro") == "wonderechopro"
                and bool(settings.get("manual_recording_enabled"))
            )

            if should_run and (session_task is None or session_task.done()):
                print("[INFO] starting WS session", flush=True)
                session_task = asyncio.create_task(_run_ws_session(config))

            if not should_run and session_task is not None and not session_task.done():
                print("[INFO] stopping WS session", flush=True)
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
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="WonderEchoPro Pi-side listener.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    if not args.config.exists():
        print(f"[ERROR] config not found: {args.config}", flush=True)
        return 1

    config = json.loads(args.config.read_text(encoding="utf-8"))
    asyncio.run(_ws_main(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
