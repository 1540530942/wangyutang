#!/usr/bin/env python3
"""Hardware-AEC probe for WonderEchoPro (docs/wonderecho-fullduplex-bargein-plan.md §6).

Answers one question: does the WonderEchoPro module cancel its own speaker echo
before the mic reaches us? If yes, the software AEC branch in wonderecho_listener.py
can be removed (real-vehicle optimum). If no, keep the speexdsp path.

How it works (stdlib only — no numpy/torch/network):
  1. Record ambient/noise floor for a few seconds (mic only, no playback).
     Run the motors during this window if you also want a DUCK threshold estimate.
  2. Play a broadband white-noise test tone through the speaker while recording the
     mic. White noise excites every frequency, so any echo path shows up cleanly.
  3. Compare mic RMS during playback vs the silent floor:
        echo_ratio = playback_rms / floor_rms      (2x == +6 dB)
        < 2x  (<6 dB residual)  -> hardware AEC present  -> can drop software AEC
        >= 2x                    -> echo present          -> keep software AEC

Usage on the Pi (adjust device names from `aplay -l` / `arecord -l`):
  python3 aec_probe.py \
      --play-device   "plughw:CARD=Device,DEV=0" \
      --capture-device "plughw:CARD=Device,DEV=0"

  # supply a real TTS sample instead of white noise:
  python3 aec_probe.py --wav /tmp/tts_sample.wav --play-device ... --capture-device ...
"""
from __future__ import annotations

import argparse
import array
import math
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

RATE = 16000


def _need(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        print(f"[ERROR] '{binary}' not found on PATH — install alsa-utils.", flush=True)
        sys.exit(2)
    return path


def _gen_white_noise_wav(path: Path, seconds: float, amp: int, rate: int = RATE) -> None:
    """Deterministic broadband test signal (LCG so no numpy needed)."""
    n = int(seconds * rate)
    samples = array.array("h", bytes(2 * n))
    seed = 0x2545F491
    for i in range(n):
        seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
        # map to [-amp, amp]
        samples[i] = int((seed / 0x7FFFFFFF * 2.0 - 1.0) * amp)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())


def _record(capture_device: str, seconds: float, out: Path, rate: int = RATE) -> None:
    cmd = [
        _need("arecord"), "-q", "-f", "S16_LE", "-r", str(rate), "-c", "1",
        "-t", "wav", "-d", str(int(math.ceil(seconds))),
    ]
    if capture_device:
        cmd += ["-D", capture_device]
    cmd.append(str(out))
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _rms(path: Path) -> float:
    """RMS over the middle 80% of the recording (drop start/stop transients)."""
    with wave.open(str(path), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    n = len(a)
    if n == 0:
        return 0.0
    lo, hi = int(n * 0.1), int(n * 0.9)
    window = a[lo:hi] if hi > lo else a
    return (sum(v * v for v in window) / len(window)) ** 0.5


def main() -> int:
    ap = argparse.ArgumentParser(description="WonderEchoPro hardware-AEC probe.")
    ap.add_argument("--play-device", default="", help="ALSA playback device (aplay -D)")
    ap.add_argument("--capture-device", default="", help="ALSA capture device (arecord -D)")
    ap.add_argument("--wav", type=Path, default=None, help="test WAV to play (default: white noise)")
    ap.add_argument("--seconds", type=float, default=4.0, help="measurement duration per phase")
    ap.add_argument("--amp", type=int, default=8000, help="white-noise amplitude (of 32767)")
    ap.add_argument("--rate", type=int, default=RATE)
    args = ap.parse_args()

    aplay = _need("aplay")
    _need("arecord")

    print("=== Available devices ===", flush=True)
    for tool in ("aplay", "arecord"):
        try:
            out = subprocess.run([shutil.which(tool), "-l"], capture_output=True, text=True, timeout=5).stdout
            print(f"--- {tool} -l ---\n{out.strip()}", flush=True)
        except Exception:
            pass

    tmp = Path(tempfile.mkdtemp(prefix="aec_probe_"))
    test_wav = args.wav if args.wav else tmp / "test_tone.wav"
    if not args.wav:
        _gen_white_noise_wav(test_wav, args.seconds, args.amp, args.rate)
        print(f"\n[INFO] generated white-noise test tone: {test_wav} ({args.seconds:.1f}s)", flush=True)
    else:
        print(f"\n[INFO] using supplied WAV: {test_wav}", flush=True)

    # ---- Phase 1: ambient / noise floor ----
    print("\n=== Phase 1/2: recording SILENCE floor ===", flush=True)
    print("    Keep quiet (or run motors now to also estimate the DUCK threshold).", flush=True)
    for i in (3, 2, 1):
        print(f"    starting in {i}...", flush=True)
        time.sleep(1)
    floor_wav = tmp / "floor.wav"
    _record(args.capture_device, args.seconds, floor_wav, args.rate)
    floor_rms = _rms(floor_wav)
    print(f"    floor RMS = {floor_rms:.1f}", flush=True)

    # ---- Phase 2: playback + record (echo path) ----
    print("\n=== Phase 2/2: PLAYBACK + record (measuring echo) ===", flush=True)
    print("    Playing the test tone through the speaker while recording the mic...", flush=True)
    play_cmd = [aplay, "-q"]
    if args.play_device:
        play_cmd += ["-D", args.play_device]
    play_cmd.append(str(test_wav))
    player = subprocess.Popen(play_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.2)  # let playback ramp up
    echo_wav = tmp / "echo.wav"
    _record(args.capture_device, args.seconds, echo_wav, args.rate)
    try:
        player.wait(timeout=2)
    except subprocess.TimeoutExpired:
        player.kill()
    echo_rms = _rms(echo_wav)
    print(f"    playback-window RMS = {echo_rms:.1f}", flush=True)

    # ---- Verdict ----
    floor = max(floor_rms, 1.0)
    ratio = echo_rms / floor
    db = 20 * math.log10(ratio) if ratio > 0 else -99.0
    print("\n================= RESULT =================", flush=True)
    print(f"  floor RMS      : {floor_rms:.1f}", flush=True)
    print(f"  playback RMS   : {echo_rms:.1f}", flush=True)
    print(f"  echo ratio     : {ratio:.2f}x  ({db:+.1f} dB)", flush=True)
    if ratio < 2.0:
        print("  VERDICT: HARDWARE AEC PRESENT (residual echo < 6 dB).", flush=True)
        print("           -> You can drop the software AEC branch in _AudioEngine", flush=True)
        print("              (make process() pass mic through when playing).", flush=True)
    else:
        print("  VERDICT: ECHO PRESENT — no/weak hardware AEC.", flush=True)
        print("           -> Keep the speexdsp software AEC path (current default).", flush=True)

    # DUCK threshold suggestion: sit safely above the (possibly motor-laden) floor.
    suggest = max(300, int(floor_rms * 3))
    print("\n  DUCK_RMS_THRESHOLD suggestion (if motors ran during Phase 1):", flush=True)
    print(f"           ~{suggest}  (3x floor; current default 800)", flush=True)
    print("==========================================", flush=True)
    print(f"\n[INFO] raw recordings kept in {tmp} (floor.wav / echo.wav / test_tone.wav)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
