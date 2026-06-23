"""
Pi-side VAD streamer for audio_interact.

Flow:
  PyAudio capture (48 kHz) → energy VAD → collect speech segment
  → downsample to 16 kHz → build WAV → WebSocket → cloud ASR+route → print result

Run:
  python3 edge_streamer.py [--config config.json] [--loop] [--once] [--calibrate]

The USB PnP Audio Device on the Pi only supports 48 kHz natively.
We capture at 48 kHz, do VAD on raw energy, then downsample to 16 kHz for ASR.
"""
from __future__ import annotations

import argparse
import audioop
import io
import json
import math
import struct
import sys
import time
import uuid
import wave
from pathlib import Path
from typing import Any

HAVE_PYAUDIO = False
HAVE_WS = False

try:
    import pyaudio
    HAVE_PYAUDIO = True
except ImportError:
    pass

try:
    import websocket
    HAVE_WS = True
except ImportError:
    pass


CAPTURE_RATE = 48000   # hardware native rate
ASR_RATE = 16000       # rate expected by ASR API
RESAMPLE_RATIO = CAPTURE_RATE // ASR_RATE  # = 3

DEFAULT_CONFIG: dict[str, Any] = {
    "device_index": 0,
    "capture_rate": CAPTURE_RATE,
    "channels": 1,
    "chunk_ms": 20,           # VAD granularity in ms (20 ms = 960 samples @ 48 kHz)
    "energy_threshold": 400,  # RMS threshold; tune with --calibrate in your environment
    "speech_start_chunks": 5, # 5 × 20 ms = 100 ms of loud audio → speech started
    "speech_end_chunks": 40,  # 40 × 20 ms = 800 ms of silence → speech ended
    "pre_speech_chunks": 15,  # 15 × 20 ms = 300 ms pre-buffer kept before trigger
    "max_speech_seconds": 12,
    "ws_url": "wss://www.wangyutang.cn/interact/ws/audio",
    "device_id": "turbopi-01",
    "ws_timeout": 15,
    "result_timeout": 120,
}


# ── helpers ──────────────────────────────────────────────────────────────────

def load_config(path: Path) -> dict[str, Any]:
    if path.exists():
        return {**DEFAULT_CONFIG, **json.loads(path.read_text(encoding="utf-8"))}
    return dict(DEFAULT_CONFIG)


def chunk_rms(data: bytes) -> float:
    n = len(data) // 2
    if n == 0:
        return 0.0
    shorts = struct.unpack(f"<{n}h", data[:n * 2])
    return math.sqrt(sum(x * x for x in shorts) / n)


def resample_and_build_wav(frames: list[bytes], capture_rate: int, asr_rate: int, channels: int) -> bytes:
    raw = b"".join(frames)
    # Downsample via audioop linear interpolation
    resampled, _ = audioop.ratecv(raw, 2, channels, capture_rate, asr_rate, None)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(asr_rate)
        wf.writeframes(resampled)
    return buf.getvalue()


# ── WebSocket send / receive ─────────────────────────────────────────────────

def send_and_receive(wav_bytes: bytes, cfg: dict[str, Any]) -> dict[str, Any]:
    session_id = str(uuid.uuid4())[:12]
    ws = websocket.WebSocket()
    ws.connect(cfg["ws_url"], timeout=cfg["ws_timeout"])
    try:
        ws.send(json.dumps({
            "type": "start",
            "session_id": session_id,
            "device_id": cfg["device_id"],
        }))
        ready = json.loads(ws.recv())
        if ready.get("type") != "ready":
            return {"error": f"unexpected handshake response: {ready}"}

        for i in range(0, len(wav_bytes), 8192):
            ws.send_binary(wav_bytes[i: i + 8192])

        ws.send(json.dumps({"type": "end"}))
        ws.settimeout(cfg["result_timeout"])
        return json.loads(ws.recv())
    finally:
        ws.close()


# ── VAD listen loop ───────────────────────────────────────────────────────────

