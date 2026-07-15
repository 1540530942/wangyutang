"""Smoke tests for common-api live endpoints at wangyutang.cn.

Run after deployment to verify correctness. Failure means pipeline rollback.

Tests are split into two tiers:
  - fast  (default): health probes + lightweight functional tests  (<30s total)
  - slow  (mark: slow): heavy LLM/vision inference, skipped in time-constrained CI

Usage:
    pytest tests/test_smoke.py -v                       # all tests
    pytest tests/test_smoke.py -v -m "not slow"         # fast tier only (CI default)
    API_BASE=https://www.wangyutang.cn/common pytest tests/test_smoke.py -v
"""
from __future__ import annotations

import base64
import io
import os
import struct
import time

import pytest
import requests
from PIL import Image

BASE = os.getenv("API_BASE", "https://www.wangyutang.cn/common")
SHORT = 15    # health / model-list endpoints
TTS   = 30    # TTS synthesis (short text, ~10s p50)
CHAT  = 90    # LLM inference single request
VISION = 45   # vision inference single request


def _call(fn, *, retries: int = 1, delay: float = 3.0):
    """Call fn(); on non-200 or exception, retry up to `retries` times."""
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            r = fn()
            if r.status_code == 200 or attempt == retries:
                return r
        except Exception as exc:
            last_exc = exc
            if attempt == retries:
                raise
        time.sleep(delay)
    if last_exc:
        raise last_exc
    return r  # type: ignore[return-value]


def _red_jpeg_b64(width: int = 64, height: int = 64) -> str:
    img = Image.new("RGB", (width, height), (200, 40, 40))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


# ── Tier 1: health checks (fast, always run) ──────────────────────────────

def test_api_health():
    r = requests.get(f"{BASE}/api/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"


def test_asr_health():
    r = requests.get(f"{BASE}/api/asr/qwen3/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "lv_qwen_asr"


def test_tts_health():
    r = requests.get(f"{BASE}/api/tts/qwen3/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "lv_qwen_tts"


def test_chat_health():
    r = requests.get(f"{BASE}/api/chat/qwen3/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "lv_qwen_chat"


def test_dashscope_llm_health():
    r = requests.get(f"{BASE}/api/llm/qwen3-32b/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "dashscope_qwen_chat"


def test_spark_llm_health():
    r = requests.get(f"{BASE}/api/llm/qwen3.6-35b/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "spark_qwen_chat"


def test_vision_lv_health():
    r = requests.get(f"{BASE}/api/vision/lv/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "lv_qwen_vision"


def test_vision_spark_health():
    r = requests.get(f"{BASE}/api/vision/spark/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "spark_qwen_vision"


def test_vision_dashscope_health():
    r = requests.get(f"{BASE}/api/vision/dashscope/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "dashscope_qwen_vision"


# ── Tier 2: lightweight functional tests (fast, always run) ───────────────

def test_tts_speech_returns_audio():
    """TTS synthesis: assert WAV bytes returned within TTS timeout."""
    r = _call(
        lambda: requests.post(
            f"{BASE}/api/tts/speech",
            json={"input": "向右看", "voice": "vivian"},
            timeout=TTS,
        )
    )
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"RIFF", "response is not WAV"
    assert len(r.content) > 1000, "audio too short"


def test_tts_speech_stream_returns_chunks():
    """Streaming TTS: assert at least one [4B len][WAV] chunk arrives."""
    r = requests.post(
        f"{BASE}/api/tts/speech/stream",
        json={"input": "向右看", "voice": "vivian"},
        timeout=TTS,
        stream=True,
    )
    assert r.status_code == 200, r.text
    buf = b""
    got_chunk = False
    for raw in r.iter_content(chunk_size=4096):
        buf += raw
        while len(buf) >= 4:
            length = struct.unpack(">I", buf[:4])[0]
            if length == 0:
                break
            if len(buf) < 4 + length:
                break
            wav = buf[4 : 4 + length]
            assert wav[:4] == b"RIFF", "chunk is not WAV"
            got_chunk = True
            buf = buf[4 + length :]
        if got_chunk:
            break
    assert got_chunk, "no audio chunk received from stream endpoint"


# ── Tier 3: heavy inference tests (slow, skipped in time-constrained CI) ──

@pytest.mark.slow
def test_lv_chat_returns_text():
    r = _call(
        lambda: requests.post(
            f"{BASE}/api/llm/chat",
            json={"messages": [{"role": "user", "content": "只回答数字：1+1="}], "max_tokens": 50},
            timeout=CHAT,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "lv_qwen_chat", body
    assert body.get("text", "").strip(), f"empty text: {body}"


@pytest.mark.slow
def test_dashscope_chat_returns_text():
    r = _call(
        lambda: requests.post(
            f"{BASE}/api/llm/qwen3-32b/chat",
            json={"messages": [{"role": "user", "content": "只回答数字：2+2="}], "max_tokens": 20},
            timeout=CHAT,
        )
    )
    assert r.status_code == 200, r.text
    assert r.json().get("text", "").strip(), f"empty text: {r.text}"


@pytest.mark.slow
def test_spark_llm_returns_text():
    r = _call(
        lambda: requests.post(
            f"{BASE}/api/llm/qwen3.6-35b/chat",
            json={"messages": [{"role": "user", "content": "只回答数字：3+3="}], "max_tokens": 20},
            timeout=CHAT,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "spark_qwen_chat", body
    assert body.get("text", "").strip(), f"empty text: {body}"


@pytest.mark.slow
def test_vision_lv_analyze_json():
    r = _call(
        lambda: requests.post(
            f"{BASE}/api/vision/lv/analyze-json",
            json={"image_base64": _red_jpeg_b64(), "question": "图片主要是什么颜色？"},
            timeout=VISION,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "lv_qwen_vision", body
    assert body.get("text", "").strip(), f"empty text: {body}"


@pytest.mark.slow
def test_vision_spark_analyze_json():
    r = _call(
        lambda: requests.post(
            f"{BASE}/api/vision/spark/analyze-json",
            json={"image_base64": _red_jpeg_b64(), "question": "图片主要是什么颜色？"},
            timeout=VISION,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "spark_qwen_vision", body
    assert body.get("text", "").strip(), f"empty text: {body}"


@pytest.mark.slow
def test_vision_dashscope_analyze_json():
    r = _call(
        lambda: requests.post(
            f"{BASE}/api/vision/dashscope/analyze-json",
            json={"image_base64": _red_jpeg_b64(), "question": "图片主要是什么颜色？"},
            timeout=VISION,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "dashscope_qwen_vision", body
    assert body.get("text", "").strip(), f"empty text: {body}"
