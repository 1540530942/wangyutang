"""
Pi-side audio streamer for audio_interact.

Flow:
  PyAudio capture at 48 kHz -> Silero VAD -> collect speech segment
  -> downsample to 16 kHz -> build WAV -> WebSocket -> cloud ASR/wake/route.

Run:
  python3 edge_streamer.py --config config.json --loop
  python3 edge_streamer.py --once
  python3 edge_streamer.py --calibrate

The USB PnP Audio Device on the Pi usually supports 48 kHz natively.
Silero VAD runs at 16 kHz, so each chunk is resampled before scoring.
"""
from __future__ import annotations

import argparse
import audioop
import collections
import io
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Deque, Protocol

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


CAPTURE_RATE = 48000
ASR_RATE = 16000

DEFAULT_CONFIG: dict[str, Any] = {
    "device_index": 0,
    "capture_rate": CAPTURE_RATE,
    "asr_rate": ASR_RATE,
    "channels": 1,
    "chunk_ms": 32,
    "vad_mode": "silero",
    "silero_repo": "snakers4/silero-vad",
    "silero_model": "silero_vad",
    "silero_threshold": 0.55,
    "speech_start_chunks": 3,
    "speech_end_chunks": 22,
    "pre_speech_chunks": 10,
    "max_speech_seconds": 12,
    "energy_threshold": 400,
    "ws_url": "wss://www.wangyutang.cn/audio_interact/ws/audio",
    "device_id": "turbopi-01",
    "ws_timeout": 15,
    "result_timeout": 120,
}


class VadDetector(Protocol):
    name: str

    def score(self, frame: bytes) -> float:
        ...


class EnergyVadDetector:
    name = "energy"

    def __init__(self, threshold: float) -> None:
        self.threshold = threshold

    def score(self, frame: bytes) -> float:
        energy = chunk_rms(frame)
        if self.threshold <= 0:
            return 0.0
        return min(1.0, energy / self.threshold)


class SileroVadDetector:
    name = "silero"

    def __init__(self, *, capture_rate: int, asr_rate: int, channels: int, repo: str, model_name: str) -> None:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("Silero VAD requires torch. Install torch before running edge_streamer.py.") from exc

        self.torch = torch
        self.capture_rate = capture_rate
        self.asr_rate = asr_rate
        self.channels = channels
        self.resample_state: tuple[Any, ...] | None = None
        try:
            self.model, _utils = torch.hub.load(repo_or_dir=repo, model=model_name, trust_repo=True)
        except TypeError:
            self.model, _utils = torch.hub.load(repo_or_dir=repo, model=model_name)
        self.model.eval()

    def score(self, frame: bytes) -> float:
        pcm16, self.resample_state = audioop.ratecv(
            frame,
            2,
            self.channels,
            self.capture_rate,
            self.asr_rate,
            self.resample_state,
        )
        if not pcm16:
            return 0.0

        samples = [
            int.from_bytes(pcm16[index : index + 2], "little", signed=True) / 32768.0
            for index in range(0, len(pcm16), 2)
        ]
        if not samples:
            return 0.0

        tensor = self.torch.tensor(samples, dtype=self.torch.float32)
        with self.torch.no_grad():
            probability = self.model(tensor, self.asr_rate).item()
        return float(probability)


def load_config(path: Path) -> dict[str, Any]:
    if path.exists():
        return {**DEFAULT_CONFIG, **json.loads(path.read_text(encoding="utf-8"))}
    return dict(DEFAULT_CONFIG)


def chunk_rms(data: bytes) -> float:
    n = len(data) // 2
    if n == 0:
        return 0.0
    shorts = struct.unpack(f"<{n}h", data[: n * 2])
    return math.sqrt(sum(x * x for x in shorts) / n)


def resample_and_build_wav(frames: list[bytes], capture_rate: int, asr_rate: int, channels: int) -> bytes:
    raw = b"".join(frames)
    resampled, _ = audioop.ratecv(raw, 2, channels, capture_rate, asr_rate, None)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(asr_rate)
        wf.writeframes(resampled)
    return buf.getvalue()


