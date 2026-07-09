from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.asr_eval import evaluate_asr
from eval.bargein_eval import evaluate_bargein
from eval.tts_eval import evaluate_tts
from eval.vad_eval import evaluate_vad
from runtime.event_logger import read_jsonl


def generate_report(session_dir: Path, *, output_path: Path | None = None) -> dict[str, Any]:
    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    vad_events = read_jsonl(session_dir / "events" / "vad_runtime.jsonl")
    asr_events = read_jsonl(session_dir / "events" / "asr_runtime.jsonl")
    tts_events = read_jsonl(session_dir / "events" / "tts_runtime.jsonl")
    bargein_events = read_jsonl(session_dir / "events" / "bargein_runtime.jsonl")
    labels = {
        "vad": _load_label(session_dir, "vad_label.json"),
        "asr": _load_label(session_dir, "asr_label.json"),
        "tts": _load_label(session_dir, "tts_label.json"),
        "bargein": _load_label(session_dir, "bargein_label.json"),
    }
    report = {
        "session_id": manifest.get("session_id"),
        "versions": manifest.get("versions", {}),
        "vad_eval": evaluate_vad(vad_events, labels["vad"]),
        "asr_eval": evaluate_asr(asr_events, labels["asr"]),
        "tts_eval": evaluate_tts(tts_events, labels["tts"], asr_events),
        "bargein_eval": evaluate_bargein(bargein_events, labels["bargein"]),
    }
    target = output_path or (session_dir / "reports" / "eval_result.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _load_label(session_dir: Path, filename: str) -> dict[str, Any]:
    path = session_dir / "labels" / filename
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

