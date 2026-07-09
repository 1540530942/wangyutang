from __future__ import annotations

import re
import time
import uuid

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_id(value: str | None, *, prefix: str = "session") -> str:
    cleaned = _SAFE_ID_RE.sub("_", (value or "").strip()).strip("._-")
    return cleaned or f"{prefix}_{uuid.uuid4().hex[:12]}"


def new_session_id(prefix: str = "session") -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def ordinal_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index + 1:03d}"