def _play_wav_bytes(audio: bytes, cfg: dict[str, Any]) -> None:
    player = shutil.which("aplay") or shutil.which("paplay") or ""
    if not player:
        print("[WARN] tts_play: no audio player found (aplay/paplay)", flush=True)
        return
    device = str(cfg.get("tts_device") or cfg.get("voice_device") or "")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        handle.write(audio)
        temp_path = Path(handle.name)
    try:
        cmd = [player, "-q"]
        if device and Path(player).name == "aplay":
            cmd.extend(["-D", device])
        cmd.append(str(temp_path))
        subprocess.run(cmd, capture_output=True, timeout=30)
        print(f"[INFO] tts_played bytes={len(audio)}", flush=True)
    except Exception as exc:
        print(f"[WARN] tts_play failed: {exc}", flush=True)
    finally:
        temp_path.unlink(missing_ok=True)


def send_and_receive(wav_bytes: bytes, cfg: dict[str, Any]) -> dict[str, Any]:
    session_id = str(uuid.uuid4())[:12]
    ws = websocket.WebSocket()
    ws.connect(cfg["ws_url"], timeout=cfg["ws_timeout"])
    try:
        ws.send(
            json.dumps(
                {
                    "type": "start",
                    "session_id": session_id,
                    "device_id": cfg["device_id"],
                }
            )
        )
        ready = json.loads(ws.recv())
        if ready.get("type") != "ready":
            return {"error": f"unexpected handshake response: {ready}"}

        for offset in range(0, len(wav_bytes), 8192):
            ws.send_binary(wav_bytes[offset : offset + 8192])

        ws.send(json.dumps({"type": "end"}))
        ws.settimeout(cfg["result_timeout"])
        result = json.loads(ws.recv())

        # 接收流式 TTS 分块：每块是一句完整 WAV，空 bytes 表示结束
        tts_timeout = float(cfg.get("tts_timeout", 15))
        ws.settimeout(tts_timeout)
        while True:
            try:
                msg = ws.recv()
                if isinstance(msg, bytes):
                    if not msg:  # 空 bytes = 流式结束哨兵
                        break
                    _play_wav_bytes(msg, cfg)
            except Exception:
                break

        return result
    finally:
        ws.close()


def build_vad_detector(cfg: dict[str, Any], *, capture_rate: int, asr_rate: int, channels: int) -> VadDetector:
    mode = str(cfg.get("vad_mode") or "silero").lower()
    if mode == "energy":
        return EnergyVadDetector(float(cfg["energy_threshold"]))
    if mode != "silero":
        raise RuntimeError(f"unsupported vad_mode={mode!r}; use silero or energy")
    return SileroVadDetector(
        capture_rate=capture_rate,
        asr_rate=asr_rate,
        channels=channels,
        repo=str(cfg["silero_repo"]),
        model_name=str(cfg["silero_model"]),
    )