def vad_listen(cfg: dict[str, Any], *, once: bool = False) -> None:
    if not HAVE_PYAUDIO:
        sys.exit("[ERROR] pyaudio not installed: pip install pyaudio")
    if not HAVE_WS:
        sys.exit("[ERROR] websocket-client not installed: pip install websocket-client")

    capture_rate = int(cfg.get("capture_rate", CAPTURE_RATE))
    asr_rate = int(cfg.get("asr_rate", ASR_RATE))
    channels = int(cfg.get("channels", 1))
    chunk_ms = int(cfg.get("chunk_ms", 20))
    chunk_samples = capture_rate * chunk_ms // 1000  # e.g. 960 @ 48 kHz

    threshold = int(cfg["energy_threshold"])
    start_n = int(cfg["speech_start_chunks"])
    end_n = int(cfg["speech_end_chunks"])
    pre_n = int(cfg["pre_speech_chunks"])
    max_chunks = int(cfg["max_speech_seconds"] * 1000 / chunk_ms)

    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=capture_rate,
        input=True,
        input_device_index=int(cfg["device_index"]),
        frames_per_buffer=chunk_samples,
    )

    pre_buf: list[bytes] = []
    speech_buf: list[bytes] = []
    loud_n = 0
    quiet_n = 0
    state = "idle"

    print(
        f"[VAD] listening  threshold={threshold}  chunk={chunk_ms}ms  "
        f"device={cfg['device_index']}  capture={capture_rate}Hz→{asr_rate}Hz",
        flush=True,
    )

    try:
        while True:
            frame = stream.read(chunk_samples, exception_on_overflow=False)
            energy = chunk_rms(frame)

            if state == "idle":
                pre_buf.append(frame)
                if len(pre_buf) > pre_n:
                    pre_buf.pop(0)
                if energy > threshold:
                    loud_n += 1
                else:
                    loud_n = 0
                if loud_n >= start_n:
                    state = "speaking"
                    speech_buf = list(pre_buf)
                    quiet_n = 0
                    print("[VAD] speech started", flush=True)

            else:  # speaking
                speech_buf.append(frame)
                if energy < threshold:
                    quiet_n += 1
                else:
                    quiet_n = 0

                hit_silence = quiet_n >= end_n
                hit_max = len(speech_buf) >= max_chunks

                if hit_silence or hit_max:
                    dur_ms = len(speech_buf) * chunk_ms
                    reason = "silence" if hit_silence else "max_duration"
                    print(f"[VAD] speech ended  reason={reason}  duration={dur_ms}ms", flush=True)

                    wav = resample_and_build_wav(speech_buf, capture_rate, asr_rate, channels)
                    state = "idle"
                    speech_buf.clear()
                    pre_buf.clear()
                    loud_n = 0
                    quiet_n = 0

                    print(f"[WS]  sending {len(wav)} bytes → cloud ...", flush=True)
                    t0 = time.time()
                    try:
                        result = send_and_receive(wav, cfg)
                        elapsed = int((time.time() - t0) * 1000)
                        print(f"[OK]  {elapsed}ms  {json.dumps(result, ensure_ascii=False)}", flush=True)
                    except Exception as exc:
                        print(f"[ERR] {exc}", flush=True)

                    if once:
                        return
                    print("[VAD] listening ...", flush=True)

    except KeyboardInterrupt:
        print("\n[VAD] stopped", flush=True)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


# ── calibration helper ────────────────────────────────────────────────────────

def calibrate(cfg: dict[str, Any], seconds: int = 5) -> None:
    """Print ambient RMS for `seconds` seconds — do NOT speak during this."""
    if not HAVE_PYAUDIO:
        sys.exit("[ERROR] pyaudio not installed")

    capture_rate = int(cfg.get("capture_rate", CAPTURE_RATE))
    channels = int(cfg.get("channels", 1))
    chunk_ms = int(cfg.get("chunk_ms", 20))
    chunk_samples = capture_rate * chunk_ms // 1000

    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=capture_rate,
        input=True,
        input_device_index=int(cfg["device_index"]),
        frames_per_buffer=chunk_samples,
    )

    print(f"[CAL] measuring ambient noise for {seconds}s — stay quiet ...", flush=True)
    rms_vals: list[float] = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        frame = stream.read(chunk_samples, exception_on_overflow=False)
        rms_vals.append(chunk_rms(frame))

    stream.stop_stream()
    stream.close()
    pa.terminate()

    avg = sum(rms_vals) / len(rms_vals)
    peak = max(rms_vals)
    suggested = int(peak * 2.5)
    print(
        f"[CAL] ambient  avg={avg:.1f}  peak={peak:.1f}  "
        f"suggested_threshold={suggested}",
        flush=True,
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Audio Interact edge VAD streamer")
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config.json")
    parser.add_argument("--once", action="store_true", help="Record one speech segment then exit")
    parser.add_argument("--loop", action="store_true", help="Run continuously (default)")
    parser.add_argument("--calibrate", action="store_true", help="Measure ambient noise and suggest threshold")
    parser.add_argument("--threshold", type=int, help="Override energy_threshold")
    parser.add_argument("--url", type=str, help="Override ws_url")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.threshold:
        cfg["energy_threshold"] = args.threshold
    if args.url:
        cfg["ws_url"] = args.url

    if args.calibrate:
        calibrate(cfg)
        return

    vad_listen(cfg, once=args.once)


if __name__ == "__main__":
    main()
