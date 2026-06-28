from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from audio_recognition.agent.react_agent import build_llm_react_agent, run_react_agent
from audio_recognition.core.envelope import DecisionEnvelope, ToolCall
from audio_recognition.safety.guard import has_emergency_intent, run_safety_guard, run_safety_guard_for_task
from audio_recognition.skills.registry import load_skill_registry, resolve_catalog_path, resolve_registry_path
from audio_recognition.tools.dispatcher import dispatch_envelope, dispatch_task
from audio_recognition.tools.observation_executor import OBSERVATION_TOOLS, execute_observation_tool
from audio_recognition.tools.tool_call_adapter import build_tool_result_message
from audio_recognition.tools.tool_validator import validate_tool_call, validate_tool_calls


_TTS_SKIP = {"completed", "done", "dry_run", "emergency_stop", "rejected", ""}

_SKILL_TTS: dict[str, str] = {
    "move_forward": "好的，往前走",
    "move_backward": "好的，往后退",
    "move_left": "好的，向左移",
    "move_right": "好的，向右移",
    "turn_left": "好的，向左转",
    "turn_right": "好的，向右转",
    "look_left": "好的，向左看",
    "look_right": "好的，向右看",
    "look_up": "好的，抬头看",
    "look_down": "好的，低头看",
    "reset_pose": "好的，回到初始姿态",
    "rgb_on": "好的，打开灯光",
    "rgb_off": "好的，关闭灯光",
    "emergency_stop": "好的，紧急停止",
    "camera_snapshot": "好的，已拍照",
    "front_distance": "好的，已测距",
    "inspect_scene": "好的，已查看场景",
}

_REJECT_TTS: dict[str, str] = {
    "front_distance_too_close": "前方太近，无法前进",
    "front_distance_unavailable": "距离传感器不可用",
    "front_distance_stale": "传感器数据过旧，我先停下",
    "front_distance_low_confidence": "传感器置信度低，我先停下",
    "front_distance_observation_missing": "缺少前方距离数据",
    "recent_front_distance_required": "需要先测距才能执行",
    "recent_camera_snapshot_required": "需要先拍照才能执行",
    "negative_instruction_detected": "好的，我不会这样做",
    "low_confidence_confirmation_required": "我不太确定指令，先暂停",
    "too_many_movement_tasks": "动作太多了，只能执行三个",
    "total_duration_exceeded": "总时长超限，已调整",
    "unsupported_action_skill": "抱歉，我不认识这个动作",
    "unsupported_face_skill": "抱歉，我不支持这个表情",
}


def _build_tts_text(envelope: DecisionEnvelope) -> str:
    if envelope.dispatch_mode == "dry_run":
        return ""

    # LLM path: finish.message is natural language from the LLM
    if envelope.final_response and envelope.final_response not in _TTS_SKIP:
        return envelope.final_response

    # emergency_stop is in _TTS_SKIP so check tasks directly
    completed_task = next((t for t in envelope.tasks if t.status in {"completed", "accepted"}), None)
    rejected_task = next((t for t in envelope.tasks if t.status == "rejected"), None)

    if completed_task:
        return _SKILL_TTS.get(completed_task.skill_id, "好的，已执行")

    if rejected_task:
        return _REJECT_TTS.get(rejected_task.error, "抱歉，无法执行这个动作")

    # Observation-only result (inspect_scene, front_distance, etc.)
    if envelope.observations:
        obs = next(
            (o for o in reversed(envelope.observations) if o.get("status") == "completed"),
            envelope.observations[-1],
        )
        tool = str(obs.get("tool") or "")
        data = obs.get("data") or {}
        if tool == "inspect_scene":
            answer = str(data.get("answer") or "")
            if answer:
                return answer[:100]
        if tool == "front_distance":
            sonar = data.get("sonar") if isinstance(data.get("sonar"), dict) else data
            dist = sonar.get("front_distance_estimate_cm")
            if dist is not None:
                try:
                    return f"前方距离约{int(float(dist))}厘米"
                except (TypeError, ValueError):
                    pass
        return _SKILL_TTS.get(tool, "好的，观测完成")

    if not envelope.tasks and not envelope.observations:
        return "抱歉，我没有理解这条指令"

    return ""


