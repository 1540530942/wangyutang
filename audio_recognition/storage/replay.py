from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from audio_recognition.agent.react_agent import run_react_agent
from audio_recognition.core.envelope import DecisionEnvelope
from audio_recognition.safety.guard import run_safety_guard
from audio_recognition.storage.envelope_store import load_envelope
from audio_recognition.tools.dispatcher import dispatch_envelope
from audio_recognition.tools.tool_validator import validate_tool_calls


ReplayFrom = Literal["text", "tool_calls", "tasks"]


def diff_envelopes(old: DecisionEnvelope, new: DecisionEnvelope) -> dict[str, bool]:
    return {
        "transcript_changed": old.transcript != new.transcript,
        "tool_calls_changed": [call.model_dump() for call in old.tool_calls] != [call.model_dump() for call in new.tool_calls],
        "tasks_changed": [task.model_dump() for task in old.tasks] != [task.model_dump() for task in new.tasks],
        "safety_changed": old.safety_result != new.safety_result,
    }


def replay_envelope(
    *,
    data_dir: Path,
    envelope_id: str,
    base_dir: Path,
    router_config: dict[str, Any] | None,
    replay_from: ReplayFrom = "text",
) -> dict[str, Any]:
    old = load_envelope(data_dir, envelope_id)
    if not old:
        raise KeyError(envelope_id)
    new = DecisionEnvelope(device_id=old.device_id, source="replay", transcript=old.transcript, asr_meta=old.asr_meta, raw={"replay_from": replay_from, "old_envelope_id": envelope_id})
    if replay_from == "text":
        new = run_react_agent(envelope=new, base_dir=base_dir, router_config=router_config)
        new = validate_tool_calls(new)
        new = run_safety_guard(new)
    elif replay_from == "tool_calls":
        new.tool_calls = old.tool_calls
        new = validate_tool_calls(new)
        new = run_safety_guard(new)
    elif replay_from == "tasks":
        new.tool_calls = old.tool_calls
        new.validated_tool_calls = old.validated_tool_calls
        new.tasks = old.tasks
        new = run_safety_guard(new)
    new = dispatch_envelope(new, cloud_config={}, source="replay", dispatch_mode="dry_run")
    return {"old_envelope_id": old.envelope_id, "new_envelope": new.model_dump(), "diff": diff_envelopes(old, new)}
