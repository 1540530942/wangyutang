from __future__ import annotations

import json
from typing import Any

from audio_recognition.core.envelope import build_call_id


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _parse_arguments(raw: Any) -> tuple[dict[str, Any], str]:
    if raw is None or raw == "":
        return {}, ""
    if isinstance(raw, dict):
        return dict(raw), ""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {}, f"invalid_function_arguments_json: {exc.msg}"
        if not isinstance(parsed, dict):
            return {}, "function_arguments_must_be_object"
        return dict(parsed), ""
    return {}, "function_arguments_must_be_object_or_json_string"


def _extract_call(item: dict[str, Any]) -> tuple[str, dict[str, Any], str, str]:
    function = item.get("function") if isinstance(item.get("function"), dict) else None
    if function is not None:
        name = str(function.get("name") or "")
        args, error = _parse_arguments(function.get("arguments"))
        call_id = str(item.get("id") or build_call_id())
        return name, args, call_id, error

    name = str(item.get("tool") or item.get("name") or "")
    args, error = _parse_arguments(item.get("args") if "args" in item else item.get("arguments"))
    call_id = str(item.get("call_id") or item.get("id") or build_call_id())
    return name, args, call_id, error


def _assistant_tool_message(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": _json_dumps(args),
                },
            }
        ],
    }


def normalize_tool_calls_to_react_turn(assistant_msg: dict[str, Any]) -> dict[str, Any]:
    raw_msg = dict(assistant_msg or {})
    tool_calls = raw_msg.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        return {
            "type": "error",
            "error": {"code": "SCHEMA_VALIDATION_FAILED", "message": "assistant.tool_calls must be a list"},
            "raw_assistant_message": raw_msg,
            "message_for_history": {"role": "assistant", "content": raw_msg.get("content") or ""},
            "deferred_tool_calls": [],
            "warnings": [],
        }

    if not tool_calls:
        content = raw_msg.get("content")
        final = str(content if content not in {None, ""} else "done")
        return {
            "type": "finish",
            "final": final,
            "raw_assistant_message": raw_msg,
            "message_for_history": {"role": "assistant", "content": final},
            "deferred_tool_calls": [],
            "warnings": [],
        }

    first = tool_calls[0]
    if not isinstance(first, dict):
        return {
            "type": "error",
            "error": {"code": "SCHEMA_VALIDATION_FAILED", "message": "tool_call must be an object"},
            "raw_assistant_message": raw_msg,
            "message_for_history": {"role": "assistant", "content": raw_msg.get("content") or ""},
            "deferred_tool_calls": tool_calls[1:],
            "warnings": [],
        }

    name, args, call_id, error = _extract_call(first)
    warnings: list[str] = []
    if len(tool_calls) > 1:
        warnings.append("multiple_tool_calls_collapsed_to_first")
    if error:
        return {
            "type": "error",
            "error": {"code": "SCHEMA_VALIDATION_FAILED", "message": error},
            "raw_assistant_message": raw_msg,
            "message_for_history": _assistant_tool_message(call_id, name, {}),
            "deferred_tool_calls": tool_calls[1:],
            "warnings": warnings,
            "raw_tool_call_id": call_id,
        }
    if not name:
        return {
            "type": "error",
            "error": {"code": "SCHEMA_VALIDATION_FAILED", "message": "tool_call function name is required"},
            "raw_assistant_message": raw_msg,
            "message_for_history": _assistant_tool_message(call_id, name, args),
            "deferred_tool_calls": tool_calls[1:],
            "warnings": warnings,
            "raw_tool_call_id": call_id,
        }
    return {
        "type": "tool_call",
        "tool_call": {"name": name, "arguments": args},
        "raw_tool_call_id": call_id,
        "raw_assistant_message": raw_msg,
        "message_for_history": _assistant_tool_message(call_id, name, args),
        "deferred_tool_calls": tool_calls[1:],
        "deferred_policy": "collapse_to_first",
        "warnings": warnings,
    }


def normalize_legacy_json_to_react_turn(data: dict[str, Any]) -> dict[str, Any]:
    raw_data = dict(data or {})
    if raw_data.get("type") == "finish":
        final = str(raw_data.get("message") or raw_data.get("final") or "done")
        return {
            "type": "finish",
            "message": final,
            "final": final,
            "raw_assistant_message": {"role": "assistant", "content": _json_dumps(raw_data)},
            "message_for_history": {"role": "assistant", "content": _json_dumps(raw_data)},
            "deferred_tool_calls": [],
            "warnings": ["legacy_content_json"],
        }

    item = raw_data.get("tool_call")
    if raw_data.get("type") == "tool_call" and isinstance(item, dict):
        item = {"tool": item.get("tool") or item.get("name"), "args": item.get("args") or item.get("arguments") or {}}
    if item is None and isinstance(raw_data.get("tool_calls"), list):
        calls = raw_data.get("tool_calls") or []
        if not calls:
            item = None
        else:
            item = calls[0]
    if not isinstance(item, dict):
        return {
            "type": "error",
            "error": {"code": "SCHEMA_VALIDATION_FAILED", "message": "LLM response missing tool_call"},
            "raw_assistant_message": {"role": "assistant", "content": _json_dumps(raw_data)},
            "message_for_history": {"role": "assistant", "content": _json_dumps(raw_data)},
            "deferred_tool_calls": [],
            "warnings": ["legacy_content_json"],
        }

    calls = raw_data.get("tool_calls") if isinstance(raw_data.get("tool_calls"), list) else []
    name, args, call_id, error = _extract_call(item)
    warnings = ["legacy_content_json"]
    if calls and len(calls) > 1:
        warnings.append("multiple_tool_calls_collapsed_to_first")
    if error or not name:
        return {
            "type": "error",
            "error": {"code": "SCHEMA_VALIDATION_FAILED", "message": error or "tool_call function name is required"},
            "raw_assistant_message": {"role": "assistant", "content": _json_dumps(raw_data)},
            "message_for_history": _assistant_tool_message(call_id, name, {}),
            "deferred_tool_calls": calls[1:] if calls else [],
            "warnings": warnings,
            "raw_tool_call_id": call_id,
        }
    return {
        "type": "tool_call",
        "tool_call": {"name": name, "arguments": args},
        "raw_tool_call_id": call_id,
        "raw_assistant_message": {"role": "assistant", "content": _json_dumps(raw_data)},
        "message_for_history": _assistant_tool_message(call_id, name, args),
        "deferred_tool_calls": calls[1:] if calls else [],
        "deferred_policy": "collapse_to_first",
        "warnings": warnings,
    }


def build_tool_result_message(call_id: str, name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": _json_dumps(result),
    }
