from __future__ import annotations

"""Lab model catalog + strict real-inference validation.

Exposes the full registry of callable models with their live health and their
interface paths (current + historical aliases), and a validation endpoint that
runs a *real* inference against a model and returns the actual output + latency
so the lab page can prove a model really works rather than displaying a
hard-coded "online" badge.
"""

import time
from typing import Any

import requests
from fastapi import APIRouter
from pydantic import BaseModel, Field


router = APIRouter()

# Every model the platform can really call. `paths` lists the primary interface
# first, then historical/alias paths that still route. `health` is a GET probe;
# `validate` describes a cheap real inference used for strict verification.
MODEL_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "dashscope-qwen3-32b",
        "name": "Qwen3-32B (DashScope)",
        "provider": "DashScope",
        "type": "text",
        "paths": ["/common/api/llm/qwen3-32b/chat/completions", "/common/api/llm/dashscope/chat/completions"],
        "health": "/common/api/llm/qwen3-32b/health",
        "models": "/common/api/llm/qwen3-32b/models",
        "validate": {"kind": "chat", "path": "/common/api/llm/qwen3-32b/chat/completions", "model": "qwen3-32b"},
    },
    {
        "id": "spark-qwen3.6-35b",
        "name": "Qwen3.6-35B (Spark)",
        "provider": "Spark",
        "type": "text",
        "paths": ["/common/api/llm/spark-qwen/chat/completions", "/common/api/llm/qwen3.6-35b/chat/completions"],
        "health": "/common/api/llm/spark-qwen/health",
        "models": "/common/api/llm/spark-qwen/models",
        "validate": {"kind": "chat", "path": "/common/api/llm/spark-qwen/chat/completions", "model": "qwen3.6-35b-a3b-fp8"},
    },
    {
        "id": "lv-qwen-chat",
        "name": "Qwen Chat (LV self-host)",
        "provider": "LV",
        "type": "text",
        "paths": ["/common/api/chat/qwen3/completions", "/common/api/llm/chat"],
        "health": "/common/api/chat/qwen3/health",
        "models": "/common/api/chat/qwen3/models",
        "validate": {"kind": "chat", "path": "/common/api/chat/qwen3/completions", "model": "qwen3"},
    },
    {
        "id": "lv-qwen-vision",
        "name": "Qwen2.5-VL-7B (LV self-host)",
        "provider": "LV",
        "type": "vision",
        "paths": ["/common/api/vision/lv/analyze-json"],
        "health": "/common/api/vision/lv/health",
        "models": "/common/api/vision/lv/models",
        "validate": {"kind": "vision", "path": "/common/api/vision/lv/analyze-json", "model": ""},
    },
    {
        "id": "spark-qwen-vision",
        "name": "Qwen-VL (Spark vision)",
        "provider": "Spark",
        "type": "vision",
        "paths": ["/common/api/vision/spark/analyze-json", "/common/api/vision/spark-qwen/analyze-json"],
        "health": "/common/api/vision/spark/health",
        "models": "/common/api/vision/spark/models",
        "validate": {"kind": "vision", "path": "/common/api/vision/spark/analyze-json", "model": "qwen3.6-35b-a3b-fp8"},
    },
    {
        "id": "dashscope-qwen-vision",
        "name": "Qwen-VL (DashScope vision)",
        "provider": "DashScope",
        "type": "vision",
        "paths": ["/common/api/vision/qwen/analyze-json", "/common/api/vision/dashscope/analyze-json"],
        "health": "/common/api/vision/qwen/health",
        "models": "/common/api/vision/qwen/models",
        "validate": {"kind": "vision", "path": "/common/api/vision/qwen/analyze-json", "model": ""},
    },
    {
        "id": "lv-qwen-asr",
        "name": "Qwen3 ASR (LV)",
        "provider": "LV",
        "type": "asr",
        "paths": ["/common/api/asr/qwen3/transcribe", "/common/api/asr/transcribe", "/common/api/transcribe"],
        "health": "/common/api/asr/qwen3/health",
        "models": "/common/api/asr/qwen3/models",
        "validate": {"kind": "health_only"},
    },
    {
        "id": "lv-qwen-tts",
        "name": "Qwen3 TTS (LV)",
        "provider": "LV",
        "type": "tts",
        "paths": ["/common/api/tts/qwen3/speech", "/common/api/tts/speech", "/common/api/tts/synthesize"],
        "health": "/common/api/tts/qwen3/health",
        "models": "/common/api/tts/qwen3/models",
        "validate": {"kind": "health_only"},
    },
]

# A real 32x32 red/blue checker PNG — large enough for vision models to accept
# (a 1x1 pixel often makes them error). Minimal but genuine for validation.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAOUlEQVR42mO4Y2ODFdlEncCKSFXPMGrBqAVDwAJqGYRL"
    "/agFoxYMBQtGi4pRC0YtGK0PRi0YtQCIAF5wZEzMocn+AAAAAElFTkSuQmCC"
)


class ValidateRequest(BaseModel):
    model_id: str = Field(..., max_length=80)
    base_url: str = Field("http://127.0.0.1:8101", max_length=200)


@router.get("/api/lab/catalog")
def catalog() -> dict[str, Any]:
    """Return the model registry (paths + historical aliases). Health/validation
    are probed by the browser against each model's own endpoint."""
    return {
        "count": len(MODEL_REGISTRY),
        "models": [{k: v for k, v in m.items() if k != "validate"} | {"validatable": m["validate"]["kind"] != "health_only"} for m in MODEL_REGISTRY],
    }


@router.post("/api/lab/validate")
def validate(req: ValidateRequest) -> dict[str, Any]:
    """Run a real inference against one model and return the actual output +
    latency. Strict: a model is only "verified" if it returns a real response."""
    spec = next((m for m in MODEL_REGISTRY if m["id"] == req.model_id), None)
    if spec is None:
        return {"ok": False, "error": f"unknown model_id: {req.model_id}"}
    v = spec["validate"]
    base = req.base_url.rstrip("/")
    # The registry paths are public (/common/...); strip the /common prefix for
    # the in-cluster base which is already rooted at the service.
    def _local(path: str) -> str:
        return base + (path[len("/common"):] if path.startswith("/common/") else path)

    started = time.time()
    try:
        if v["kind"] == "chat":
            resp = requests.post(
                _local(v["path"]),
                json={"model": v["model"], "messages": [{"role": "user", "content": "只回答数字: 2+3=?"}], "max_tokens": 16, "stream": False},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            text = ""
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                text = str(data)[:200]
            verified = "5" in (text or "")
            return {"ok": True, "verified": verified, "elapsed_ms": int((time.time() - started) * 1000), "sample_prompt": "2+3=?", "output": (text or "").strip()[:300]}
        if v["kind"] == "vision":
            resp = requests.post(
                _local(v["path"]),
                json={"image_base64": _TINY_PNG_B64, "question": "描述这张图", **({"model": v["model"]} if v.get("model") else {})},
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()
            text = str(data.get("text") or data.get("answer") or "")
            return {"ok": True, "verified": bool(text.strip()), "elapsed_ms": int((time.time() - started) * 1000), "output": text.strip()[:300]}
        return {"ok": False, "error": "not auto-validatable (needs audio); use health/models probe"}
    except requests.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.response.status_code}", "detail": (exc.response.text or "")[:200], "elapsed_ms": int((time.time() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200], "elapsed_ms": int((time.time() - started) * 1000)}
