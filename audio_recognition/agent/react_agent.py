from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from audio_recognition.core.envelope import DecisionEnvelope, ToolCall, build_call_id
from audio_recognition.skills.registry import resolve_catalog_path, resolve_registry_path
from audio_recognition.tools.tool_call_adapter import normalize_legacy_json_to_react_turn, normalize_tool_calls_to_react_turn
from audio_recognition.tools.tool_schema import build_react_tools_schema, tool_skill_groups


DEFAULT_LLM_ENDPOINT = "https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat/completions"
DEFAULT_LLM_MODEL = "qwen3-32b"


class LlmReactAgent:
    def __init__(self, *, base_dir: Path, router_config: dict[str, Any] | None):
        self.base_dir = base_dir
        self.router_config = router_config or {}
        self.catalog_path = resolve_catalog_path(base_dir, self.router_config)
        self.registry_path = resolve_registry_path(base_dir, self.router_config)
        agent_config = dict((router_config or {}).get("react_agent") or {})
        llm_config = dict(agent_config.get("llm") or {})
        self.endpoint = str(llm_config.get("endpoint") or os.getenv("AUDIO_REACT_LLM_ENDPOINT") or DEFAULT_LLM_ENDPOINT).strip()
        self.model = str(llm_config.get("model") or os.getenv("AUDIO_REACT_LLM_MODEL") or DEFAULT_LLM_MODEL).strip()
        self.timeout = float(llm_config.get("timeout_seconds") or os.getenv("AUDIO_REACT_LLM_TIMEOUT_SECONDS") or 90)
        self.verify_ssl = bool(llm_config.get("verify_ssl", True))
        self.retries = int(llm_config.get("retries") or os.getenv("AUDIO_REACT_LLM_RETRIES") or 2)
        self.use_response_format = bool(llm_config.get("response_format_json") or os.getenv("AUDIO_REACT_LLM_RESPONSE_FORMAT_JSON"))
        self.prompt_path = self._resolve_prompt_path(agent_config)
        self.last_decision: dict[str, Any] = {}
        if not self.endpoint or not self.model:
            raise RuntimeError("react_agent.llm.endpoint and model are required")

    def _resolve_prompt_path(self, agent_config: dict[str, Any]) -> Path:
        raw_path = agent_config.get("prompt_path") or os.getenv("AUDIO_REACT_SYSTEM_PROMPT") or "agent/prompts/walle_system_prompt.md"
        prompt_path = Path(str(raw_path))
        if not prompt_path.is_absolute():
            prompt_path = (self.base_dir / prompt_path).resolve()
        return prompt_path

    def _persona_prompt(self) -> str:
        if self.prompt_path.exists():
            text = self.prompt_path.read_text(encoding="utf-8").strip()
            if text:
                return text
        return (
            "You are WALL-E, the robot control brain. Use native tool_calls when available. "
            "One ReAct turn equals one tool_call. Observe before acting when current state is required. "
            "Execute only positive requested actions; negated fragments do not create actions."
        )

    def _system_prompt(self) -> str:
        groups = tool_skill_groups(self.registry_path, self.catalog_path)
        allowed = {
            "dispatch_action": groups["action"],
            "dispatch_face": groups["face"],
            "camera_snapshot": ["camera_snapshot"],
            "front_distance": ["front_distance"],
            "get_robot_state": ["get_robot_state"],
            "ask_confirmation": ["ask_confirmation"],
            "emergency_stop": ["emergency_stop"],
            "finish": ["finish"],
        }
        schema = {
            "protocol_version": "react_v1_single_tool",
            "reasoning_summary": "short Chinese summary, no hidden chain-of-thought",
            "tool_call": {
                "tool": "dispatch_action | dispatch_face | emergency_stop | finish",
                "args": {
                    "skill_id": "one allowed skill id, omitted for finish",
                    "duration_ms": 800,
                    "wait_until": "completed",
                    "confidence": 0.9,
                    "text": "exact minimal source fragment for this one step",
                },
            },
            "final": "only for finish",
        }
        return (
            f"{self._persona_prompt()}\n\n"
            "Runtime protocol:\n"
            "- Prefer native OpenAI-style tool_calls. If native tool calling is unavailable, output only compact JSON.\n"
            "- protocol_version=react_v1_single_tool. No Thinking Process. One ReAct turn equals one tool_call.\n"
            "- After a completed/dry_run tool result, choose the next unfinished positive command; output finish when done.\n"
            "- Negated fragments such as \u4e0d\u8981/\u522b/\u4e0d\u8bb8/\u4e0d\u7528 do not create that action and do not cancel previous completed actions.\n"
            "- \u505c\u6b62/\u6025\u505c/\u522b\u52a8/\u4e0d\u8981\u52a8 -> emergency_stop.\n"
            "- \u5f80\u524d\u8d70=move_forward; \u5f80\u540e\u8d70=move_backward; \u62ac\u5934\u770b=look_up; \u4f4e\u5934\u770b=look_down.\n"
            "- tool_call.args.text must be the minimal source fragment for only this step.\n"
            "- Use front_distance before forward motion when front clearance matters; use camera_snapshot when visual scene evidence is required.\n"
            f"Allowed: {json.dumps(allowed, ensure_ascii=False)}.\n"
            f"Schema: {json.dumps(schema, ensure_ascii=False)}."
        )

    def _tools_schema(self) -> list[dict[str, Any]]:
        return build_react_tools_schema(self.registry_path, self.catalog_path)

    def _parse_response(self, text: str) -> dict[str, Any]:
        stripped = text.strip()
        if "</think>" in stripped:
            stripped = stripped.split("</think>", 1)[1].strip()
        if stripped.startswith("```"):
            stripped = "\n".join(line for line in stripped.splitlines() if not line.startswith("```")).strip()
        start = stripped.find("{")
        if start >= 0:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(stripped[start:])
        else:
            data = json.loads(stripped)
        if not isinstance(data, dict):
            raise ValueError("LLM response is not a JSON object")
        return data

    def _post(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 1200,
            "tools": self._tools_schema(),
            "tool_choice": "auto",
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if self.use_response_format:
            payload["response_format"] = {"type": "json_object"}
        response = None
        last_error: Exception | None = None
        for attempt in range(max(self.retries, 1)):
            try:
                response = requests.post(self.endpoint, json=payload, timeout=self.timeout, verify=self.verify_ssl)
                response.raise_for_status()
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 >= max(self.retries, 1):
                    raise
                time.sleep(0.5 * (attempt + 1))
        if response is None:
            raise RuntimeError(str(last_error or "empty llm response"))
        raw = response.json()
        choices = (raw.get("raw") or {}).get("choices") if isinstance(raw.get("raw"), dict) else raw.get("choices")
        choice_message: dict[str, Any] | None = None
        if choices:
            message = ((choices[0] or {}).get("message") or {})
            choice_message = message if isinstance(message, dict) else None
            if choice_message and choice_message.get("tool_calls"):
                data = normalize_tool_calls_to_react_turn(choice_message)
                data["_raw"] = raw
                return data
        text = str(raw.get("text") or "")
        if not text:
            if choices:
                text = str((choice_message or {}).get("content") or "")
                if not text and choice_message:
                    data = normalize_tool_calls_to_react_turn(choice_message)
                    data["_raw"] = raw
                    return data
        try:
            data = self._parse_response(text)
        except Exception:
            if choice_message is not None:
                data = normalize_tool_calls_to_react_turn(choice_message)
                data["_raw"] = raw
                return data
            raise
        data = normalize_legacy_json_to_react_turn(data)
        data["_raw"] = raw
        return data

    def run_turn(self, envelope: DecisionEnvelope, messages: list[dict[str, Any]], turn: int) -> ToolCall:
        data = self._post(messages)
        self.last_decision = data
        envelope.raw.setdefault("react_llm_turns", []).append(
            {
                "turn": turn,
                "model": self.model,
                "raw": data.get("_raw", {}),
                "normalized": {key: value for key, value in data.items() if key != "_raw"},
            }
        )
        if data.get("type") == "error":
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            raise ValueError(str(error.get("message") or "LLM output is invalid"))
        if data.get("type") == "finish":
            message = str(data.get("message") or data.get("final") or "done")
            return ToolCall(tool="finish", args={"message": message, "final": message, "order": turn, "wait_until": "completed", "confidence": 1.0})
        item = data.get("tool_call")
        if data.get("type") == "tool_call" and isinstance(item, dict):
            item = {"tool": item.get("tool") or item.get("name"), "args": item.get("args") or item.get("arguments") or {}}
        if not isinstance(item, dict):
            raise ValueError("LLM response missing tool_call")
        args = dict(item.get("args") or {})
        args.setdefault("order", turn)
        args.setdefault("wait_until", "completed")
        args.setdefault("confidence", 0.9)
        call = ToolCall(call_id=str(data.get("raw_tool_call_id") or data.get("call_id") or build_call_id()), tool=str(item.get("tool") or ""), args=args)
        envelope.reasoning_summary = str(data.get("reasoning_summary") or envelope.reasoning_summary)
        return call

    def run(self, envelope: DecisionEnvelope) -> DecisionEnvelope:
        envelope.t_agent_start = time.time()
        try:
            call = self.run_turn(
                envelope,
                [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": f"/no_think\n\u7528\u6237\u539f\u59cb\u6307\u4ee4: {envelope.transcript}"},
                ],
                1,
            )
            envelope.tool_calls = [call]
            envelope.agent_steps = [{"planner": "llm", "model": self.model, "tool_call_count": 1}]
        except Exception as exc:  # noqa: BLE001 - no rule fallback in llm mode
            envelope.tool_calls = []
            envelope.reasoning_summary = "LLM ReAct agent failed; no tool calls generated."
            envelope.add_error("react_agent", str(exc), {"mode": "llm", "model": self.model})
        envelope.t_agent_end = time.time()
        return envelope


def build_llm_react_agent(*, base_dir: Path, router_config: dict[str, Any] | None) -> LlmReactAgent:
    router_config = router_config or {}
    agent_config = dict(router_config.get("react_agent") or {})
    mode = str(agent_config.get("mode") or os.getenv("AUDIO_REACT_AGENT_MODE") or "").strip().lower()
    if mode != "llm":
        raise RuntimeError("real ReAct execution requires react_agent.mode=llm; rule and hybrid are disabled")
    return LlmReactAgent(base_dir=base_dir, router_config=router_config)


def run_react_agent(*, envelope: DecisionEnvelope, base_dir: Path, router_config: dict[str, Any] | None) -> DecisionEnvelope:
    try:
        return build_llm_react_agent(base_dir=base_dir, router_config=router_config).run(envelope)
    except Exception as exc:  # noqa: BLE001 - no rule fallback
        envelope.tool_calls = []
        envelope.reasoning_summary = "LLM ReAct agent failed; no rule fallback is allowed."
        envelope.add_error("react_agent", str(exc), {"mode_required": "llm"})
        return envelope
