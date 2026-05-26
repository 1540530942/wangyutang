from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from urllib import request

from common.settings import settings


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")


def estimate_tokens(text: object) -> int:
    value = str(text or "")
    if not value:
        return 0
    cjk_count = len(_CJK_RE.findall(value))
    non_cjk = _CJK_RE.sub(" ", value)
    latin_count = len(_LATIN_RE.findall(non_cjk))
    return cjk_count + latin_count


def messages_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        parts.append(str(message.get("role") or ""))
        parts.append(str(message.get("content") or ""))
    return "\n".join(parts)


def report_model_usage(
    *,
    model: str,
    capability: str,
    input_text: object = "",
    output_text: object = "",
    raw_usage: dict[str, Any] | None = None,
) -> None:
    if not settings.model_usage_collector_url:
        return
    input_tokens = estimate_tokens(input_text)
    output_tokens = estimate_tokens(output_text)
    usage = raw_usage or {}
    event = {
        "ts": time.time(),
        "model": model,
        "capability": capability,
        "input_tokens": int(usage.get("prompt_tokens") or input_tokens),
        "output_tokens": int(usage.get("completion_tokens") or output_tokens),
        "total_tokens": int(usage.get("total_tokens") or input_tokens + output_tokens),
        "estimated": not bool(raw_usage),
    }
    threading.Thread(target=_post_usage_event, args=(event,), daemon=True).start()


def _post_usage_event(event: dict[str, Any]) -> None:
    body = json.dumps(event).encode("utf-8")
    req = request.Request(
        settings.model_usage_collector_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        request.urlopen(req, timeout=2).close()
    except Exception:
        return
