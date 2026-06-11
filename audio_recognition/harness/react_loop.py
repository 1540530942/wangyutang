from __future__ import annotations

from pathlib import Path
from typing import Any

from audio_recognition.agent.react_agent import build_llm_react_agent, run_react_agent
from audio_recognition.core.envelope import DecisionEnvelope, ToolCall
from audio_recognition.safety.guard import has_emergency_intent, run_safety_guard, run_safety_guard_for_task
from audio_recognition.skills.registry import resolve_catalog_path, resolve_registry_path
from audio_recognition.tools.dispatcher import dispatch_envelope, dispatch_task
from audio_recognition.tools.observation_executor import OBSERVATION_TOOLS, execute_observation_tool
from audio_recognition.tools.tool_call_adapter import build_tool_result_message
from audio_recognition.tools.tool_validator import validate_tool_call, validate_tool_calls


def route_transcript(
    *,
    base_dir: Path,
    text: str,
    router_config: dict[str, Any] | None,
    cloud_config: dict[str, Any] | None,
    route_action: bool,
    source: str,
    device_id: str = "turbopi-01",
) -> dict[str, Any]:
    envelope = decide_transcript(
        base_dir=base_dir,
        text=text,
        router_config=router_config,
        cloud_config=cloud_config,
        dispatch_mode="cloud_queue" if route_action else "dry_run",
        source=source,
        device_id=device_id,
    )
    first_task = next((task for task in envelope.tasks if task.status != "rejected"), None)
    first_execution = next((item.get("result", {}) for item in envelope.dispatch_results if item.get("status") not in {"rejected"}), {})
    execution = first_execution if isinstance(first_execution, dict) else {}
    plan = None
    if first_task:
        plan = {
            "skill_id": first_task.skill_id,
            "route": first_task.route,
            "planner": "react_llm",
            "confidence": 1.0,
            "transcript": text.strip(),
        }
    return {
        "plan": plan,
        "skill_id": first_task.skill_id if first_task else "",
        "action_task": execution.get("action_task"),
        "face_task": execution.get("face_task"),
        "action_error": execution.get("action_error", ""),
        "face_error": execution.get("face_error", ""),
        "envelope": envelope.model_dump(),
    }


