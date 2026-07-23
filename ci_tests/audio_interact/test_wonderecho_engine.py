"""Pi-side listener (PipeWire echo-cancel architecture) unit tests.

Stdlib only — no PipeWire/torch. Covers URL derivation, settings parsing, the
echo-cancel load args, and the killable playback queue lifecycle.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "audio_interact" / "edge"))

import wonderecho_listener as wm  # noqa: E402


def test_ws_url_scheme():
    assert wm._ws_url("https://x/audio_interact") == "wss://x/audio_interact/ws/audio"
    assert wm._ws_url("http://x/ai") == "ws://x/ai/ws/audio"
    assert wm._ws_url("host/ai") == "wss://host/ai/ws/audio"


def test_echo_cancel_load_args():
    args = " ".join(wm._EC_LOAD_ARGS)
    assert "aec_method=webrtc" in args
    assert f"source_name={wm.EC_SOURCE}" in args
    assert f"sink_name={wm.EC_SINK}" in args
    assert "webrtc.extended_filter=1" in args  # the tuning that gave ~31 dB ERLE


def test_get_cloud_settings(monkeypatch):
    monkeypatch.setattr(wm, "_get_json", lambda url, tok, timeout=8.0: {
        "settings": {"manual_recording_enabled": True, "input_mode": "wonderechopro"}})
    s = wm.get_cloud_settings("https://x", "")
    assert s["manual_recording_enabled"] is True
    # unreachable server → None so the caller keeps the session as-is (no false stop)
    monkeypatch.setattr(wm, "_get_json", lambda url, tok, timeout=8.0: None)
    assert wm.get_cloud_settings("https://x", "") is None


def test_playback_queue_and_close(monkeypatch):
    # Replace _play with a fast no-op so no real pw-play is spawned.
    played: list[int] = []

    def fake_play(self, wav):
        played.append(len(wav))
        time.sleep(0.02)

    monkeypatch.setattr(wm._Playback, "_play", fake_play)
    pb = wm._Playback()
    pb.enqueue_wav(b"RIFF" + b"\x00" * 100)
    time.sleep(0.1)
    assert played, "worker should have played the queued chunk"
    assert not pb.playing  # drained
    pb.close()
    pb._thread.join(timeout=2)
    assert not pb._thread.is_alive()


def test_playback_kill_drains_queue(monkeypatch):
    # _play blocks until released, so we can observe kill() draining the backlog.
    import threading
    gate = threading.Event()

    def slow_play(self, wav):
        gate.wait(1.0)

    monkeypatch.setattr(wm._Playback, "_play", slow_play)
    pb = wm._Playback()
    for _ in range(5):
        pb.enqueue_wav(b"x" * 10)
    time.sleep(0.05)
    assert pb.playing
    pb.kill()            # drop the 4 queued + stop current
    gate.set()
    time.sleep(0.1)
    assert pb._q.qsize() == 0
    pb.close()
