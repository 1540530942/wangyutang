"""WonderEchoPro Pi-side listener — full-duplex via PipeWire echo-cancel (v3).

Instead of grabbing the ALSA card directly (which fought wireplumber and gave a
useless 0.6 dB speexdsp AEC on this hardware), we route audio through PipeWire's
WebRTC echo canceller. `module-echo-cancel` exposes a virtual mic `ec_source`
(echo already removed, ~30 dB on this device) and a virtual speaker `ec_sink`.

Flow:
  poll /api/settings  ->  when manual_recording_enabled + input_mode==wonderechopro
  ->  pw-record ec_source (clean PCM16 16k)  ->  WS /ws/audio (server VAD/ASR/route)
  ->  streaming TTS WAV chunks back  ->  pw-play each to ec_sink
  ->  mic stays streaming during TTS (echo cancelled) → true barge-in.

Because the upstream is clean even while TTS plays, the server's VAD hears real
user speech (not echo) and drives barge-in: speech_start/tts_cancel -> kill.

No speexdsp, no keeper, no half-duplex, no direct ALSA — PipeWire owns the card.

Requirements: websockets>=10; PipeWire with module-echo-cancel (pw-record/pw-play/pactl/wpctl).

Run:
  python3 wonderecho_listener.py --config config.json
"""
from __future__ import annotations

import argparse
import array
import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
import queue
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.example.json"

CHUNK_SAMPLES = 512
CHUNK_BYTES = CHUNK_SAMPLES * 2          # PCM16 mono
WAV_HEADER_BYTES = 44                    # pw-record writes a canonical WAV header first
SETTINGS_POLL_INTERVAL = 3.0
PROTO_VERSION = 2

EC_SOURCE = "ec_source"
EC_SINK = "ec_sink"
# module-echo-cancel load args (validated: WebRTC AEC → ~31 dB ERLE on this card)
_EC_LOAD_ARGS = [
    "aec_method=webrtc",
    f"source_name={EC_SOURCE}",
    f"sink_name={EC_SINK}",
    "aec_args=webrtc.extended_filter=1 webrtc.high_pass_filter=1 webrtc.noise_suppression=1 webrtc.gain_control=0",
]
ONSET_GRACE_S = 0.5  # ignore barge-in for the first 0.5 s of a turn (AEC convergence)
# Local barge-in: the AEC leaves ~400 RMS residual echo (robot's own voice), which
# keeps the server's speech VAD busy so it can't segment a barge-in. So we detect the
# user locally — their voice on the clean ec_source spikes well above the residual —
# and kill the TTS; once playback stops the echo vanishes and the server cleanly
# recognizes the rest of the user's speech.
BARGEIN_RMS = 3200.0   # above the TTS residual echo (p95 ~2800 at 45% vol), config-tunable
BARGEIN_FRAMES = 5     # sustained frames (~160 ms) — avoids brief residual spikes


def _rms(pcm16: bytes) -> float:
    a = array.array("h")
    a.frombytes(pcm16)
    if not a:
        return 0.0
    return (sum(v * v for v in a) / len(a)) ** 0.5


def _pw_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


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


def _post_json(url: str, token: str, payload: dict[str, Any], timeout: float = 8.0) -> dict[str, Any] | None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Audio-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"[WARN] POST {url} failed: {exc}", flush=True)
        return None


