from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
import wave
from pathlib import Path
from typing import Any


STORE_LOCK = threading.Lock()
MAX_CASES = 500


def ensure_store(data_dir: Path) -> dict[str, Path]:
    root = data_dir / "intermediate"
    audio_dir = root / "audio"
    root.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "audio": audio_dir,
        "cases": root / "cases.jsonl",
        "latest": root / "latest_case.json",
    }


def build_case_id(prefix: str = "case") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"


def safe_filename(name: str, default: str = "recording.wav") -> str:
    candidate = Path(name or default).name
    if not candidate or candidate in {".", ".."}:
        return default
    return candidate


def wav_duration_seconds(path: Path) -> float:
    if path.suffix.lower() != ".wav":
        return 0.0
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
        return frames / rate if rate else 0.0
    except (OSError, wave.Error):
        return 0.0


def copy_audio_file(
    *,
    data_dir: Path,
    case_id: str,
    source_path: str | Path,
    original_filename: str = "",
) -> dict[str, Any]:
    paths = ensure_store(data_dir)
    source = Path(source_path)
    if not source.exists() or not source.is_file():
        return {}
    filename = safe_filename(original_filename or source.name)
    target = paths["audio"] / f"{case_id}-{filename}"
    shutil.copy2(source, target)
    return {
        "audio_path": str(target.relative_to(data_dir).as_posix()),
        "audio_bytes": target.stat().st_size,
        "audio_duration_seconds": wav_duration_seconds(target),
    }


def write_audio_bytes(
    *,
    data_dir: Path,
    case_id: str,
    content: bytes,
    original_filename: str = "",
    content_type: str = "",
) -> dict[str, Any]:
    paths = ensure_store(data_dir)
    filename = safe_filename(original_filename)
    target = paths["audio"] / f"{case_id}-{filename}"
    target.write_bytes(content)
    return {
        "audio_path": str(target.relative_to(data_dir).as_posix()),
        "audio_bytes": len(content),
        "audio_mime": content_type,
        "audio_duration_seconds": wav_duration_seconds(target),
    }


def load_cases(data_dir: Path, limit: int = 100) -> list[dict[str, Any]]:
    paths = ensure_store(data_dir)
    if not paths["cases"].exists():
        return []
    items: list[dict[str, Any]] = []
    for line in paths["cases"].read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            items.append(value)
    return items[-limit:]


def find_case(data_dir: Path, case_id: str) -> dict[str, Any] | None:
    for item in reversed(load_cases(data_dir, limit=MAX_CASES)):
        if item.get("case_id") == case_id:
            return item
    return None


def append_case(data_dir: Path, case_item: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_store(data_dir)
    item = dict(case_item)
    item.setdefault("case_id", build_case_id())
    item.setdefault("recorded_at", time.time())
    with STORE_LOCK:
        existing = load_cases(data_dir, limit=MAX_CASES - 1)
        existing.append(item)
        paths["cases"].write_text(
            "\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True) for case in existing) + "\n",
            encoding="utf-8",
        )
        paths["latest"].write_text(json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return item
