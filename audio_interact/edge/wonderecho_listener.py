"""WonderEchoPro Pi-side listener — full-duplex WebSocket streaming.

Flow:
  poll /api/settings  ->  when manual_recording_enabled + input_mode==wonderechopro
  ->  arecord raw PCM16 pipe  ->  AEC(speexdsp) ->  WS /ws/audio (512-sample, 16 kHz)
  ->  server Silero VAD cuts sentences  ->  ASR -> wake -> robot_sandbox -> streaming TTS
  ->  binary TTS WAV chunks back  ->  self-hosted ALSA player -> local speaker

Full duplex:
  * Ingestion never pauses for playback — mic streams up continuously.
  * Player is self-hosted (pyalsaaudio): plays frame-by-frame so it can duck /
    kill within one 32 ms frame, feeds the AEC reference at write time (hard
    alignment), and plays streamed TTS sentence-by-sentence.
  * Barge-in is two-level:
      1. local RMS on the AEC-cleaned frame  -> duck volume (recoverable);
      2. server speech_start / tts_cancel     -> hard kill (authoritative).
  * Pi reports tts_state so the server switches to its echo-resistant VAD profile.

Requirements: websockets>=10, pyalsaaudio (playback), speexdsp (AEC).
  pip install websockets pyalsaaudio speexdsp

Run:
  python3 wonderecho_listener.py --config config.json
"""
from __future__ import annotations

import argparse
import array
import asyncio
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

try:
    import alsaaudio  # type: ignore
    _ALSA_AVAILABLE = True
except ImportError:
    alsaaudio = None  # type: ignore
    _ALSA_AVAILABLE = False

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

DEFAULT_CONFIG = SCRIPT_DIR / "config.example.json"
CHUNK_SAMPLES = 512
CHUNK_BYTES = CHUNK_SAMPLES * 2  # PCM16 mono
SETTINGS_POLL_INTERVAL = 3.0
PROTO_VERSION = 2  # streaming TTS + tts_state; server falls back to 1 if unset

_AEC_FILTER_LENGTH = 4096   # 256 ms at 16 kHz — covers room echo tail

# --- barge-in / ducking tuning (标定见 docs/wonderecho-fullduplex-bargein-plan.md §6) ---
DUCK_GAIN = 0.3             # playback gain while a local barge-in is suspected
DUCK_RMS_THRESHOLD = 800.0  # PCM16 RMS on the AEC-cleaned frame that suggests speech
DUCK_SPEECH_FRAMES = 2      # consecutive frames (~64 ms) before ducking
DUCK_HANGOVER_S = 1.2       # release duck if server never confirms within this window
PLAY_START_MUTE_S = 0.3     # absorb the aplay/ALSA startup transient before AEC tracks
POST_KILL_MUTE_S = 0.8      # tail silence after a hard kill so echo decays


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
# PCM helpers
# ---------------------------------------------------------------------------

def _wav_to_pcm_frames(wav_bytes: bytes) -> list[bytes]:
    """Strip WAV header and split PCM into CHUNK_BYTES frames (last one padded)."""
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