EXACT_ACTION_ALIASES = {
    "forward": "move_forward",
    "\u524d\u8fdb": "move_forward",
    "\u5411\u524d": "move_forward",
    "\u5411\u524d\u8d70": "move_forward",
    "\u5f80\u524d\u8d70": "move_forward",
    "\u5411\u524d\u79fb\u52a8": "move_forward",
    "backward": "move_backward",
    "\u540e\u9000": "move_backward",
    "\u5411\u540e": "move_backward",
    "\u5411\u540e\u8d70": "move_backward",
    "\u5f80\u540e\u8d70": "move_backward",
    "\u5de6\u8f6c": "turn_left",
    "\u5411\u5de6\u8f6c": "turn_left",
    "\u5f80\u5de6\u8f6c": "turn_left",
    "\u671d\u5de6\u8f6c": "turn_left",
    "\u5411\u5de6\u65cb\u8f6c": "turn_left",
    "\u6389\u5934": "turn_left",
    "\u539f\u5730\u6389\u5934": "turn_left",
    "\u53f3\u8f6c": "turn_right",
    "\u5411\u53f3\u8f6c": "turn_right",
    "\u5f80\u53f3\u8f6c": "turn_right",
    "\u671d\u53f3\u8f6c": "turn_right",
    "\u5411\u53f3\u65cb\u8f6c": "turn_right",
    "\u5de6\u79fb": "move_left",
    "\u5411\u5de6\u79fb": "move_left",
    "\u5f80\u5de6\u8d70": "move_left",
    "\u53f3\u79fb": "move_right",
    "\u5411\u53f3\u79fb": "move_right",
    "\u5f80\u53f3\u8d70": "move_right",
}
EXACT_ACTION_SPLIT_RE = re.compile(r"(?:\s+|[\uff0c,;\uff1b\u3001]+|\u7136\u540e|\u518d|\u63a5\u7740|\u5e76\u4e14|\u540e)+")


def _normalized_exact_text(text: str) -> str:
    return re.sub(r"[\s\u3002\uff01!\uff1f?]+", "", text.strip().casefold())


def _exact_action_sequence(transcript: str) -> list[tuple[str, str]] | None:
    normalized = _normalized_exact_text(transcript)
    if not normalized:
        return None
    if normalized in EXACT_ACTION_ALIASES:
        return [(EXACT_ACTION_ALIASES[normalized], transcript.strip())]
    parts = [item for item in EXACT_ACTION_SPLIT_RE.split(transcript.strip()) if item.strip()]
    if len(parts) <= 1:
        return None
    sequence: list[tuple[str, str]] = []
    for part in parts:
        key = _normalized_exact_text(part)
        skill_id = EXACT_ACTION_ALIASES.get(key)
        if not skill_id:
            return None
        sequence.append((skill_id, part.strip()))
    return sequence or None


def _append_rejected_result(envelope: DecisionEnvelope, task: Any) -> dict[str, Any]:
    result = {"task_id": task.task_id, "skill_id": task.skill_id, "status": "rejected", "error": task.error}
    envelope.dispatch_results.append(result)
    return result


def _run_exact_action_sequence(
    envelope: DecisionEnvelope,
    sequence: list[tuple[str, str]],
    *,
    registry_path: Path,
    catalog_path: Path,
    cloud_config: dict[str, Any] | None,
    source: str,
    dispatch_mode: str,
) -> DecisionEnvelope:
    envelope.reasoning_summary = "Exact action alias matched before LLM."
    for index, (skill_id, fragment) in enumerate(sequence, start=1):
        if skill_id == "move_forward":
            observation_call = ToolCall(
                tool="front_distance",
                args={"skill_id": "front_distance", "order": index - 0.5, "confidence": 1.0, "text": fragment},
            )
            envelope.tool_calls.append(observation_call)
            envelope.react_turns.append({"turn": len(envelope.react_turns), "assistant_tool_call": observation_call.model_dump(), "preflight": True})
            observation = execute_observation_tool(envelope, observation_call, cloud_config or {})
            observation["preflight"] = True
            envelope.react_turns[-1]["tool_result"] = {"ok": observation.get("status") != "failed", "observation": observation}

        call = ToolCall(
            tool="dispatch_action",
            args={"skill_id": skill_id, "order": index, "wait_until": "completed", "confidence": 1.0, "text": fragment},
        )
        envelope.tool_calls.append(call)
        envelope.react_turns.append({"turn": len(envelope.react_turns), "assistant_tool_call": call.model_dump(), "preflight": True})
        task = validate_tool_call(envelope, call, registry_path=registry_path, catalog_path=catalog_path)
        if not task:
            result = {"ok": False, "error": "tool_call_rejected", "tool": call.tool}
            envelope.react_turns[-1]["tool_result"] = result
            continue
        checked_task = run_safety_guard_for_task(envelope, task, registry_path=registry_path, catalog_path=catalog_path)
        if checked_task.status == "rejected":
            task.status = checked_task.status
            task.error = checked_task.error
            result = _append_rejected_result(envelope, task)
        else:
            result = dispatch_task(envelope, task, cloud_config=cloud_config or {}, source=source, dispatch_mode=dispatch_mode)
        envelope.react_turns[-1]["tool_result"] = result
        if result.get("status") not in {"completed", "dry_run"}:
            break
    envelope.final_response = str(envelope.dispatch_results[-1].get("status") if envelope.dispatch_results else "done")
    envelope.t_agent_end = __import__("time").time()
    return envelope