def decide_transcript(
    *,
    base_dir: Path,
    text: str,
    router_config: dict[str, Any] | None,
    cloud_config: dict[str, Any] | None,
    dispatch_mode: str,
    source: str,
    device_id: str = "turbopi-01",
    raw: dict[str, Any] | None = None,
) -> DecisionEnvelope:
    envelope = DecisionEnvelope(device_id=device_id, source=source, transcript=str(text or "").strip(), raw=raw or {})
    envelope.source_chain.append({"node": source, "stage": "received", "ts": envelope.t_created})
    catalog_path = resolve_catalog_path(base_dir, router_config)
    registry_path = resolve_registry_path(base_dir, router_config)
    envelope.raw.setdefault("skill_registry", {"path": str(registry_path), "catalog_fallback": str(catalog_path)})
    if not envelope.transcript:
        envelope.reasoning_summary = "Empty transcript."
        return envelope
    if has_emergency_intent(envelope.transcript):
        envelope.reasoning_summary = "Emergency intent detected before LLM; dispatching emergency_stop."
        call = ToolCall(
            tool="emergency_stop",
            args={"skill_id": "emergency_stop", "order": 0, "wait_until": "accepted", "confidence": 1.0, "text": envelope.transcript},
        )
        envelope.tool_calls.append(call)
        envelope.react_turns.append({"turn": 0, "assistant_tool_call": call.model_dump(), "preflight": True})
        task = validate_tool_call(envelope, call, registry_path=registry_path, catalog_path=catalog_path)
        if task:
            checked_task = run_safety_guard_for_task(envelope, task, registry_path=registry_path, catalog_path=catalog_path)
            if checked_task.status == "rejected":
                task.status = checked_task.status
                task.error = checked_task.error
                result = {"task_id": task.task_id, "skill_id": task.skill_id, "status": "rejected", "error": task.error}
                envelope.dispatch_results.append(result)
            else:
                result = dispatch_task(envelope, task, cloud_config=cloud_config or {}, source=source, dispatch_mode=dispatch_mode)
            envelope.react_turns[-1]["tool_result"] = result
        envelope.final_response = "emergency_stop"
        envelope.t_agent_end = __import__("time").time()
        return envelope
    try:
        agent = build_llm_react_agent(base_dir=base_dir, router_config=router_config)
    except Exception as exc:  # noqa: BLE001 - no rule fallback
        envelope.add_error("react_agent", str(exc), {"mode_required": "llm"})
        envelope.reasoning_summary = "LLM ReAct agent unavailable; no rule fallback is allowed."
        return envelope
    max_steps = int(((router_config or {}).get("react_agent") or {}).get("max_steps") or 8)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": agent._system_prompt()},
        {"role": "user", "content": f"/no_think\n用户原始指令: {envelope.transcript}"},
    ]
    envelope.react_messages = list(messages)
    envelope.t_agent_start = envelope.t_agent_start or __import__("time").time()
    for turn in range(1, max(max_steps, 1) + 1):
        try:
            call = agent.run_turn(envelope, messages, turn)
        except Exception as exc:  # noqa: BLE001
            envelope.add_error("react_agent", str(exc), {"turn": turn})
            break
        decision = dict(getattr(agent, "last_decision", {}) or {})
        envelope.tool_calls.append(call)
        message_for_history = decision.get("message_for_history")
        if isinstance(message_for_history, dict):
            messages.append(message_for_history)
        else:
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {
                                "name": call.tool,
                                "arguments": __import__("json").dumps(call.args, ensure_ascii=False),
                            },
                        }
                    ],
                }
            )
        turn_record = {
            "turn": turn,
            "assistant_tool_call": call.model_dump(),
            "raw_assistant_message": decision.get("raw_assistant_message"),
            "message_for_history": message_for_history,
            "deferred_tool_calls": decision.get("deferred_tool_calls", []),
            "deferred_policy": decision.get("deferred_policy", ""),
            "warnings": decision.get("warnings", []),
        }
        envelope.react_turns.append(turn_record)
        if call.tool == "finish":
            envelope.final_response = str(call.args.get("message") or call.args.get("final") or "done")
            validate_tool_call(envelope, call, registry_path=registry_path, catalog_path=catalog_path)
            break
        task = validate_tool_call(envelope, call, registry_path=registry_path, catalog_path=catalog_path)
        if not task:
            if call.tool in OBSERVATION_TOOLS:
                observation = execute_observation_tool(envelope, call, cloud_config or {})
                tool_result = {"ok": observation.get("status") != "failed", "observation": observation}
            else:
                tool_result = {"ok": False, "error": "tool_call_rejected", "tool": call.tool}
            messages.append(build_tool_result_message(call.call_id, call.tool, tool_result))
            envelope.react_turns[-1]["tool_result"] = tool_result
            continue
        checked_task = run_safety_guard_for_task(envelope, task, registry_path=registry_path, catalog_path=catalog_path)
        if checked_task.status == "rejected":
            task.status = checked_task.status
            task.error = checked_task.error
            result = {"task_id": task.task_id, "skill_id": task.skill_id, "status": "rejected", "error": task.error}
            envelope.dispatch_results.append(result)
            messages.append(build_tool_result_message(call.call_id, call.tool, result))
            envelope.react_turns[-1]["tool_result"] = result
            continue
        result = dispatch_task(envelope, task, cloud_config=cloud_config or {}, source=source, dispatch_mode=dispatch_mode)
        messages.append(build_tool_result_message(call.call_id, call.tool, result))
        envelope.react_turns[-1]["tool_result"] = result
        envelope.react_messages = list(messages)
        if result.get("status") not in {"completed", "dry_run"} or task.skill_id == "emergency_stop":
            break
    else:
        envelope.add_error("react_agent", "max_steps_exceeded", {"max_steps": max_steps})
    envelope.react_messages = list(messages)
    envelope.t_agent_end = __import__("time").time()
    return envelope


def transcribe_audio_path(
    *,
    base_dir: Path,
    wav_path: str | Path,
    provider: Any,
    router_config: dict[str, Any] | None,
) -> dict[str, Any]:
    transcript = provider.transcribe(wav_path)
    text = str(transcript.get("text") or "").strip()
    return {
        "text": text,
        "raw": transcript.get("raw", {}),
        "error": str(transcript.get("error") or ""),
        "plan": None,
        "skill_id": "",
    }
