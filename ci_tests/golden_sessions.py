"""Shared loader and helpers for session-shaped golden audio data."""
from __future__ import annotations

import json
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_SESSIONS_DIR = REPO_ROOT / "audio_interact" / "tests" / "golden" / "sessions"


@dataclass(frozen=True)
class GoldenSession:
    root: Path
    manifest: dict[str, Any]
    turns: list[dict[str, Any]]

    @property
    def session_id(self) -> str:
        return str(self.manifest["session_id"])

    @property
    def audio_path(self) -> Path:
        return self.root / str(self.manifest["audio"]["file"])

    @property
    def sample_rate(self) -> int:
        return int(self.manifest["audio"].get("sample_rate") or 16000)

    def guard_turns(self, guard: str) -> list[dict[str, Any]]:
        return [turn for turn in self.turns if guard in set(turn.get("guards") or [])]


def load_golden_session(session_dir: Path) -> GoldenSession:
    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    turns_rel = manifest.get("labels", {}).get("turns", "labels/turns.json")
    turns_payload = json.loads((session_dir / str(turns_rel)).read_text(encoding="utf-8"))
    return GoldenSession(root=session_dir, manifest=manifest, turns=list(turns_payload["turns"]))


def iter_golden_sessions(*, tier: str | None = "smoke") -> list[GoldenSession]:
    sessions: list[GoldenSession] = []
    for session_dir in sorted(GOLDEN_SESSIONS_DIR.glob("*")):
        if not (session_dir / "manifest.json").exists():
            continue
        session = load_golden_session(session_dir)
        tiers = set(session.manifest.get("tiers") or [])
        if tier is None or tier in tiers:
            sessions.append(session)
    return sessions


def strip_text(text: str) -> str:
    return re.sub(r"[，。？！、,.?!\s]", "", text or "")


def read_pcm16_wav(wav_path: Path, *, sample_rate: int) -> bytes:
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1, "golden audio must be mono"
        assert wf.getsampwidth() == 2, "golden audio must be PCM16"
        assert wf.getframerate() == sample_rate, f"sample rate must be {sample_rate}"
        return wf.readframes(wf.getnframes())
