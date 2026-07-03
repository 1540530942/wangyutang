"""Smoke tests for common-api live endpoints at wangyutang.cn.

Run after deployment to verify correctness. Failure means pipeline rollback.

Usage:
    pytest tests/test_smoke.py -v
    API_BASE=https://www.wangyutang.cn/common pytest tests/test_smoke.py -v
"""
from __future__ import annotations

import base64
import io
import os
import time

import pytest
import requests
from PIL import Image

BASE = os.getenv("API_BASE", "https://www.wangyutang.cn/common")
SHORT = 15   # seconds for health/read endpoints
CHAT  = 90   # seconds for LLM inference (35B model can be slow post-restart)
VISION = 45  # seconds for vision inference


def _call_with_retry(fn, retries: int = 2, delay: float = 5.0):
    """Retry fn() on exception or non-200 up to retries times."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            r = fn()
            if r.status_code == 200 or attempt == retries:
                return r
            time.sleep(delay)
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(delay)
    if last_exc:
        raise last_exc
    return r


def _red_jpeg_b64(width: int = 64, height: int = 64) -> str:
    img = Image.new("RGB", (width, height), (200, 40, 40))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


# ── health checks ──────────────────────────────────────────────────────────

def test_api_health():
    r = requests.get(f"{BASE}/api/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok", body


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
    body = r.json()
    assert body["provider"] == "lv_qwen_chat", body


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


# ── functional tests ───────────────────────────────────────────────────────

def test_lv_chat_returns_text():
    r = _call_with_retry(
        lambda: requests.post(
            f"{BASE}/api/llm/chat",
            json={"messages": [{"role": "user", "content": "只回答数字：1+1="}], "max_tokens": 50},
            timeout=CHAT,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "lv_qwen_chat", body
    assert body.get("text", "").strip(), f"empty text in: {body}"


def test_dashscope_chat_returns_text():
    r = _call_with_retry(
        lambda: requests.post(
            f"{BASE}/api/llm/qwen3-32b/chat",
            json={"messages": [{"role": "user", "content": "只回答数字：2+2="}], "max_tokens": 20},
            timeout=CHAT,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("text", "").strip(), f"empty text in: {body}"


def test_vision_lv_analyze_json():
    r = _call_with_retry(
        lambda: requests.post(
            f"{BASE}/api/vision/lv/analyze-json",
            json={"image_base64": _red_jpeg_b64(), "question": "图片主要是什么颜色？"},
            timeout=VISION,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "lv_qwen_vision", body
    assert body.get("text", "").strip(), f"empty text in: {body}"


def test_vision_spark_analyze_json():
    r = _call_with_retry(
        lambda: requests.post(
            f"{BASE}/api/vision/spark/analyze-json",
            json={"image_base64": _red_jpeg_b64(), "question": "图片主要是什么颜色？"},
            timeout=VISION,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "spark_qwen_vision", body
    assert body.get("text", "").strip(), f"empty text in: {body}"


def test_vision_dashscope_health():
    r = requests.get(f"{BASE}/api/vision/dashscope/health", timeout=SHORT)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "dashscope_qwen_vision"


def test_vision_dashscope_analyze_json():
    r = _call_with_retry(
        lambda: requests.post(
            f"{BASE}/api/vision/dashscope/analyze-json",
            json={"image_base64": _red_jpeg_b64(), "question": "图片主要是什么颜色？"},
            timeout=VISION,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "dashscope_qwen_vision", body
    assert body.get("text", "").strip(), f"empty text in: {body}"


def test_spark_llm_returns_text():
    r = _call_with_retry(
        lambda: requests.post(
            f"{BASE}/api/llm/qwen3.6-35b/chat",
            json={"messages": [{"role": "user", "content": "只回答数字：3+3="}], "max_tokens": 20},
            timeout=CHAT,
        )
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "spark_qwen_chat", body
    assert body.get("text", "").strip(), f"empty text in: {body}"