class EdgeJournal:
    """Append-only Pi-side event ledger on a monotonic clock.

    Records facts only the edge knows — TTS playback start/stop, barge-in kills,
    capture stalls — durably to disk the instant they happen, then uploads them to
    the cloud session package on session end. The uplink can drop without losing
    the cancel_delay / post_cancel_tail evidence, and re-uploads dedup by event_id.
    """

    def __init__(self, session_id: str, device_id: str, root: Path | None = None) -> None:
        self.session_id = session_id
        self.device_id = device_id
        self._t0 = time.monotonic()
        self._seq = 0
        self._pending: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        base = Path(root or (SCRIPT_DIR / "edge_journal"))
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.path = base / f"{session_id}.jsonl"

    def now_ms(self) -> int:
        return max(0, int((time.monotonic() - self._t0) * 1000))

    def emit(self, etype: str, **data: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {
                "event_id": f"edge_{self.session_id}_{self._seq:06d}",
                "ts_ms": self.now_ms(),
                "type": etype,
                "session_id": self.session_id,
                "source": "edge",
                **data,
            }
            self._pending.append(event)
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                pass
            return event

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            out = list(self._pending)
            self._pending = []
            return out


def estimate_clock_offset(samples: list[tuple[int, int, int]]) -> dict[str, int] | None:
    """NTP-style offset from (t0_edge, t1_server, t2_edge) probe samples.

    For each sample: offset = t1 - (t0+t2)/2 maps edge clock → server session
    clock (ts_session ≈ ts_edge + offset). The sample with the smallest RTT is
    the least queue-delayed, so use the median of the best half by RTT.
    """
    if not samples:
        return None
    scored = sorted(((t2 - t0, t1 - (t0 + t2) // 2) for t0, t1, t2 in samples))
    best = scored[: max(1, len(scored) // 2 + 1)]
    offsets = sorted(o for _, o in best)
    return {"offset_ms": offsets[len(offsets) // 2], "rtt_ms": best[0][0]}


async def _run_clock_probes(ws: Any, journal: "EdgeJournal", count: int = 5) -> dict[str, int] | None:
    """Sequential probe exchange right after stream_ready (no audio in flight yet)."""
    samples: list[tuple[int, int, int]] = []
    for _ in range(count):
        t0 = journal.now_ms()
        await ws.send(json.dumps({"type": "clock_probe", "t0": t0}))
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                if isinstance(raw, str):
                    msg = json.loads(raw)
                    if msg.get("type") == "clock_probe_ack" and int(msg.get("t0", -1)) == t0:
                        samples.append((t0, int(msg.get("t1", 0)), journal.now_ms()))
                        break
        except (asyncio.TimeoutError, json.JSONDecodeError, ValueError):
            continue
    sync = estimate_clock_offset(samples)
    if sync is not None:
        journal.emit("clock.sync", **sync, probes=len(samples))
        await ws.send(json.dumps({"type": "clock_sync", **sync, "probes": len(samples)}))
        print(f"[INFO] clock sync offset={sync['offset_ms']}ms rtt={sync['rtt_ms']}ms", flush=True)
    return sync


def upload_edge_journal(server: str, token: str, journal: "EdgeJournal") -> None:
    events = journal.drain()
    if not events:
        return
    url = f"{server.rstrip('/')}/api/sessions/{journal.session_id}/edge-events"
    resp = _post_json(url, token, {"events": events})
    if resp is None:
        # Uplink failed: put them back so a later attempt (or the next session's
        # end) can retry. The local JSONL remains as the durable fallback.
        with journal._lock:
            journal._pending = events + journal._pending
    else:
        print(f"[INFO] edge-events uploaded merged={resp.get('merged')} skipped={resp.get('skipped')}", flush=True)


def get_cloud_settings(server: str, token: str) -> dict[str, Any] | None:
    """Return settings, or None on a failed poll (caller must NOT treat that as
    manual_recording=False, or a transient network blip would kill the session)."""
    result = _get_json(f"{server.rstrip('/')}/api/settings", token)
    if result is None:
        return None
    return result.get("settings", result) if isinstance(result, dict) else None


# ---------------------------------------------------------------------------
# PipeWire helpers
# ---------------------------------------------------------------------------

def _ensure_echo_cancel() -> bool:
    """Load module-echo-cancel if ec_source/ec_sink aren't already present."""
    env = _pw_env()
    try:
        out = subprocess.run(["pactl", "list", "sources", "short"], env=env,
                             capture_output=True, text=True, timeout=6).stdout
        if EC_SOURCE in out:
            return True
        subprocess.run(["pactl", "load-module", "module-echo-cancel"] + _EC_LOAD_ARGS,
                       env=env, capture_output=True, timeout=10)
        time.sleep(1.5)
        out = subprocess.run(["pactl", "list", "sources", "short"], env=env,
                             capture_output=True, text=True, timeout=6).stdout
        ok = EC_SOURCE in out
        print(f"[INFO] echo-cancel module {'ready' if ok else 'FAILED to load'}", flush=True)
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] ensure_echo_cancel failed: {exc}", flush=True)
        return False


def _real_usb_sink(env: dict[str, str]) -> str | None:
    try:
        out = subprocess.run(["pactl", "list", "sinks", "short"], env=env,
                             capture_output=True, text=True, timeout=6).stdout
        for line in out.splitlines():
            parts = line.split("\t")
            name = parts[1] if len(parts) > 1 else ""
            if name.startswith("alsa_output") and "usb" in name.lower():
                return name
    except Exception:  # noqa: BLE001
        pass
    return None


def apply_speaker_volume(volume_pct: int) -> None:
    """Master volume = real USB sink volume (PipeWire drives the ALSA mixer)."""
    env = _pw_env()
    sink = _real_usb_sink(env)
    if not sink:
        return
    v = max(0, min(150, volume_pct))
    for cmd in (["pactl", "set-sink-mute", sink, "0"],
                ["pactl", "set-sink-volume", sink, f"{v}%"]):
        try:
            subprocess.run(cmd, env=env, capture_output=True, timeout=4)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Playback: one killable pw-play per TTS WAV chunk, to ec_sink
# ---------------------------------------------------------------------------

class _Playback:
    def __init__(self, on_event: Any = None) -> None:
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self._cur: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._busy = False
        self._lock = threading.Lock()
        self._env = _pw_env()
        self._stop = False
        # Called (from the playback thread) with (etype, **data) for play_start /
        # play_stop so the edge journal can measure post-cancel playback tail.
        self._on_event = on_event
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _emit(self, etype: str, **data: Any) -> None:
        if self._on_event is not None:
            try:
                self._on_event(etype, **data)
            except Exception:  # noqa: BLE001
                pass

    @property
    def playing(self) -> bool:
        return self._busy or not self._q.empty()

    def enqueue_wav(self, wav: bytes) -> None:
        self._q.put(wav)

    def kill(self) -> None:
        """Barge-in: drop queued chunks and stop the current pw-play immediately."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            p = self._cur
        if p is not None and p.poll() is None:
            p.kill()

    def close(self) -> None:
        self._stop = True
        self.kill()
        self._q.put(None)
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop:
            wav = self._q.get()
            if wav is None:
                break
            self._busy = True
            try:
                self._play(wav)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] playback error: {exc}", flush=True)
            finally:
                self._busy = False

    def _play(self, wav: bytes) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            fh.write(wav)
            tmp = fh.name
        try:
            print(f"[DBG] pw-play start wav={len(wav)}B", flush=True)
            self._emit("playback.play_start", bytes=len(wav))
            p = subprocess.Popen(
                ["pw-play", "--target", EC_SINK, tmp],
                env=self._env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            with self._lock:
                self._cur = p
            _, err = p.communicate()
            rc = p.returncode or 0
            # pw-play killed by barge-in exits on a signal (negative rc); a clean
            # play returns 0. This distinguishes tail-cut from natural end.
            self._emit("playback.play_stop", reason="killed" if rc < 0 else "ended", rc=rc)
            print(f"[DBG] pw-play done rc={p.returncode} err={(err or b'')[:80]!r}", flush=True)
        finally:
            with self._lock:
                self._cur = None
            Path(tmp).unlink(missing_ok=True)


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

    if not _ensure_echo_cancel():
        print("[ERROR] echo-cancel unavailable — aborting session", flush=True)
        await asyncio.sleep(3)
        return

    apply_speaker_volume(int(config.get("pi_speaker_volume", 80)))

    server = str(config.get("server") or "")
    token = str(config.get("token") or "")
    device_id = str(config.get("device_id") or "turbopi-01")
    ws_url = _ws_url(server)
    session_id = f"pi-{int(time.time() * 1000)}"
    extra_headers = {"X-Audio-Token": token} if token else {}

    print(f"[INFO] WS connect {ws_url} session={session_id} proto={PROTO_VERSION} (PipeWire ec)", flush=True)

    journal = EdgeJournal(session_id, device_id)
    playback = _Playback(on_event=journal.emit)
    turn_begin_at = [0.0]  # shared: when the current TTS turn started (onset grace)
    bargein_rms = float(config.get("bargein_rms", BARGEIN_RMS))
    bargein_frames = int(config.get("bargein_frames", BARGEIN_FRAMES))
    # Barge-in during TTS needs the mic upstream to cleanly separate the user's
    # voice from the robot's echo. On this USB card the AEC residual overlaps user
    # speech, so energy detection false-triggers — disable to keep clean turn-taking.
    bargein_enabled = bool(config.get("bargein_enabled", True))

    # ping_interval keepalive is essential: through the public gateway the WS can
    # half-drop silently; without pings the client would block on recv/send forever
    # and the session would freeze (no logs, no restart). Pings surface the drop as
    # ConnectionClosed → session ends → _ws_main restarts it.
    async with websockets.connect(
        ws_url, additional_headers=extra_headers, ping_interval=20, ping_timeout=20, close_timeout=5,
    ) as ws:
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
                print("[INFO] stream_ready — streaming clean mic (ec_source)", flush=True)
                break

        # Estimate edge↔session clock offset before any audio is in flight, so
        # edge journal timestamps can be mapped onto the session timeline (G8).
        try:
            await _run_clock_probes(ws, journal)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] clock probe failed: {exc}", flush=True)

        rec = await asyncio.create_subprocess_exec(
            "pw-record", "--target", EC_SOURCE, "--rate", "16000",
            "--channels", "1", "--format", "s16", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=_pw_env(),
        )

        st = {"discarding": False, "barge_run": 0}

        async def _recv_loop() -> None:
            async for m in ws:
                if isinstance(m, bytes):
                    if len(m) == 0:
                        continue  # stream-end sentinel
                    if st["discarding"]:
                        continue  # belongs to a cancelled turn
                    playback.enqueue_wav(m)
                elif isinstance(m, str):
                    try:
                        ev = json.loads(m)
                    except json.JSONDecodeError:
                        continue
                    et = ev.get("type")
                    if et == "tts_begin":
                        print("[DBG] tts_begin", flush=True)
                        st["discarding"] = False
                        turn_begin_at[0] = time.time()
                        journal.emit("tts.begin_recv", tts_id=ev.get("tts_id"), turn_id=ev.get("turn_id"))
                    elif et == "tts_cancel":
                        print("[DBG] tts_cancel -> kill", flush=True)
                        # Mark receipt before killing so post_cancel_tail =
                        # play_stop − tts.cancel_recv is measurable on the edge clock.
                        journal.emit("bargein.tts_cancel_recv", tts_id=ev.get("tts_id"), turn_id=ev.get("turn_id"))
                        playback.kill()
                        st["discarding"] = True
                    elif et == "speech_start":
                        # Server-side barge-in confirmation.
                        if bargein_enabled and playback.playing and (time.time() - turn_begin_at[0]) > ONSET_GRACE_S:
                            print("[DBG] speech_start -> barge-in kill", flush=True)
                            journal.emit("bargein.playback_killed", reason="server_speech_start",
                                         segment_id=ev.get("segment_id"))
                            playback.kill()
                            st["discarding"] = True
                    elif et == "result":
                        # A new turn's result always precedes its TTS — accept the
                        # upcoming audio even if a prior barge-in left discarding set
                        # and no tts_begin arrives to clear it (proto<2 path).
                        st["discarding"] = False
                        print(json.dumps({
                            "text": ev.get("text") or "",
                            "tts_text": ev.get("tts_text") or "",
                            "wake": ev.get("wake_status") or "—",
                        }, ensure_ascii=False), flush=True)
                    elif et == "stream_stopped":
                        break

        recv_task = asyncio.create_task(_recv_loop())

        prev_playing = False
        journal.emit("capture.start", ec_source=EC_SOURCE)
        try:
            assert rec.stdout is not None
            await asyncio.wait_for(rec.stdout.readexactly(WAV_HEADER_BYTES), timeout=10.0)  # skip WAV header
            while True:
                try:
                    # 15 s guard: if the capture stalls (alive but no data) the read
                    # would block forever; time out so the session restarts.
                    chunk = await asyncio.wait_for(rec.stdout.readexactly(CHUNK_BYTES), timeout=15.0)
                except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                    print("[WARN] capture stalled/ended — restarting session", flush=True)
                    journal.emit("capture.stall")
                    break
                # Full duplex: always send the (echo-cancelled) mic upstream.
                await ws.send(chunk)

                # Local energy barge-in: while TTS plays, a sustained spike above the
                # residual echo means the user is talking → kill TTS so the server can
                # then cleanly hear (and recognize) the rest of their speech.
                if bargein_enabled and playback.playing and (time.time() - turn_begin_at[0]) > ONSET_GRACE_S:
                    if _rms(chunk) > bargein_rms:
                        st["barge_run"] += 1
                        if st["barge_run"] == 1:
                            journal.emit("bargein.local_duck", rms=round(_rms(chunk), 1))
                        if st["barge_run"] >= bargein_frames:
                            print("[DBG] local barge-in (energy) -> kill", flush=True)
                            journal.emit("bargein.playback_killed", reason="local_energy",
                                         rms=round(_rms(chunk), 1))
                            playback.kill()
                            st["discarding"] = True
                            st["barge_run"] = 0
                    else:
                        st["barge_run"] = 0
                else:
                    st["barge_run"] = 0

                playing_now = playback.playing
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
            try:
                rec.kill()
                await rec.wait()
            except Exception:
                pass
            try:
                await asyncio.wait_for(ws.send(json.dumps({"type": "stop_stream"})), timeout=3.0)
                await asyncio.sleep(0.3)
            except Exception:
                pass
            recv_task.cancel()
            try:
                await recv_task
            except (asyncio.CancelledError, Exception):
                pass
            playback.close()
            journal.emit("capture.end")
            # Upload the edge ledger so cancel/tail metrics land in the cloud
            # session package. Runs off the loop; a failed uplink keeps the local
            # JSONL and re-queues the events for the next attempt.
            try:
                loop = asyncio.get_event_loop()
                await asyncio.wait_for(
                    loop.run_in_executor(None, upload_edge_journal, server, token, journal),
                    timeout=10.0,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] edge-events upload failed: {exc}", flush=True)

    print("[INFO] WS session ended", flush=True)


# ---------------------------------------------------------------------------
# WonderEcho Pro UART wake word integration
# ---------------------------------------------------------------------------
# The CL1302 DSP on WonderEcho Pro detects the onboard wake word and fires a
# 5-byte packet on /dev/ttyUSB0 (115200 baud): aa 55 03 00 fb.
# When we see this packet we call POST /api/device/{device_id}/wake so the
# server marks the device as awake — bypassing the ASR-based wake gate.

_UART_WAKE_PACKET = bytes([0xAA, 0x55, 0x03, 0x00, 0xFB])
_UART_PORT = "/dev/ttyUSB0"
_UART_BAUD = 115200


def _uart_wake_thread(server: str, token: str, device_id: str) -> None:
    """Background thread: watch WonderEcho Pro UART for wake word, activate server."""
    wake_url = f"{server.rstrip('/')}/api/device/{device_id}/wake"
    buf = bytearray()
    pat = _UART_WAKE_PACKET
    pat_len = len(pat)

    try:
        import termios, tty  # type: ignore[import]
        fd = open(_UART_PORT, "rb", buffering=0)
        attrs = termios.tcgetattr(fd)
        attrs[4] = attrs[5] = termios.B115200  # ispeed, ospeed
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        print(f"[UART] watching {_UART_PORT} for wake word", flush=True)
    except Exception as exc:
        print(f"[UART] {_UART_PORT} unavailable ({exc}), wake-word UART disabled", flush=True)
        return

    try:
        while True:
            try:
                byte = fd.read(1)
                if not byte:
                    break
            except OSError:
                break
            buf.extend(byte)
            # Keep only the last pat_len bytes
            if len(buf) > pat_len * 2:
                del buf[:len(buf) - pat_len]
            if len(buf) >= pat_len and bytes(buf[-pat_len:]) == pat:
                buf.clear()
                print("[UART] wake word detected — activating device", flush=True)
                _post_json(wake_url, token, {})
    finally:
        try:
            fd.close()
        except Exception:
            pass
    print("[UART] thread exited", flush=True)


# ---------------------------------------------------------------------------
# Settings-aware main loop
# ---------------------------------------------------------------------------

async def _ws_main(config: dict[str, Any]) -> None:
    server = str(config.get("server") or "")
    token = str(config.get("token") or "")
    session_task: asyncio.Task[None] | None = None
    _last_volume: int = -1

    while True:
        try:
            settings = get_cloud_settings(server, token)
            if settings is None:
                # Transient poll failure — keep the current session as-is.
                await asyncio.sleep(SETTINGS_POLL_INTERVAL)
                continue

            pi_volume = int(settings.get("pi_speaker_volume", 80))
            config["pi_speaker_volume"] = pi_volume
            # Re-assert every poll: the PipeWire sink can get muted (wireplumber
            # then mirrors the mute to the ALSA hardware mixer → silence). Self-heal.
            apply_speaker_volume(pi_volume)
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

def main() -> int:
    parser = argparse.ArgumentParser(description="WonderEchoPro Pi-side listener (PipeWire echo-cancel).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    if not args.config.exists():
        print(f"[ERROR] config not found: {args.config}", flush=True)
        return 1

    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    _ensure_echo_cancel()
    apply_speaker_volume(int(config.get("pi_speaker_volume", 80)))

    server = str(config.get("server") or "")
    token = str(config.get("token") or "")
    device_id = str(config.get("device_id") or "turbopi-01")
    t = threading.Thread(target=_uart_wake_thread, args=(server, token, device_id), daemon=True)
    t.start()

    asyncio.run(_ws_main(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
