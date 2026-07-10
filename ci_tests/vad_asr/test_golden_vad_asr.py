"""金标 VAD+ASR 远端集成测试 — 单工(simplex)场景。

将金标音频逐帧流式发送到远端 audio-interact WebSocket，
收集识别结果，与 golden JSON 预期值对比。

运行方式（repo 根目录）：
    pytest ci_tests/audio_interact/test_golden_vad_asr.py -v
"""
from __future__ import annotations

import asyncio
import json
import struct
import uuid
import wave
from pathlib import Path
from typing import Any

import pytest

_GOLDEN_DIR  = Path(__file__).resolve().parents[2] / "audio_interact" / "tests" / "golden"
_GOLDEN_JSON = _GOLDEN_DIR / "vad_asr_simplex_001.json"
_GOLDEN_WAV  = _GOLDEN_DIR / "audio" / "vad_asr_simplex_001.wav"

_WS_PATH      = "/audio_interact/ws/audio"
_FRAME_SAMPLES = 512     # 必须与服务端 FRAME_SAMPLES 一致
_SAMPLE_RATE   = 16000
_VAD_TOL_MS    = 400     # VAD 起止允许误差 ±400ms
_SILENCE_PAD_FRAMES = 25  # 末尾补 25 帧静音，确保最后一句 VAD 自然关闭


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_golden() -> dict[str, Any]:
    return json.loads(_GOLDEN_JSON.read_text(encoding="utf-8"))


def _read_pcm(wav_path: Path) -> bytes:
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1, "golden audio must be mono"
        assert wf.getsampwidth() == 2, "golden audio must be PCM16"
        assert wf.getframerate() == _SAMPLE_RATE, f"sample rate must be {_SAMPLE_RATE}"
        return wf.readframes(wf.getnframes())


def _silence_frame() -> bytes:
    return b"\x00" * (_FRAME_SAMPLES * 2)


async def _replay_and_collect(base_ws_url: str) -> list[dict[str, Any]]:
    """Connect WS, stream golden audio, return list of utterance results."""
    import websockets  # optional dep; skip test if absent

    pcm = _read_pcm(_GOLDEN_WAV)
    silence_pad = _silence_frame() * _SILENCE_PAD_FRAMES
    full_pcm = pcm + silence_pad

    session_id = f"ci-golden-{uuid.uuid4().hex[:8]}"
    ws_url = base_ws_url + _WS_PATH

    utterances: list[dict[str, Any]] = []

    async with websockets.connect(ws_url, ping_interval=None, open_timeout=20) as ws:
        # 1. 启动流（route=false，不驱动真实机器人）
        await ws.send(json.dumps({
            "type": "start_stream",
            "session_id": session_id,
            "device_id": "ci-test",
            "sample_rate": _SAMPLE_RATE,
            "route": False,
        }))

        # 等待 ready
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        assert ready.get("type") == "ready", f"expected ready, got {ready}"

        # 2. 逐帧发送 PCM（尽快发送，VAD 基于帧数非壁钟时间）
        frame_size = _FRAME_SAMPLES * 2
        for i in range(0, len(full_pcm), frame_size):
            frame = full_pcm[i: i + frame_size]
            if len(frame) < frame_size:
                frame = frame.ljust(frame_size, b"\x00")
            await ws.send(frame)

        # 3. 停止流
        await ws.send(json.dumps({"type": "stop_stream"}))

        # 4. 收集消息直到 stream_stopped
        deadline = asyncio.get_event_loop().time() + 60
        pending_start_ms: float | None = None

        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, bytes):
                continue  # TTS 音频帧，跳过
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "speech_start":
                pending_start_ms = int((msg.get("offset_seconds") or 0) * 1000)

            elif msg_type == "speech_end":
                pass  # start_ms 已在 speech_start 记录

            elif msg_type == "stream_stopped":
                break

            elif "streaming_vad" in msg or ("text" in msg and "wake_status" in msg):
                # 这是每句话的 ASR 结果
                utterances.append({
                    "vad_start_ms": pending_start_ms,
                    "vad_end_ms": int((msg.get("offset_seconds") or 0) * 1000),
                    "text": msg.get("text", ""),
                    "wake_status": msg.get("wake_status", ""),
                    "status": msg.get("status", ""),
                    "skill_id": msg.get("skill_id", ""),
                })
                pending_start_ms = None

    return utterances


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def golden():
    if not _GOLDEN_JSON.exists():
        pytest.skip(f"golden JSON not found: {_GOLDEN_JSON}")
    if not _GOLDEN_WAV.exists():
        pytest.skip(f"golden WAV not found: {_GOLDEN_WAV}")
    return _load_golden()


@pytest.fixture(scope="module")
def replay_results(golden):
    """实际跑一次 WS 回放，module 级别只跑一次，所有 test 共享结果。"""
    try:
        import websockets  # noqa: F401
    except ImportError:
        pytest.skip("websockets library not installed")

    from ci_tests.conftest import WS_URL

    try:
        results = asyncio.run(_replay_and_collect(WS_URL))
    except Exception as exc:
        pytest.fail(f"WS replay failed: {exc}")

    if not results:
        pytest.fail("WS replay returned no utterances — VAD 未检测到语音")

    return results


# ── 断言 ──────────────────────────────────────────────────────────────────────

def test_utterance_count(golden, replay_results):
    """句数必须与金标一致。"""
    expected = len(golden["utterances"])
    actual   = len(replay_results)
    assert actual == expected, (
        f"期望 {expected} 句，实际 {actual} 句\n"
        f"实际: {[r['text'] for r in replay_results]}"
    )


@pytest.mark.parametrize("idx", range(6))
def test_asr_text(idx, golden, replay_results):
    """每句 ASR 文本必须完全匹配（标点不敏感：去掉标点比较）。"""
    if idx >= len(replay_results):
        pytest.fail(f"第 {idx} 句不存在（共 {len(replay_results)} 句）")

    import re
    strip = lambda s: re.sub(r"[，。？！、,.?!\s]", "", s)

    exp = golden["utterances"][idx]["expected_text"]
    got = replay_results[idx]["text"]
    assert strip(got) == strip(exp), (
        f"[{idx}] ASR 文本不符\n  期望: {exp!r}\n  实际: {got!r}"
    )


@pytest.mark.parametrize("idx", range(6))
def test_wake_status(idx, golden, replay_results):
    """唤醒状态必须与金标一致。"""
    if idx >= len(replay_results):
        pytest.fail(f"第 {idx} 句不存在")
    exp = golden["utterances"][idx]["expected_wake_status"]
    got = replay_results[idx]["wake_status"]
    assert got == exp, f"[{idx}] wake_status 期望={exp!r} 实际={got!r}"


@pytest.mark.parametrize("idx", range(6))
def test_vad_timing(idx, golden, replay_results):
    """VAD 起止时间误差在 ±400ms 以内。"""
    if idx >= len(replay_results):
        pytest.fail(f"第 {idx} 句不存在")
    exp_u = golden["utterances"][idx]
    got_u = replay_results[idx]

    for key in ("vad_start_ms", "vad_end_ms"):
        exp_ms = exp_u[key]
        got_ms = got_u.get(key)
        if got_ms is None:
            continue
        diff = abs(got_ms - exp_ms)
        assert diff <= _VAD_TOL_MS, (
            f"[{idx}] {key} 误差 {diff}ms > {_VAD_TOL_MS}ms\n"
            f"  期望={exp_ms}ms 实际={got_ms}ms"
        )
