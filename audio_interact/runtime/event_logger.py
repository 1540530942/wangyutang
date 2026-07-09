from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class EventLogger:
    """Small JSONL writer used by online capture and offline replay."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, **event: Any) -> dict[str, Any]:
        clean = {k: v for k, v in event.items() if v is not None}
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + "\n")
        return clean

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return EventLogger(path).read_all()
