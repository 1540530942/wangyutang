from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from audio_recognition.core.envelope import DecisionEnvelope


def envelope_paths(data_dir: Path) -> dict[str, Path]:
    root = data_dir / "envelopes"
    root.mkdir(parents=True, exist_ok=True)
    return {"root": root, "latest": root / "latest_envelope.json", "index": root / "index.jsonl"}


def save_envelope(data_dir: Path, envelope: DecisionEnvelope) -> DecisionEnvelope:
    paths = envelope_paths(data_dir)
    payload = envelope.model_dump()
    target = paths["root"] / f"{envelope.envelope_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["latest"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with paths["index"].open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"envelope_id": envelope.envelope_id, "source": envelope.source, "device_id": envelope.device_id, "transcript": envelope.transcript, "t_created": envelope.t_created}, ensure_ascii=False) + "\n")
    return envelope


def load_envelope(data_dir: Path, envelope_id: str) -> DecisionEnvelope | None:
    target = envelope_paths(data_dir)["root"] / f"{Path(envelope_id).name}.json"
    if not target.exists():
        return None
    return DecisionEnvelope(**json.loads(target.read_text(encoding="utf-8-sig")))


def list_envelopes(data_dir: Path, limit: int = 50) -> list[dict[str, Any]]:
    index = envelope_paths(data_dir)["index"]
    if not index.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in index.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-limit:]