def _exact_observation_alias_call(transcript: str, *, registry_path: Path, catalog_path: Path) -> ToolCall | None:
    normalized = transcript.strip().casefold()
    if not normalized:
        return None
    registry = load_skill_registry(registry_path, catalog_path)
    for spec in registry.skills.values():
        if spec.tool not in OBSERVATION_TOOLS:
            continue
        aliases = {spec.skill_id, spec.tool, *spec.aliases}
        if normalized in {alias.strip().casefold() for alias in aliases if alias.strip()}:
            return ToolCall(
                tool=spec.tool,
                args={"skill_id": spec.skill_id, "order": 0, "confidence": 1.0, "text": transcript.strip()},
            )
    return None


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
    first_task = next((task for task in envelope.tasks if task.status != "rejected"), None) or (envelope.tasks[0] if envelope.tasks else None)
    first_observation = envelope.observations[0] if envelope.observations else None
    first_dispatch = next((item for item in envelope.dispatch_results if item.get("status") not in {"rejected"}), None) or (
        envelope.dispatch_results[0] if envelope.dispatch_results else {}
    )
    first_execution = first_dispatch.get("result", {}) if isinstance(first_dispatch, dict) else {}
    execution = first_execution if isinstance(first_execution, dict) else {}
    action_task = execution.get("action_task")
    if action_task == {}:
        action_task = None
    action_error = str(execution.get("action_error") or first_dispatch.get("error") or (first_task.error if first_task and first_task.route == "action" else "") or "")
    face_error = str(execution.get("face_error") or (first_task.error if first_task and first_task.route == "face" else "") or "")
    plan = None
    if first_task:
        planner = "exact_action_alias" if envelope.react_turns and envelope.react_turns[0].get("preflight") else "react_llm"
        plan = {
            "skill_id": first_task.skill_id,
            "route": first_task.route,
            "planner": planner,
            "confidence": 1.0,
            "transcript": text.strip(),
        }
    elif first_observation:
        plan = {
            "skill_id": str(first_observation.get("tool") or ""),
            "route": "observation",
            "planner": "exact_observation_alias" if first_observation.get("preflight") else "react_llm",
            "confidence": 1.0,
            "transcript": text.strip(),
        }
    return {
        "plan": plan,
        "skill_id": first_task.skill_id if first_task else str(first_observation.get("tool") or "") if first_observation else "",
        "action_task": action_task,
        "face_task": execution.get("face_task"),
        "observation": first_observation,
        "action_error": action_error,
        "face_error": face_error,
        "tts_text": _build_tts_text(envelope),
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
    envelope = DecisionEnvelope(device_id=device_id, source=source, transcript=str(text or "").strip(), raw=raw or {}, dispatch_mode=dispatch_mode)
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
    exact_actions = _exact_action_sequence(envelope.transcript)
    if exact_actions:
        return _run_exact_action_sequence(
            envelope,
            exact_actions,
            registry_path=registry_path,
            catalog_path=catalog_path,
            cloud_config=cloud_config,
            source=source,
            dispatch_mode=dispatch_mode,
        )
    try:
        preflight_observation = _exact_observation_alias_call(envelope.transcript, registry_path=registry_path, catalog_path=catalog_path)
    except Exception as exc:  # noqa: BLE001 - exact alias is an optimization, not a hard dependency
        envelope.add_error("observation_alias", str(exc))
        preflight_observation = None
    if preflight_observation:
        envelope.reasoning_summary = "Exact observation alias matched before LLM."
        envelope.tool_calls.append(preflight_observation)
        envelope.react_turns.append({"turn": 0, "assistant_tool_call": preflight_observation.model_dump(), "preflight": True})
        observation = execute_observation_tool(envelope, preflight_observation, cloud_config or {})
        observation["preflight"] = True
        tool_result = {"ok": observation.get("status") != "failed", "observation": observation}
        envelope.react_turns[-1]["tool_result"] = tool_result
        envelope.final_response = str(observation.get("status") or "completed")
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