def _scale_pcm16(frame: bytes, gain: float) -> bytes:
    """Scale a PCM16 frame by gain with clipping. gain==1 is a no-op."""
    if gain >= 0.999:
        return frame
    if gain <= 0.001:
        return b"\x00" * len(frame)
    a = array.array("h")
    a.frombytes(frame)
    if sys.byteorder == "big":
        a.byteswap()
    for i in range(len(a)):
        v = int(a[i] * gain)
        a[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def _rms(frame: bytes) -> float:
    a = array.array("h")
    a.frombytes(frame)
    if sys.byteorder == "big":
        a.byteswap()
    n = len(a)
    if n == 0:
        return 0.0
    return (sum(v * v for v in a) / n) ** 0.5


# ---------------------------------------------------------------------------
# Self-hosted playback (frame-level ALSA, aplay fallback)
# ---------------------------------------------------------------------------

class _Player:
    """WAV-queue playback with per-frame gain + kill and AEC reference feed.

    ALSA mode (pyalsaaudio) writes frame-by-frame so duck()/kill() land within
    ~32 ms and each written frame is pushed to `ref_q` for the echo canceller.
    Fallback mode plays whole WAVs via aplay (kill supported; duck is a no-op).
    """

    def __init__(self, device: str, sample_rate: int = 16000) -> None:
        self._device = device or "default"
        self._sr = sample_rate
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self.ref_q: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._gain = 1.0
        self._busy = False
        self._kill = threading.Event()
        self._aplay: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._pcm = None
        self._alsa = _ALSA_AVAILABLE
        if self._alsa:
            self._pcm = self._open_alsa()
            self._alsa = self._pcm is not None
        if not self._alsa:
            print("[WARN] pyalsaaudio unavailable — aplay fallback (no volume ducking, "
                  "half-duplex mute during TTS). Install pyalsaaudio for best experience.",
                  flush=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def alsa_active(self) -> bool:
        return self._alsa and self._pcm is not None

    @property
    def playing(self) -> bool:
        return self._busy or not self._q.empty()

    def enqueue_wav(self, wav_bytes: bytes) -> None:
        self._q.put(wav_bytes)

    def duck(self) -> None:
        self._gain = DUCK_GAIN

    def unduck(self) -> None:
        self._gain = 1.0

    def kill(self) -> None:
        self._kill.set()
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        proc = self._aplay
        if proc is not None and proc.poll() is None:
            proc.kill()
        self._gain = 1.0

    def _open_alsa(self):
        try:
            pcm = alsaaudio.PCM(
                type=alsaaudio.PCM_PLAYBACK, mode=alsaaudio.PCM_NORMAL, device=self._device,
            )
            pcm.setchannels(1)
            pcm.setrate(self._sr)
            pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
            pcm.setperiodsize(CHUNK_SAMPLES)
            return pcm
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] ALSA open failed ({exc}); using aplay fallback", flush=True)
            return None

    def _run(self) -> None:
        while True:
            wav = self._q.get()
            if wav is None:
                break
            self._busy = True
            self._kill.clear()
            try:
                if self.alsa_active:
                    self._play_alsa(wav)
                else:
                    self._play_aplay(wav)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] playback error: {exc}", flush=True)
            finally:
                self._busy = False

    def _play_alsa(self, wav: bytes) -> None:
        for frame in _wav_to_pcm_frames(wav):
            if self._kill.is_set():
                break
            out = _scale_pcm16(frame, self._gain)
            try:
                self._pcm.write(out)  # blocks ~real-time as the ALSA buffer drains
            except Exception:
                pass
            # Push the post-gain frame (what the speaker emits) as the AEC reference
            # at write time so mic and reference stay lock-step. Drop-oldest on overflow.
            try:
                self.ref_q.put_nowait(out)
            except queue.Full:
                try:
                    self.ref_q.get_nowait()
                    self.ref_q.put_nowait(out)
                except queue.Empty:
                    pass

    def _play_aplay(self, wav: bytes) -> None:
        player = shutil.which("aplay") or shutil.which("paplay")
        if not player:
            print("[WARN] no audio player available", flush=True)
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            fh.write(wav)
            tmp = fh.name
        cmd = [player, "-q"]
        if Path(player).name == "aplay" and self._device and self._device != "default":
            cmd += ["-D", self._device]
        cmd.append(tmp)
        try:
            self._aplay = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while self._aplay.poll() is None:
                if self._kill.is_set():
                    self._aplay.kill()
                    break
                time.sleep(0.02)
        finally:
            self._aplay = None
            Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Audio engine: AEC + local barge-in ducking + playback control
# ---------------------------------------------------------------------------

