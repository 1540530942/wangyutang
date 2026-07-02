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

import pytest
import requests
from PIL import Image

BASE = os.getenv("API_BASE", "https://www.wangyutang.cn/common")
SHORT = 15   # seconds for health/read endpoints
CHAT  = 60   # seconds for LLM inference
VISION = 30  # seconds for vision inference


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
    r = requests.post(
        f"{BASE}/api/llm/chat",
        json={"messages": [{"role": "user", "content": "只回答数字：1+1="}], "max_tokens": 50},
        timeout=CHAT,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "lv_qwen_chat", body
    assert body.get("text", "").strip(), f"empty text in: {body}"


def test_dashscope_chat_returns_text():
    r = requests.post(
        f"{BASE}/api/llm/qwen3-32b/chat",
        json={"messages": [{"role": "user", "content": "只回答数字：2+2="}], "max_tokens": 20},
        timeout=CHAT,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("text", "").strip(), f"empty text in: {body}"


def test_vision_lv_analyze_json():
    r = requests.post(
        f"{BASE}/api/vision/lv/analyze-json",
        json={"image_base64": _red_jpeg_b64(), "question": "图片主要是什么颜色？"},
        timeout=VISION,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "lv_qwen_vision", body
    assert body.get("text", "").strip(), f"empty text in: {body}"


def test_vision_spark_analyze_json():
    r = requests.post(
        f"{BASE}/api/vision/spark/analyze-json",
        json={"image_base64": _red_jpeg_b64(), "question": "图片主要是什么颜色？"},
        timeout=VISION,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") == "spark_qwen_vision", body
    assert body.get("text", "").strip(), f"empty text in: {body}"
