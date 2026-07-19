"""Server /ws/audio full-duplex pipeline: turn queue, ordering, streaming TTS, barge-in.

Exercises audio_ws with a fake WebSocket + fake VAD so it runs without torch. Guarded
by importorskip(fastapi) since some CI images ship without it.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "audio_interact"))
os.environ.setdefault("AUDIO_INTERACT_DATA_DIR", tempfile.mkdtemp(prefix="ai_ci_"))

import server  # noqa: E402


class FakeVAD:
    """Drop-in StreamingSileroVad; emits scripted events by feed() call index."""
    _script: dict = {}

    def __init__(self, session_id: str, device_id: str, **kw):
        self.session_id = session_id
        self.device_id = device_id
        self.tts_active = False
        self.calls = 0

    def feed(self, b: bytes):
        self.calls += 1
        return [dict(e) for e in self._script.get(self.calls, [])]

    def finish(self):
        return None


class FakeWS:
    def __init__(self, script):
        self.script = list(script)
        self.i = 0
        self.sent = []

    async def accept(self):
        pass

    async def receive(self):
        if self.i >= len(self.script):
            return {"type": "websocket.disconnect"}
        item = self.script[self.i]
        self.i += 1
        if "__delay__" in item:
            await asyncio.sleep(item["__delay__"])
        return {k: v for k, v in item.items() if k != "__delay__"}

    async def send_text(self, s):
        self.sent.append(("text", s))

    async def send_bytes(self, b):
        self.sent.append(("bytes", b))


def _text_types(ws):
    return [json.loads(p).get("type") for k, p in ws.sent if k == "text"]


def _nbytes(ws):
    return sum(1 for k, _ in ws.sent if k == "bytes")


async def _run(monkeypatch, script, vad_script, tts_chunks, chunk_sleep=0.0):
    FakeVAD._script = vad_script
    monkeypatch.setattr(server, "StreamingSileroVad", FakeVAD)
    monkeypatch.setattr(server, "TTS_URL", "http://tts")
    monkeypatch.setattr(server, "_sse_broadcast", lambda payload: None)
    monkeypatch.setattr(server, "save_session_recording", lambda *a, **k: "rec/x")
    monkeypatch.setattr(server, "_fetch_tts_audio", lambda text: b"RIFFfake")

    def fake_process(wav, device_id, session_id, route):
        return {
            "type": "result", "session_id": session_id, "text": "你好",
            "wake_status": "awake", "skill_id": "", "status": "ok",
            "tts_text": "你好呀我在", "asr_elapsed_ms": 12, "route_elapsed_ms": 20,
        }
    monkeypatch.setattr(server, "_process", fake_process)

    def fake_stream(text, **kw):
        for c in tts_chunks:
            if chunk_sleep:
                time.sleep(chunk_sleep)
            yield c
    monkeypatch.setattr(server, "_iter_tts_stream", fake_stream)

    ws = FakeWS(script)
    await asyncio.wait_for(server.audio_ws(ws), timeout=10)
    return ws


_SPEECH = {
    1: [{"type": "speech_start", "offset_seconds": 0.1}],
    2: [{"type": "speech_end", "offset_seconds": 1.0, "reason": "silence", "wav_bytes": b"RIFFmic"}],
}


def _ordered(types, expected):
    last = -1
    for et in expected:
        try:
            last = types.index(et, last + 1)
        except ValueError:
            raise AssertionError(f"{et} not found after index {last} in {types}")


def test_proto1_legacy_ordering_and_base64(monkeypatch):
    script = [
        {"text": json.dumps({"type": "start_stream", "session_id": "s1", "route": True})},
        {"bytes": b"\x00" * 1024},
        {"bytes": b"\x00" * 1024},
        {"text": json.dumps({"type": "stop_stream"})},
    ]
    ws = asyncio.run(_run(monkeypatch, script, _SPEECH, [b"RIFF1"]))
    _ordered(_text_types(ws), ["stream_ready", "speech_start", "asr_started", "result", "stream_stopped"])
    res = next(json.loads(p) for k, p in ws.sent if k == "text" and json.loads(p).get("type") == "result")
    assert "tts_audio_base64" in res  # legacy embeds audio
    assert _nbytes(ws) >= 1


def test_proto2_streaming_no_base64(monkeypatch):
    script = [
        {"text": json.dumps({"type": "start_stream", "session_id": "s2", "route": True, "proto": 2})},
        {"bytes": b"\x00" * 1024},
        {"bytes": b"\x00" * 1024},
        {"text": json.dumps({"type": "stop_stream"})},
    ]
    ws = asyncio.run(_run(monkeypatch, script, _SPEECH, [b"RIFFa", b"RIFFb", b"RIFFc"]))
    types = _text_types(ws)
    _ordered(types, ["stream_ready", "speech_start", "asr_started", "result", "tts_begin", "stream_stopped"])
    res = next(json.loads(p) for k, p in ws.sent if k == "text" and json.loads(p).get("type") == "result")
    assert "tts_audio_base64" not in res
    assert _nbytes(ws) == 4  # 3 sentence chunks + b"" sentinel


def test_bargein_cancels_streaming_tts(monkeypatch):
    script = [
        {"text": json.dumps({"type": "start_stream", "session_id": "s3", "route": True, "proto": 2})},
        {"bytes": b"\x00" * 1024},
        {"bytes": b"\x00" * 1024},
        {"__delay__": 0.18, "bytes": b"\x00" * 1024},       # barge-in mid-TTS
        {"__delay__": 0.8, "text": json.dumps({"type": "stop_stream"})},
    ]
    vad = dict(_SPEECH)
    vad[3] = [{"type": "speech_start", "offset_seconds": 2.0}]
    ws = asyncio.run(_run(monkeypatch, script, vad,
                          [b"RIFF%d" % n for n in range(6)], chunk_sleep=0.08))
    types = _text_types(ws)
    assert "tts_cancel" in types, types
    assert _nbytes(ws) < 6  # streaming cut short
    # cancelled turn must not emit the b"" stream-end sentinel
    assert ws.sent[-1][0] == "text"