class _AudioEngine:
    """Owns the player + echo canceller; cleans each mic frame and drives ducking."""

    def __init__(
        self,
        tts_device: str,
        sample_rate: int = 16000,
        *,
        hardware_aec: bool = False,
        duck_rms_threshold: float = DUCK_RMS_THRESHOLD,
        debug: bool = False,
    ) -> None:
        self._sr = sample_rate
        self._player = _Player(tts_device, sample_rate)
        self._hardware_aec = hardware_aec
        self._duck_threshold = duck_rms_threshold
        self._debug = debug
        if hardware_aec:
            # Trust the WonderEchoPro board AEC — the mic upstream is already
            # echo-free, so no software canceller / startup mute is needed.
            self._ec = None
            print("[INFO] hardware_aec=on — trusting module board AEC (software AEC off)", flush=True)
        elif _SPEEX_AVAILABLE:
            self._ec = _SpeexEC.create(CHUNK_SAMPLES, _AEC_FILTER_LENGTH, sample_rate)
        else:
            self._ec = None
            print("[WARN] speexdsp unavailable — no echo cancellation; mic is muted "
                  "during TTS (half-duplex). Install speexdsp for barge-in.", flush=True)
        self._mute_until = 0.0
        self._prev_playing = False
        self._duck_active = False
        self._duck_until = 0.0
        self._speech_run = 0
        # --aec-debug residual-echo instrumentation
        self._dbg_sum = 0.0
        self._dbg_n = 0
        self._dbg_peak = 0.0
        self._dbg_floor: float | None = None
        self._dbg_last = 0.0

    # --- playback delegation -------------------------------------------------
    def enqueue_wav(self, wav_bytes: bytes) -> None:
        self._player.enqueue_wav(wav_bytes)

    @property
    def playing(self) -> bool:
        return self._player.playing

    def in_mute_window(self) -> bool:
        return time.time() < self._mute_until

    def kill(self) -> None:
        """Server-confirmed barge-in: hard-stop playback and reset ducking."""
        self._player.kill()
        self._duck_active = False
        self._speech_run = 0
        self._mute_until = time.time() + POST_KILL_MUTE_S

    # --- per-frame mic processing -------------------------------------------
    def process(self, mic_frame: bytes) -> bytes:
        now = time.time()
        playing = self._player.playing

        if playing and not self._prev_playing:
            # Hardware AEC needs no startup mute (mic is already clean).
            self._mute_until = 0.0 if self._hardware_aec else now + PLAY_START_MUTE_S
        self._prev_playing = playing

        if playing:
            if self._hardware_aec:
                # Module cancels its own echo — pass the mic straight through.
                self._local_barge(mic_frame, now)
                self._dbg_observe(mic_frame, during_tts=True, now=now)
                return mic_frame

            # Stay lock-step with the player: consume exactly one reference per frame.
            try:
                ref = self._player.ref_q.get_nowait()
            except queue.Empty:
                ref = b"\x00" * CHUNK_BYTES

            if now < self._mute_until:
                return b"\x00" * CHUNK_BYTES

            if self._ec is not None and self._player.alsa_active:
                clean = bytes(self._ec.process(mic_frame, ref))
            elif not self._player.alsa_active:
                # No aligned reference available: mute to avoid an echo self-trigger.
                return b"\x00" * CHUNK_BYTES
            else:
                clean = mic_frame

            self._local_barge(clean, now)
            self._dbg_observe(clean, during_tts=True, now=now)
            return clean

        # Not playing: release any duck, enforce post-kill/post-TTS tail silence.
        if self._duck_active:
            self._duck_active = False
            self._speech_run = 0
        self._dbg_observe(mic_frame, during_tts=False, now=now)
        if now < self._mute_until:
            return b"\x00" * CHUNK_BYTES
        return mic_frame

    def _dbg_observe(self, frame: bytes, during_tts: bool, now: float) -> None:
        """--aec-debug: report the AEC-cleaned RMS during TTS vs the idle floor.

        During-TTS residual should sit near the idle floor if echo is cancelled
        (hardware or software). Keep quiet during playback for a clean reading;
        your own barge-in speech will (expectedly) spike the mean.
        """
        if not self._debug:
            return
        r = _rms(frame)
        if during_tts:
            self._dbg_sum += r
            self._dbg_n += 1
            self._dbg_peak = max(self._dbg_peak, r)
        else:
            self._dbg_floor = r if self._dbg_floor is None else 0.9 * self._dbg_floor + 0.1 * r
        if now - self._dbg_last >= 2.0 and self._dbg_n > 0:
            mean = self._dbg_sum / self._dbg_n
            floor = self._dbg_floor or 0.0
            ratio = (mean / floor) if floor > 1 else float("inf")
            tag = "GOOD" if ratio < 2 else "ECHO LEAK"
            print(f"[AEC-DEBUG] during-TTS residual mean={mean:.0f} peak={self._dbg_peak:.0f} "
                  f"idle_floor~{floor:.0f} ratio={ratio:.1f}x ({tag})", flush=True)
            self._dbg_sum = 0.0
            self._dbg_n = 0
            self._dbg_peak = 0.0
            self._dbg_last = now

    def _local_barge(self, clean: bytes, now: float) -> None:
        """Level-1 barge-in: duck on sustained energy in the cleaned frame."""
        if _rms(clean) >= self._duck_threshold:
            self._speech_run += 1
        else:
            self._speech_run = 0

        if self._speech_run >= DUCK_SPEECH_FRAMES and not self._duck_active:
            self._duck_active = True
            self._duck_until = now + DUCK_HANGOVER_S
            self._player.duck()
            print("[INFO] barge-in L1: duck", flush=True)
        elif self._duck_active and now > self._duck_until:
            # Server never confirmed → treat as a false alarm and restore volume.
            self._duck_active = False
            self._speech_run = 0
            self._player.unduck()
            print("[INFO] barge-in L1: duck released (no server confirm)", flush=True)


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

    print(f"[INFO] WS connect {ws_url} session={session_id} proto={PROTO_VERSION}", flush=True)

    engine = _AudioEngine(
        tts_device,
        hardware_aec=bool(config.get("hardware_aec", False)),
        duck_rms_threshold=float(config.get("duck_rms_threshold", DUCK_RMS_THRESHOLD)),
        debug=bool(config.get("aec_debug", False)),
    )

    async with websockets.connect(ws_url, additional_headers=extra_headers, ping_interval=None) as ws:
        await ws.send(json.dumps({
            "type": "start_stream",
            "session_id": session_id,
            "device_id": device_id,
            "sample_rate": 16000,
            "route": True,
            "proto": PROTO_VERSION,
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
            discarding = False
            async for m in ws:
                if isinstance(m, bytes):
                    if len(m) == 0:
                        continue  # stream-end sentinel
                    if discarding:
                        continue  # dropped: belongs to a cancelled turn
                    engine.enqueue_wav(m)
                elif isinstance(m, str):
                    try:
                        ev = json.loads(m)
                    except json.JSONDecodeError:
                        continue
                    et = ev.get("type")
                    if et == "tts_begin":
                        discarding = False  # new turn — accept its audio
                    elif et == "tts_cancel":
                        discarding = True
                        engine.kill()
                    elif et == "speech_start":
                        # Level-2 barge-in: hard kill if we're actually playing and
                        # past the startup mute (server VAD ran on AEC-cleaned upstream).
                        if engine.playing and not engine.in_mute_window():
                            engine.kill()
                            discarding = True
                    elif et == "result":
                        print(json.dumps({
                            "text": ev.get("text") or "",
                            "tts_text": ev.get("tts_text") or "",
                            "wake": ev.get("wake_status") or "—",
                        }, ensure_ascii=False), flush=True)
                    elif et == "stream_stopped":
                        break

        recv_task = asyncio.create_task(_recv_loop())

        prev_playing = False
        try:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(CHUNK_BYTES)
                if not chunk:
                    break
                if len(chunk) < CHUNK_BYTES:
                    chunk = chunk + b"\x00" * (CHUNK_BYTES - len(chunk))
                clean = engine.process(chunk)
                await ws.send(clean)

                # Report playback state transitions so the server switches VAD profile.
                playing_now = engine.playing
                if playing_now != prev_playing:
                    prev_playing = playing_now
                    try:
                        await ws.send(json.dumps({
                            "type": "tts_state",
                            "playing": playing_now,
                            "ts_ms": int(time.time() * 1000),
                        }))
                    except Exception:
                        pass
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
    tts_device = str(config.get("tts_device") or "")
    _last_volume: int = -1  # track last applied pi_speaker_volume

    while True:
        try:
            settings = get_cloud_settings(server, token)

            # Apply Pi speaker volume if server setting changed
            pi_volume = int(settings.get("pi_speaker_volume", 80))
            if pi_volume != _last_volume:
                _init_alsa_volume(tts_device, pi_volume)
                _last_volume = pi_volume

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

def _init_alsa_volume(tts_device: str, volume_pct: int = 80) -> None:
    """Set Speaker playback volume on the TTS card."""
    if not shutil.which("amixer"):
        return
    # Find card index via aplay -l matching the CARD name in tts_device
    # aplay -l format: "card N: SHORTNAME [LONGNAME], device ..."
    card_idx: str | None = None
    if "CARD=" in tts_device:
        card_name = tts_device.split("CARD=")[1].split(",")[0].lower()
        try:
            out = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=3).stdout
            for line in out.splitlines():
                if not line.startswith("card "):
                    continue
                parts = line.split()
                # parts[0]="card", parts[1]="N:", parts[2]="SHORTNAME" (may have trailing comma)
                short_name = parts[2].rstrip(",").lower() if len(parts) > 2 else ""
                if short_name == card_name:
                    card_idx = parts[1].rstrip(":")
                    break
        except Exception:
            pass
    card_arg = ["-c", card_idx] if card_idx else []
    vol_str = f"{volume_pct}%"
    for ctrl in ("Speaker Playback Volume", "Speaker", "PCM"):
        try:
            r = subprocess.run(
                ["amixer"] + card_arg + ["sset", ctrl, vol_str, "on"],
                capture_output=True, timeout=3,
            )
            if r.returncode == 0:
                print(f"[INFO] ALSA '{ctrl}' {vol_str} on card={card_idx or 'default'}", flush=True)
                break
        except Exception:
            pass
    for ctrl in ("Speaker Playback Switch", "Speaker"):
        try:
            subprocess.run(
                ["amixer"] + card_arg + ["sset", ctrl, "on"],
                capture_output=True, timeout=3,
            )
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="WonderEchoPro Pi-side listener.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--aec-debug", action="store_true",
                        help="log residual-echo RMS during TTS (real-mode AEC readout)")
    parser.add_argument("--hardware-aec", action="store_true",
                        help="trust the module board AEC — skip software AEC + startup mute")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"[ERROR] config not found: {args.config}", flush=True)
        return 1

    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.aec_debug:
        config["aec_debug"] = True
    if args.hardware_aec:
        config["hardware_aec"] = True
    _init_alsa_volume(str(config.get("tts_device") or ""))
    asyncio.run(_ws_main(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
