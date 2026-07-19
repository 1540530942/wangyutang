"""Pi-side full-duplex audio engine logic (two-level barge-in state machine).

Runs on stdlib only — wonderecho_listener guards speexdsp/alsaaudio imports and
loads websockets lazily, so no hardware or torch is needed in CI.
"""
from __future__ import annotations

import array
import queue
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "audio_interact" / "edge"))

import wonderecho_listener as wm  # noqa: E402


def _frame(amp: int, n: int = wm.CHUNK_SAMPLES) -> bytes:
    return array.array("h", [amp] * n).tobytes()


class FakePlayer:
    def __init__(self) -> None:
        self._playing = False
        self.gain = 1.0
        self.killed = 0
        self.alsa_active = True
        self.ref_q: queue.Queue = queue.Queue()

    @property
    def playing(self) -> bool:
        return self._playing

    def duck(self) -> None:
        self.gain = wm.DUCK_GAIN

    def unduck(self) -> None:
        self.gain = 1.0

    def kill(self) -> None:
        self.killed += 1
        self._playing = False
        self.gain = 1.0

    def enqueue_wav(self, w: bytes) -> None:
        pass


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _engine(monkeypatch, clock: Clock, *, alsa: bool = True) -> "wm._AudioEngine":
    monkeypatch.setattr(wm.time, "time", clock)
    eng = wm._AudioEngine.__new__(wm._AudioEngine)
    eng._sr = 16000
    eng._player = FakePlayer()
    eng._player.alsa_active = alsa
    eng._ec = None  # no speex → clean == mic (isolate the ducking logic)
    eng._hardware_aec = False
    eng._duck_threshold = wm.DUCK_RMS_THRESHOLD
    eng._debug = False
    eng._dbg_sum = 0.0
    eng._dbg_n = 0
    eng._dbg_peak = 0.0
    eng._dbg_floor = None
    eng._dbg_last = 0.0
    eng._mute_until = 0.0
    eng._prev_playing = False
    eng._duck_until = 0.0
    eng._duck_active = False
    eng._speech_run = 0
    return eng


# ---- pure helpers ----

def test_scale_pcm16_attenuates_and_is_noop_at_unity():
    f = _frame(1000)
    assert wm._scale_pcm16(f, 1.0) == f
    assert wm._scale_pcm16(f, 2.0) == f  # gain>=1 never amplifies (duck only attenuates)
    assert wm._scale_pcm16(f, 0.0) == b"\x00" * len(f)
    half = array.array("h")
    half.frombytes(wm._scale_pcm16(f, 0.5))
    assert all(v == 500 for v in half)


def test_rms():
    assert wm._rms(_frame(0)) == 0.0
    assert abs(wm._rms(_frame(1000)) - 1000.0) < 1.0


# ---- engine state machine ----

def test_idle_passthrough(monkeypatch):
    eng = _engine(monkeypatch, Clock())
    f = _frame(1000)
    assert eng.process(f) == f  # not playing, no mute window → raw mic upstream


def test_startup_mute_then_local_duck(monkeypatch):
    clk = Clock()
    eng = _engine(monkeypatch, clk)
    eng._player._playing = True
    loud = _frame(5000)  # RMS 5000 > DUCK_RMS_THRESHOLD

    assert eng.process(loud) == b"\x00" * wm.CHUNK_BYTES  # startup transient muted
    assert eng._player.gain == 1.0

    clk.t += wm.PLAY_START_MUTE_S + 0.01
    eng.process(loud)
    assert eng._player.gain == 1.0  # one frame insufficient
    eng.process(loud)
    assert eng._player.gain == wm.DUCK_GAIN  # DUCK_SPEECH_FRAMES sustained → duck
    assert eng._duck_active


def test_duck_released_without_server_confirm(monkeypatch):
    clk = Clock()
    eng = _engine(monkeypatch, clk)
    eng._player._playing = True
    eng._prev_playing = True  # skip startup mute
    eng.process(_frame(5000))
    eng.process(_frame(5000))
    assert eng._player.gain == wm.DUCK_GAIN

    clk.t += wm.DUCK_HANGOVER_S + 0.1
    eng.process(_frame(0))
    assert eng._player.gain == 1.0  # false alarm → restore
    assert not eng._duck_active


def test_server_confirmed_kill(monkeypatch):
    clk = Clock()
    eng = _engine(monkeypatch, clk)
    eng._player._playing = True
    eng._prev_playing = True
    eng.process(_frame(5000))
    eng.process(_frame(5000))

    eng.kill()  # level-2 barge-in
    assert eng._player.killed == 1
    assert eng._player.gain == 1.0
    assert not eng._duck_active
    assert eng.in_mute_window()  # post-kill tail silence engaged


def test_fallback_mutes_during_tts(monkeypatch):
    eng = _engine(monkeypatch, Clock(), alsa=False)  # aplay fallback, no aligned ref
    eng._player._playing = True
    eng._prev_playing = True
    assert eng.process(_frame(5000)) == b"\x00" * wm.CHUNK_BYTES


def test_hardware_aec_passthrough_no_mute(monkeypatch):
    clk = Clock()
    eng = _engine(monkeypatch, clk)
    eng._hardware_aec = True   # trust board AEC: no startup mute, mic passes through
    eng._player._playing = True
    quiet = _frame(200)
    assert eng.process(quiet) == quiet  # first playing frame is NOT muted
    # local duck still works on sustained loud input
    loud = _frame(5000)
    eng.process(loud)
    eng.process(loud)
    assert eng._player.gain == wm.DUCK_GAIN