def vad_listen(cfg: dict[str, Any], *, once: bool = False) -> None:
    if not HAVE_PYAUDIO:
        sys.exit("[ERROR] pyaudio not installed: pip install pyaudio")
    if not HAVE_WS:
        sys.exit("[ERROR] websocket-client not installed: pip install websocket-client")

    capture_rate = int(cfg.get("capture_rate", CAPTURE_RATE))
    asr_rate = int(cfg.get("asr_rate", ASR_RATE))
    channels = int(cfg.get("channels", 1))
    chunk_ms = int(cfg.get("chunk_ms", 32))
    chunk_samples = capture_rate * chunk_ms // 1000

    detector = build_vad_detector(cfg, capture_rate=capture_rate, asr_rate=asr_rate, channels=channels)
    threshold = float(cfg["silero_threshold"] if detector.name == "silero" else 1.0)
    start_n = int(cfg["speech_start_chunks"])
    end_n = int(cfg["speech_end_chunks"])
    pre_n = int(cfg["pre_speech_chunks"])
    max_chunks = int(float(cfg["max_speech_seconds"]) * 1000 / chunk_ms)

    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=capture_rate,
        input=True,
        input_device_index=int(cfg["device_index"]),
        frames_per_buffer=chunk_samples,
    )

    pre_buf: Deque[bytes] = collections.deque(maxlen=pre_n)
    speech_buf: list[bytes] = []
    speech_n = 0
    silence_n = 0
    state = "idle"

    print(
        (
            f"[VAD] listening mode={detector.name} threshold={threshold:.2f} "
            f"chunk={chunk_ms}ms device={cfg['device_index']} "
            f"capture={capture_rate}Hz->asr={asr_rate}Hz"
        ),
        flush=True,
    )

    try:
        while True:
            frame = stream.read(chunk_samples, exception_on_overflow=False)
            probability = detector.score(frame)
            is_speech = probability >= threshold

            if state == "idle":
                pre_buf.append(frame)
                speech_n = speech_n + 1 if is_speech else 0
                if speech_n >= start_n:
                    state = "speaking"
                    speech_buf = list(pre_buf)
                    silence_n = 0
                    print(f"[VAD] speech started p={probability:.2f}", flush=True)
            else:
                speech_buf.append(frame)
                silence_n = 0 if is_speech else silence_n + 1

                hit_silence = silence_n >= end_n
                hit_max = len(speech_buf) >= max_chunks
                if hit_silence or hit_max:
                    dur_ms = len(speech_buf) * chunk_ms
                    reason = "silence" if hit_silence else "max_duration"
                    print(f"[VAD] speech ended reason={reason} duration={dur_ms}ms", flush=True)

                    wav = resample_and_build_wav(speech_buf, capture_rate, asr_rate, channels)
                    state = "idle"
                    speech_buf.clear()
                    pre_buf.clear()
                    speech_n = 0
                    silence_n = 0

                    print(f"[WS] sending {len(wav)} bytes to cloud ...", flush=True)
                    t0 = time.time()
                    try:
                        result = send_and_receive(wav, cfg)
                        elapsed = int((time.time() - t0) * 1000)
                        print(f"[OK] {elapsed}ms {json.dumps(result, ensure_ascii=False)}", flush=True)
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


def calibrate(cfg: dict[str, Any], seconds: int = 5) -> None:
    """Print ambient RMS for energy fallback tuning."""
    if not HAVE_PYAUDIO:
        sys.exit("[ERROR] pyaudio not installed")

    capture_rate = int(cfg.get("capture_rate", CAPTURE_RATE))
    channels = int(cfg.get("channels", 1))
    chunk_ms = int(cfg.get("chunk_ms", 32))
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

    print(f"[CAL] measuring ambient RMS for {seconds}s; stay quiet ...", flush=True)
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
    print(f"[CAL] ambient avg={avg:.1f} peak={peak:.1f} suggested_energy_threshold={suggested}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audio Interact edge Silero VAD streamer")
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config.json")
    parser.add_argument("--once", action="store_true", help="Record one speech segment then exit")
    parser.add_argument("--loop", action="store_true", help="Run continuously (default)")
    parser.add_argument("--calibrate", action="store_true", help="Measure ambient noise for energy fallback")
    parser.add_argument("--threshold", type=float, help="Override silero_threshold")
    parser.add_argument("--energy-threshold", type=int, help="Override energy_threshold")
    parser.add_argument("--vad-mode", choices=["silero", "energy"], help="Override vad_mode")
    parser.add_argument("--url", type=str, help="Override ws_url")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.threshold is not None:
        cfg["silero_threshold"] = args.threshold
    if args.energy_threshold is not None:
        cfg["energy_threshold"] = args.energy_threshold
    if args.vad_mode:
        cfg["vad_mode"] = args.vad_mode
    if args.url:
        cfg["ws_url"] = args.url

    if args.calibrate:
        calibrate(cfg)
        return

    vad_listen(cfg, once=args.once)


if __name__ == "__main__":
    main()
