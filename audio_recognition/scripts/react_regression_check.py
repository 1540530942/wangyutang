from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from audio_recognition.harness.react_loop import decide_transcript


DEFAULT_TEXT = "\u5148\u524d\u8fdb\u518d\u5411\u4e0a\u770b"
SECOND_TEXT = "\u5de6\u8f6c\uff0c\u4e0d\u8981\u5f80\u4e0a\u770b"
EMERGENCY_TEXT = "\u4e0d\u8981\u52a8\u3001\u5411\u53f3\u8f6c\u4e00\u4e0b"


def fetch_json(url: str, timeout: float = 5) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def print_section(name: str, data: Any) -> None:
    print(f"\n## {name}")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def build_config(base_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    llm = {
        "endpoint": args.llm_endpoint,
        "model": args.llm_model,
        "timeout_seconds": args.llm_timeout,
        "verify_ssl": False,
        "retries": args.llm_retries,
    }
    return {
        "skill_registry": str((base_dir / "skills" / "registry.yaml").resolve()),
        "skill_catalog": str((base_dir.parent / "action_move" / "skill_catalog.json").resolve()),
        "react_agent": {"mode": "llm", "max_steps": args.max_steps, "prompt_path": "agent/prompts/walle_system_prompt.md", "llm": llm},
    }


def summarize_envelope(envelope) -> dict[str, Any]:
    return {
        "protocol_version": envelope.protocol_version,
        "transcript": envelope.transcript,
        "tool_calls": [{"tool": call.tool, "args": call.args, "status": call.status, "error": call.error} for call in envelope.tool_calls],
        "tasks": [{"order": task.order, "skill_id": task.skill_id, "status": task.status, "error": task.error} for task in envelope.tasks],
        "dispatch_results": [
            {
                "skill_id": item.get("skill_id"),
                "status": item.get("status"),
                "error": item.get("error"),
                "action_ok": ((item.get("result") or {}).get("action_task") or {}).get("ok"),
                "action_elapsed": ((item.get("result") or {}).get("action_task") or {}).get("elapsed_seconds"),
            }
            for item in envelope.dispatch_results
        ],
        "observations": envelope.observations,
        "safety_result": envelope.safety_result,
        "final_response": envelope.final_response,
        "errors": envelope.errors,
        "turn_count": len(envelope.react_turns),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ReAct regression checks without needing the web UI.")
    parser.add_argument("--llm-endpoint", default="https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat/completions")
    parser.add_argument("--llm-model", default="qwen3-32b")
    parser.add_argument("--llm-timeout", type=float, default=140)
    parser.add_argument("--llm-retries", type=int, default=2)
    parser.add_argument("--action-server", default="http://127.0.0.1:18765")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--second-text", default=SECOND_TEXT)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--real", action="store_true", help="execute action tool calls on the Pi via local_first")
    parser.add_argument("--skip-llm", action="store_true", help="only run preflight emergency and observation checks")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parents[1]
    config = build_config(base_dir, args)
    cloud = {
        "local_action_server": args.action_server,
        "local_settings": {
            "unit_distance_cm": 1,
            "sensitivity": 2.0,
            "stop_publish_times": 5,
            "capture_after_move": False,
            "capture_after_servo": False,
        },
    }

    health = {}
    try:
        health = fetch_json(f"{args.action_server.rstrip('/')}/health")
    except Exception as exc:  # noqa: BLE001
        health = {"status": "unreachable", "error": str(exc)}
    print_section("action_server_health", health)

    emergency = decide_transcript(
        base_dir=base_dir,
        text=EMERGENCY_TEXT,
        router_config=config,
        cloud_config=cloud,
        dispatch_mode="dry_run",
        source="react-regression",
    )
    print_section("preflight_emergency_dry_run", summarize_envelope(emergency))
    if not emergency.tasks or emergency.tasks[0].skill_id != "emergency_stop":
        print("ERROR: emergency preflight did not produce emergency_stop", file=sys.stderr)
        return 2

    print_section("direct_robot_state_observation", {"tool": "get_robot_state", "data": health, "t": time.time()})

    if args.skip_llm:
        return 0

    mode = "local_first" if args.real else "dry_run"
    if args.real:
        try:
            stop = post_json(f"{args.action_server.rstrip('/')}/execute", {"action": "emergency_stop", "settings": {"stop_publish_times": 5}})
            print_section("pre_real_emergency_stop", stop)
        except Exception as exc:  # noqa: BLE001
            print_section("pre_real_emergency_stop_error", {"error": str(exc)})
            return 3

    envelope = decide_transcript(
        base_dir=base_dir,
        text=args.text,
        router_config=config,
        cloud_config=cloud,
        dispatch_mode=mode,
        source="react-regression",
    )
    print_section(f"react_{mode}_primary", summarize_envelope(envelope))

    second_envelope = decide_transcript(
        base_dir=base_dir,
        text=args.second_text,
        router_config=config,
        cloud_config=cloud,
        dispatch_mode=mode,
        source="react-regression",
    )
    print_section(f"react_{mode}_second", summarize_envelope(second_envelope))

    if args.real:
        try:
            stop = post_json(f"{args.action_server.rstrip('/')}/execute", {"action": "emergency_stop", "settings": {"stop_publish_times": 5}})
            print_section("post_real_emergency_stop", stop)
            print_section("post_real_health", fetch_json(f"{args.action_server.rstrip('/')}/health"))
        except Exception as exc:  # noqa: BLE001
            print_section("post_real_stop_error", {"error": str(exc)})
            return 4

    if envelope.errors or second_envelope.errors:
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
