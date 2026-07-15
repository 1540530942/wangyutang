"""Golden case helpers — stdlib only, safe to import in CI without torch/fastapi."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_GOLDEN_DIR = Path(__file__).resolve().parent / "tests" / "golden"
_GOLDEN_SESSIONS_DIR = _GOLDEN_DIR / "sessions"


def _load_golden_session_case(session_dir: Path) -> tuple[dict[str, Any], Path | None] | None:
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        turns_rel = str((manifest.get("labels") or {}).get("turns") or "labels/turns.json")
        turns_payload = json.loads((session_dir / turns_rel).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None

    session_id = str(manifest.get("session_id") or session_dir.name)
    case_id = str(manifest.get("case_id") or session_id)
    audio_meta = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    audio_file = str(audio_meta.get("file") or "audio/mic_proc_16k.wav")
    audio_path = session_dir / audio_file
    turns = list(turns_payload.get("turns") or [])
    duration_ms = int(audio_meta.get("duration_ms") or 0)
    return (
        {
            "case_id": case_id,
            "label": manifest.get("label") or manifest.get("mode") or "",
            "mode": manifest.get("mode") or "",
            "description": manifest.get("description") or "",
            "audio_file": f"sessions/{session_dir.name}/{audio_file}",
            "audio_source_session": session_id,
            "session_id": session_id,
            "day": str(manifest.get("created_at") or "")[:10],
            "capture_point": manifest.get("capture_point") or "unknown",
            "source": manifest.get("source") or "golden_session",
            "device_id": manifest.get("device_id") or "",
            "duration_ms": duration_ms,
            "created_at": manifest.get("created_at") or "",
            "utterances": turns,
            "audio_url": f"/audio_interact/api/golden/audio/{case_id}",
            "session_audio_url": f"/audio_interact/api/golden/audio/{session_id}",
        },
        audio_path if audio_path.exists() else None,
    )


def iter_golden_session_cases() -> list[tuple[dict[str, Any], Path | None]]:
    if not _GOLDEN_SESSIONS_DIR.is_dir():
        return []
    cases: list[tuple[dict[str, Any], Path | None]] = []
    for session_dir in sorted(p for p in _GOLDEN_SESSIONS_DIR.iterdir() if p.is_dir()):
        loaded = _load_golden_session_case(session_dir)
        if loaded is not None:
            cases.append(loaded)
    return cases


def legacy_golden_audio_path(case: dict[str, Any]) -> Path | None:
    audio_file = case.get("audio_file")
    if not audio_file:
        return None
    audio_path = _GOLDEN_DIR.parent.parent / str(audio_file)
    return audio_path if audio_path.exists() else None


def list_golden_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case, audio_path in iter_golden_session_cases():
        if audio_path is not None:
            case["audio_url"] = f"/audio_interact/api/golden/audio/{case['case_id']}"
        cases.append(case)
        seen.add(str(case.get("case_id") or ""))
        seen.add(str(case.get("session_id") or ""))

    if _GOLDEN_DIR.is_dir():
        for f in sorted(_GOLDEN_DIR.glob("*.json")):
            try:
                case = json.loads(f.read_text(encoding="utf-8"))
                case_id = str(case.get("case_id") or "")
                if case_id and case_id in seen:
                    continue
                if legacy_golden_audio_path(case) is not None:
                    case["audio_url"] = f"/audio_interact/api/golden/audio/{case['case_id']}"
                elif case.get("audio_source_session"):
                    case["audio_url"] = f"/audio_interact/api/sessions/{case['audio_source_session']}/audio/mic_proc_16k.wav"
                cases.append(case)
            except Exception:
                pass
    return cases


def resolve_golden_audio_path(case_id: str) -> Path | None:
    """Return the WAV path for a golden case or session ID, or None if not found."""
    safe_case_id = Path(case_id).name
    for case, audio_path in iter_golden_session_cases():
        if safe_case_id in {str(case.get("case_id") or ""), str(case.get("session_id") or "")}:
            return audio_path
    for f in _GOLDEN_DIR.glob("*.json"):
        try:
            case = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if case.get("case_id") == safe_case_id:
            return legacy_golden_audio_path(case)
    return None
