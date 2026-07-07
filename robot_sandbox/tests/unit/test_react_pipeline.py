from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from robot_sandbox.skills.catalog_loader import create_action_task
from robot_sandbox.tools.dispatcher import dispatch_envelope
from robot_sandbox.core.envelope import DecisionEnvelope, ToolCall
from robot_sandbox.storage.envelope_store import load_envelope, save_envelope
from robot_sandbox.harness.react_loop import decide_transcript, route_transcript
from robot_sandbox.storage.replay import replay_envelope
from robot_sandbox.skills.registry import load_skill_registry
from robot_sandbox.tools.observation_executor import execute_observation_tool
from robot_sandbox.tools.tool_schema import build_react_tools_schema, tool_skill_groups
from robot_sandbox.tools.tool_validator import validate_tool_calls


BASE_DIR = Path(__file__).resolve().parents[2]
CATALOG_PATH = str((Path(__file__).resolve().parents[1] / "fixtures" / "skill_catalog.fixture.json").resolve())
ROUTER_CONFIG = {
    "skill_catalog": CATALOG_PATH,
    "skill_registry": str((BASE_DIR / "skills" / "registry.yaml").resolve()),
    "react_agent": {"mode": "llm", "llm": {"endpoint": "http://llm.local", "model": "qwen3.5-9b"}},
}


def llm_response(payload: dict) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"text": json.dumps(payload, ensure_ascii=False)}
    return response


def native_llm_message(message: dict) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": message}]}
    return response


def native_tool_response(tool: str, args: dict, *, call_id: str = "call_native_1") -> Mock:
    return native_llm_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(args, ensure_ascii=False)},
                }
            ],
        }
    )


def native_finish_response(content: str = "done") -> Mock:
    return native_llm_message({"role": "assistant", "content": content})


def action_response(skill_id: str, *, text: str = "", order: int = 1, duration_ms: int = 800, distance_cm: float | None = None) -> Mock:
    args = {
        "skill_id": skill_id,
        "order": order,
        "duration_ms": duration_ms,
        "wait_until": "completed",
        "confidence": 0.9,
        "text": text or skill_id,
    }
    if distance_cm is not None:
        args["distance_cm"] = distance_cm
    return llm_response(
        {
            "protocol_version": "react_v1_single_tool",
            "reasoning_summary": f"dispatch {skill_id}",
            "tool_call": {
                "tool": "dispatch_action",
                "args": args,
            },
        }
    )


def observation_response(tool: str, *, order: int = 1) -> Mock:
    return llm_response(
        {
            "protocol_version": "react_v1_single_tool",
            "tool_call": {"tool": tool, "args": {"order": order}},
        }
    )


def multi_tool_response() -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_first",
                            "type": "function",
                            "function": {"name": "dispatch_action", "arguments": json.dumps({"skill_id": "move_forward"})},
                        },
                        {
                            "id": "call_second",
                            "type": "function",
                            "function": {"name": "dispatch_action", "arguments": json.dumps({"skill_id": "turn_right"})},
                        },
                    ],
                }
            }
        ]
    }
    return response


def invalid_native_arguments_response() -> Mock:
    return native_llm_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_bad_args",
                    "type": "function",
                    "function": {"name": "dispatch_action", "arguments": "{bad json"},
                }
            ],
        }
    )


def finish_response(order: int = 2) -> Mock:
    return llm_response(
        {
            "protocol_version": "react_v1_single_tool",
            "type": "finish",
            "final": "done",
            "tool_call": {"tool": "finish", "args": {"order": order}},
        }
    )


class ReactPipelineTest(unittest.TestCase):
    def test_create_action_task_reports_http_detail(self) -> None:
        error = urllib.error.HTTPError(
            "http://action.local/api/tasks",
            503,
            "Service Unavailable",
            {},
            io.BytesIO(b'{"detail":"robot edge device is offline"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "robot edge device is offline"):
                create_action_task("http://action.local", "turn_left")

    def test_tool_schema_is_generated_from_skill_catalog(self) -> None:
        schema = build_react_tools_schema(CATALOG_PATH)
        functions = {item["function"]["name"]: item["function"] for item in schema}
        action_enum = functions["dispatch_action"]["parameters"]["properties"]["skill_id"]["enum"]
        face_enum = functions["dispatch_face"]["parameters"]["properties"]["skill_id"]["enum"]
        self.assertIn("move_forward", action_enum)
        self.assertIn("look_up", action_enum)
        self.assertIn("face_happy", face_enum)
        self.assertNotIn("face_happy", action_enum)
        self.assertNotIn("emergency_stop", action_enum)

    def test_tool_schema_excludes_catalog_skills_outside_voice_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.yaml"
            registry_path.write_text(
                "\n".join(
                    [
                        "version: 1",
                        "skills:",
                        "  - id: move_forward",
                        "    route: action",
                        "    tool: dispatch_action",
                        "    max_duration_ms: 900",
                        "  - id: remote_shutdown",
                        "    route: system",
                        "    tool: dispatch_action",
                        "    enabled: false",
                        "  - id: face_happy",
                        "    route: face",
                        "    tool: dispatch_face",
                        "    max_duration_ms: 4000",
                    ]
                ),
                encoding="utf-8",
            )
            groups = tool_skill_groups(registry_path)
        self.assertEqual(groups["action"], ["move_forward"])
        self.assertEqual(groups["face"], ["face_happy"])
        self.assertNotIn("remote_shutdown", groups["action"])

    def test_tool_schema_uses_catalog_fallback_when_registry_is_missing(self) -> None:
        groups = tool_skill_groups(BASE_DIR / "missing-skill-catalog.json")
        self.assertIn("move_forward", groups["action"])
        self.assertIn("face_happy", groups["face"])

    def test_tool_schema_and_validator_share_registry_duration_limits(self) -> None:
        registry = load_skill_registry(ROUTER_CONFIG["skill_registry"], CATALOG_PATH)
        schema = build_react_tools_schema(ROUTER_CONFIG["skill_registry"], CATALOG_PATH)
        functions = {item["function"]["name"]: item["function"] for item in schema}
        action_max = functions["dispatch_action"]["parameters"]["properties"]["duration_ms"]["maximum"]
        self.assertEqual(action_max, registry.max_duration_for_tool("dispatch_action"))
        self.assertIn("turn_left<=800ms", functions["dispatch_action"]["description"])

        envelope = DecisionEnvelope(
            transcript="test",
            tool_calls=[ToolCall(tool="dispatch_action", args={"skill_id": "turn_left", "duration_ms": 99999})],
        )
        envelope = validate_tool_calls(envelope, registry_path=ROUTER_CONFIG["skill_registry"], catalog_path=CATALOG_PATH)
        self.assertEqual(envelope.validated_tool_calls[0].args["duration_ms"], 800)

    def test_registry_reads_safety_metadata(self) -> None:
        registry = load_skill_registry(ROUTER_CONFIG["skill_registry"], CATALOG_PATH)
        forward = registry.get("move_forward")
        self.assertIsNotNone(forward)
        self.assertEqual(registry.defaults["observation_ttl_ms"]["camera_snapshot"], 2000)
        self.assertEqual(registry.defaults["safety_thresholds"]["min_front_distance_estimate_cm"], 15)
        self.assertEqual(forward.risk, "medium")
        self.assertEqual(forward.pre_conditions, ("front_distance_clear",))
        self.assertEqual(registry.get("camera_snapshot").route, "observation")
        self.assertEqual(registry.get("front_distance").tool, "front_distance")
        self.assertIn("看一下前面", registry.get("camera_snapshot").aliases)
        self.assertIn("前方距离", registry.get("front_distance").aliases)
        self.assertIn("左转", registry.get("turn_left").aliases)
        self.assertIn("右转", registry.get("turn_right").aliases)
        self.assertIn("摄像头向左", registry.get("look_left").aliases)
        self.assertIn("摄像头向右", registry.get("look_right").aliases)
        self.assertIn("抬头看", registry.get("look_up").aliases)
        self.assertIn("低头看", registry.get("look_down").aliases)

    def test_default_llm_config_targets_qwen32_common_api(self) -> None:
        from robot_sandbox.agent.react_agent import DEFAULT_LLM_ENDPOINT, DEFAULT_LLM_MODEL, build_llm_react_agent

        self.assertEqual(DEFAULT_LLM_ENDPOINT, "https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat/completions")
        self.assertEqual(DEFAULT_LLM_MODEL, "qwen3-32b")
        agent = build_llm_react_agent(base_dir=BASE_DIR, router_config={"react_agent": {"mode": "llm"}})
        self.assertEqual(agent.endpoint, DEFAULT_LLM_ENDPOINT)
        self.assertEqual(agent.model, DEFAULT_LLM_MODEL)
        self.assertIn("WALL-E", agent._system_prompt())
        self.assertIn("react_v1_single_tool", agent._system_prompt())
        self.assertIn("multiple native tool_calls", agent._system_prompt())
        self.assertIn("Skill aliases", agent._system_prompt())

    def test_simple_command_generates_envelope_tool_task_and_dry_run(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="请左转一下",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.protocol_version, "react_v1_single_tool")
        self.assertEqual(envelope.tool_calls[0].tool, "dispatch_action")
        self.assertEqual(envelope.tasks[0].skill_id, "turn_left")
        self.assertEqual(envelope.dispatch_results[0]["status"], "dry_run")
        self.assertTrue(envelope.safety_result["allowed"])

    def test_sequence_command_generates_ordered_tasks(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[
                action_response("turn_left", text="左转", order=1),
                action_response("turn_right", text="然后右转", order=2),
                finish_response(3),
            ],
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="左转，然后右转",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual([task.skill_id for task in envelope.tasks], ["turn_left", "turn_right"])
        self.assertEqual([task.order for task in envelope.tasks], [1, 2])

    def test_exact_forward_alias_observes_distance_before_dispatch(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post") as llm_post, patch(
            "robot_sandbox.tools.observation_executor._get_json",
            return_value={"available": True, "front_distance_estimate_cm": 40, "confidence": 0.9},
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="\u524d\u8fdb",
                router_config=ROUTER_CONFIG,
                cloud_config={"sensor_server": "http://sensor.local"},
                dispatch_mode="dry_run",
                source="unit",
            )
        llm_post.assert_not_called()
        self.assertEqual(envelope.observations[0]["tool"], "front_distance")
        self.assertTrue(envelope.observations[0]["preflight"])
        self.assertEqual(envelope.tasks[0].skill_id, "move_forward")
        self.assertEqual(envelope.dispatch_results[0]["status"], "dry_run")

    def test_route_transcript_reports_rejected_forward_action(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post") as llm_post, patch(
            "robot_sandbox.tools.observation_executor._get_json",
            return_value={"available": True, "front_distance_estimate_cm": 25, "confidence": 0.9, "age_seconds": 99},
        ):
            routed = route_transcript(
                base_dir=BASE_DIR,
                text="\u524d\u8fdb",
                router_config=ROUTER_CONFIG,
                cloud_config={"sensor_server": "http://sensor.local"},
                route_action=True,
                source="unit",
            )
        llm_post.assert_not_called()
        self.assertEqual(routed["skill_id"], "move_forward")
        self.assertEqual(routed["plan"]["route"], "action")
        self.assertEqual(routed["action_error"], "front_distance_stale")
        self.assertIsNone(routed["action_task"])

    def test_exact_compound_action_aliases_bypass_llm(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post") as llm_post:
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="\u5411\u5de6\u8f6c\u7136\u540e\u6389\u5934",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        llm_post.assert_not_called()
        self.assertEqual([task.skill_id for task in envelope.tasks], ["turn_left", "turn_left"])
        self.assertEqual([task.order for task in envelope.tasks], [1, 2])
        self.assertEqual([item["status"] for item in envelope.dispatch_results], ["dry_run", "dry_run"])

    def test_native_tool_call_is_normalized_to_internal_tool_call(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[
                native_tool_response(
                    "dispatch_action",
                    {
                        "skill_id": "turn_left",
                        "duration_ms": 500,
                        "wait_until": "completed",
                        "confidence": 0.95,
                        "text": "左转",
                    },
                    call_id="call_native_turn_left",
                ),
                native_finish_response("done"),
            ],
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="请左转一下",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.tool_calls[0].call_id, "call_native_turn_left")
        self.assertEqual(envelope.tasks[0].skill_id, "turn_left")
        self.assertEqual(envelope.final_response, "done")
        self.assertEqual(envelope.errors, [])
        self.assertEqual(envelope.react_messages[3]["tool_call_id"], "call_native_turn_left")
        self.assertEqual(envelope.react_turns[0]["message_for_history"]["tool_calls"][0]["id"], "call_native_turn_left")

    def test_move_forward_requires_recent_front_distance_observation(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[action_response("move_forward", text="前进"), finish_response(2)]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="前进",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="cloud_queue",
                source="unit",
            )
        self.assertEqual(envelope.tasks[0].skill_id, "move_forward")
        self.assertEqual(envelope.tasks[0].status, "rejected")
        self.assertEqual(envelope.dispatch_results[0]["status"], "rejected")
        self.assertEqual(envelope.safety_result["reason"], "recent_front_distance_required")

    def test_move_forward_rejects_when_front_distance_too_close(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("camera_snapshot", order=1), action_response("move_forward", text="前进", order=2), finish_response(3)],
        ), patch("robot_sandbox.tools.observation_executor._post_json", return_value={"status": "ok", "front_distance_estimate_cm": 1}):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="看一下前面然后前进",
                router_config=ROUTER_CONFIG,
                cloud_config={"camera_server": "http://camera.local"},
                dispatch_mode="cloud_queue",
                source="unit",
            )
        self.assertEqual(envelope.observations[0]["tool"], "camera_snapshot")
        self.assertEqual(envelope.tasks[0].status, "rejected")
        self.assertEqual(envelope.dispatch_results[0]["status"], "rejected")
        self.assertEqual(envelope.safety_result["reason"], "front_distance_too_close")
        self.assertEqual(envelope.safety_result["observed_value"], 1.0)
        self.assertEqual(envelope.safety_result["threshold_cm"], 15)

    def test_move_forward_allows_when_front_distance_clear(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("front_distance", order=1), action_response("move_forward", text="前进", order=2), finish_response(3)],
        ), patch("robot_sandbox.tools.observation_executor._get_json", return_value={"available": True, "front_distance_estimate_cm": 40, "confidence": 0.9}):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="看一下前面然后前进",
                router_config=ROUTER_CONFIG,
                cloud_config={"sensor_server": "http://sensor.local"},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.tasks[0].skill_id, "move_forward")
        self.assertEqual(envelope.tasks[0].status, "completed")
        self.assertEqual(envelope.dispatch_results[0]["status"], "dry_run")
        self.assertTrue(envelope.safety_result["allowed"])

    def test_exact_forward_distance_uses_action_path_with_distance_override(self) -> None:
        with patch("robot_sandbox.tools.observation_executor._get_json", return_value={"available": True, "front_distance_estimate_cm": 40, "confidence": 0.9}):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="前进25cm",
                router_config=ROUTER_CONFIG,
                cloud_config={"sensor_server": "http://sensor.local"},
                dispatch_mode="dry_run",
                source="unit",
            )

        self.assertEqual(envelope.observations[0]["tool"], "front_distance")
        self.assertEqual(envelope.tasks[0].skill_id, "move_forward")
        self.assertEqual(envelope.tasks[0].settings_override["unit_distance_cm"], 25.0)
        self.assertEqual(envelope.dispatch_results[0]["status"], "dry_run")

    def test_front_distance_low_confidence_rejects_forward_motion(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("front_distance", order=1), action_response("move_forward", text="forward", order=2), finish_response(3)],
        ), patch("robot_sandbox.tools.observation_executor._get_json", return_value={"available": True, "front_distance_estimate_cm": 40, "confidence": 0.2}):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="front distance then forward",
                router_config=ROUTER_CONFIG,
                cloud_config={"sensor_server": "http://sensor.local"},
                dispatch_mode="cloud_queue",
                source="unit",
            )
        self.assertEqual(envelope.tasks[0].status, "rejected")
        self.assertEqual(envelope.safety_result["reason"], "front_distance_low_confidence")

    def test_front_distance_old_reported_at_rejects_forward_motion(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("front_distance", order=1), action_response("move_forward", text="forward", order=2), finish_response(3)],
        ), patch(
            "robot_sandbox.tools.observation_executor._get_json",
            return_value={
                "available": True,
                "front_distance_estimate_cm": 40,
                "confidence": 0.9,
                "age_seconds": 0.1,
                "reported_at": 100.0,
                "sampled_at": 100.0,
            },
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="front distance then forward",
                router_config=ROUTER_CONFIG,
                cloud_config={"sensor_server": "http://sensor.local"},
                dispatch_mode="cloud_queue",
                source="unit",
            )
        self.assertEqual(envelope.tasks[0].status, "rejected")
        self.assertEqual(envelope.safety_result["reason"], "front_distance_stale")

    def test_negative_instruction_is_rejected_by_safety(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="不要左转")]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="不要左转",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="cloud_queue",
                source="unit",
            )
        self.assertEqual(envelope.safety_result["reason"], "negative_instruction_detected")
        self.assertEqual(envelope.tasks[0].status, "rejected")

    def test_emergency_stop_preflight_bypasses_llm(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post") as post:
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="不要动",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        post.assert_not_called()
        self.assertEqual(envelope.tasks[0].skill_id, "emergency_stop")
        self.assertNotEqual(envelope.tasks[0].status, "rejected")
        self.assertEqual(envelope.safety_result["priority"], "highest")
        self.assertTrue(envelope.react_turns[0]["preflight"])

    def test_validator_rejects_unknown_tool_and_clips_duration(self) -> None:
        envelope = DecisionEnvelope(
            transcript="test",
            tool_calls=[
                ToolCall(tool="dispatch_action", args={"skill_id": "move_forward", "duration_ms": 99999}),
                ToolCall(tool="dispatch_action", args={"skill_id": "jump"}),
            ],
        )
        envelope = validate_tool_calls(envelope, registry_path=ROUTER_CONFIG["skill_registry"], catalog_path=CATALOG_PATH)
        self.assertEqual(envelope.validated_tool_calls[0].status, "validated")
        self.assertEqual(envelope.validated_tool_calls[0].args["duration_ms"], 1000)
        self.assertEqual(envelope.validated_tool_calls[1].status, "rejected")

    def test_cloud_dispatch_uses_executor_once(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="左转",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        envelope.dispatch_results = []
        with patch("robot_sandbox.tools.executors.create_action_task", return_value={"task": {"id": "task-1", "status": "pending"}}) as create_action_task, patch(
            "robot_sandbox.tools.dispatcher._fetch_json",
            return_value={"task": {"id": "task-1", "status": "complete"}},
        ):
            envelope = dispatch_envelope(envelope, cloud_config={"action_enabled": True, "action_server": "http://action.local"}, source="unit", dispatch_mode="cloud_queue")
        create_action_task.assert_called_once()
        self.assertEqual(envelope.dispatch_results[0]["status"], "completed")
        self.assertEqual(envelope.dispatch_results[0]["action_task_id"], "task-1")

    def test_cloud_dispatch_accepted_does_not_wait_for_completion(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="宸﹁浆"), finish_response()]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="请左转一下",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        envelope.tasks[0].wait_until = "accepted"
        envelope.dispatch_results = []
        with patch("robot_sandbox.tools.executors.create_action_task", return_value={"task": {"id": "task-accepted", "status": "pending"}}) as create_action_task, patch(
            "robot_sandbox.tools.dispatcher._fetch_json"
        ) as fetch_json:
            envelope = dispatch_envelope(envelope, cloud_config={"action_enabled": True, "action_server": "http://action.local"}, source="unit", dispatch_mode="cloud_queue")
        create_action_task.assert_called_once()
        fetch_json.assert_not_called()
        self.assertEqual(envelope.dispatch_results[0]["status"], "accepted")
        self.assertEqual(envelope.dispatch_results[0]["action_task_id"], "task-accepted")
        self.assertEqual(envelope.dispatch_results[0]["action_status"], "pending")

    def test_local_first_dispatch_posts_to_edge_controller(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="左转",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        envelope.dispatch_results = []
        with patch("robot_sandbox.tools.dispatcher._post_json", return_value={"ok": True, "skill_id": "turn_left"}) as post_json:
            envelope = dispatch_envelope(
                envelope,
                cloud_config={"local_action_server": "http://127.0.0.1:8765", "local_settings": {"unit_distance_cm": 1}},
                source="unit",
                dispatch_mode="local_first",
            )
        post_json.assert_called_once()
        self.assertTrue(post_json.call_args.args[0].endswith("/execute"))
        self.assertEqual(envelope.dispatch_results[0]["status"], "completed")

    def test_route_transcript_keeps_legacy_shape_with_envelope(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
            routed = route_transcript(
                base_dir=BASE_DIR,
                text="左转",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                route_action=False,
                source="unit",
            )
        self.assertEqual(routed["skill_id"], "turn_left")
        self.assertEqual(routed["plan"]["route"], "action")
        self.assertEqual(routed["envelope"]["tasks"][0]["skill_id"], "turn_left")

    def test_envelope_store_and_replay_from_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
                envelope = decide_transcript(
                    base_dir=BASE_DIR,
                    text="左转",
                    router_config=ROUTER_CONFIG,
                    cloud_config={},
                    dispatch_mode="dry_run",
                    source="unit",
                )
            save_envelope(data_dir, envelope)
            self.assertEqual(load_envelope(data_dir, envelope.envelope_id).transcript, "左转")
            with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
                replay = replay_envelope(
                    data_dir=data_dir,
                    envelope_id=envelope.envelope_id,
                    base_dir=BASE_DIR,
                    router_config=ROUTER_CONFIG,
                    replay_from="text",
                )
            self.assertFalse(replay["diff"]["transcript_changed"])
            self.assertEqual(replay["new_envelope"]["dispatch_results"][0]["status"], "dry_run")

    def test_front_distance_observation_reads_sonar_endpoint(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("front_distance"), finish_response(2)],
        ), patch("robot_sandbox.tools.observation_executor._get_json", return_value={"available": True, "front_distance_estimate_cm": 42.5, "confidence": 0.9}) as get_json:
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="前面距离多少",
                router_config=ROUTER_CONFIG,
                cloud_config={"sensor_server": "http://sensor.local"},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.observations[0]["tool"], "front_distance")
        self.assertEqual(envelope.observations[0]["data"]["front_distance_estimate_cm"], 42.5)
        self.assertEqual(get_json.call_args.args[0], "http://sensor.local/api/sonar")

    def test_front_distance_falls_back_to_camera_sonar_when_action_sonar_missing(self) -> None:
        def fake_get_json(url: str, timeout: float = 5) -> dict[str, Any]:
            if url == "http://action.local/api/sonar":
                raise RuntimeError("HTTP Error 404: Not Found")
            if url == "http://camera.local/api/sonar":
                return {"available": True, "front_distance_estimate_cm": 38.0, "confidence": 0.9}
            raise AssertionError(url)

        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("front_distance"), finish_response(2)],
        ), patch("robot_sandbox.tools.observation_executor._get_json", side_effect=fake_get_json) as get_json:
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="鍓嶉潰璺濈澶氬皯",
                router_config=ROUTER_CONFIG,
                cloud_config={"action_server": "http://action.local", "camera_server": "http://camera.local"},
                dispatch_mode="dry_run",
                source="unit",
            )

        self.assertEqual(envelope.observations[0]["tool"], "front_distance")
        self.assertEqual(envelope.observations[0]["data"]["front_distance_estimate_cm"], 38.0)
        self.assertEqual([call.args[0] for call in get_json.call_args_list], ["http://action.local/api/sonar", "http://camera.local/api/sonar"])

    def test_front_distance_live_mode_uses_action_task_when_sonar_endpoint_missing(self) -> None:
        def fake_get_json(url: str, timeout: float = 5) -> dict[str, Any]:
            if url == "http://action.local/api/sonar":
                raise RuntimeError("HTTP Error 404: Not Found")
            if url == "http://action.local/api/tasks/task-1":
                return {
                    "task": {
                        "id": "task-1",
                        "status": "complete",
                        "updated_at": 100.0,
                        "output": "[INFO] front_distance_estimate_cm=19.4\n[INFO] raw_mm_samples=194,194,194\n[INFO] confidence=1.0",
                    }
                }
            raise AssertionError(url)

        envelope = DecisionEnvelope(device_id="unit", source="unit", transcript="前进", dispatch_mode="cloud_queue")
        with patch("robot_sandbox.tools.observation_executor._post_json", return_value={"task": {"id": "task-1", "status": "pending"}}) as post_json, patch(
            "robot_sandbox.tools.observation_executor._get_json",
            side_effect=fake_get_json,
        ):
            observation = execute_observation_tool(
                envelope,
                ToolCall(tool="front_distance", args={"skill_id": "front_distance"}),
                {"action_server": "http://action.local"},
            )

        self.assertEqual(observation["status"], "completed")
        self.assertEqual(observation["data"]["front_distance_estimate_cm"], 19.4)
        self.assertEqual(observation["data"]["source"], "action-move-front-distance-task")
        self.assertEqual(post_json.call_args.args[0], "http://action.local/api/tasks")

    def test_observation_tool_writes_observation_and_continues(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("get_robot_state"), finish_response(2)],
        ), patch("robot_sandbox.tools.observation_executor._get_json", return_value={"status": "ok", "battery_pct": 82}):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="电量够吗",
                router_config=ROUTER_CONFIG,
                cloud_config={"local_action_server": "http://127.0.0.1:8765"},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.observations[0]["tool"], "get_robot_state")
        self.assertEqual(envelope.observations[0]["data"]["battery_pct"], 82)
        self.assertEqual(envelope.final_response, "done")

    def test_camera_snapshot_accepts_0524_fields(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[
                llm_response(
                    {
                        "protocol_version": "react_v1_single_tool",
                        "tool_call": {
                            "tool": "camera_snapshot",
                            "args": {"focus": "前方", "purpose": "判断是否能前进"},
                        },
                    }
                ),
                finish_response(2),
            ],
        ), patch("robot_sandbox.tools.observation_executor._post_json", return_value={"status": "ok", "front_distance_estimate_cm": 40, "has_person": False}) as post_json:
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="观察一下前方情况",
                router_config=ROUTER_CONFIG,
                cloud_config={"camera_server": "http://camera.local"},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.validated_tool_calls[0].args["focus"], "前方")
        self.assertEqual(envelope.validated_tool_calls[0].args["purpose"], "判断是否能前进")
        self.assertEqual(envelope.observations[0]["tool"], "camera_snapshot")
        self.assertEqual(envelope.observations[0]["data"]["front_distance_estimate_cm"], 40)
        self.assertEqual(post_json.call_args.args[1]["purpose"], "判断是否能前进")

    def test_camera_snapshot_waits_for_matching_latest_frame(self) -> None:
        requested_at = 1000.0
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("camera_snapshot"), finish_response(2)],
        ), patch(
            "robot_sandbox.tools.observation_executor._post_json",
            return_value={"ok": True, "task": {"id": "capture-1", "kind": "camera", "requested_at": requested_at}},
        ) as post_json, patch(
            "robot_sandbox.tools.observation_executor._get_json",
            side_effect=[
                {"task_id": "old", "updated_at": requested_at - 1, "has_image": True},
                {"task": {"id": "capture-1", "status": "pending"}},
                {"task_id": "capture-1", "updated_at": requested_at + 1, "has_image": True, "frame_id": "fresh"},
                {"task": {"id": "capture-1", "status": "complete"}},
            ],
        ) as get_json:
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="camera snapshot",
                router_config=ROUTER_CONFIG,
                cloud_config={"camera_server": "http://camera.local"},
                dispatch_mode="dry_run",
                source="unit",
            )
        post_json.assert_called_once()
        self.assertEqual(get_json.call_args_list[-2].args[0], "http://camera.local/api/latest?kind=camera")
        self.assertEqual(envelope.observations[0]["data"]["capture_task"]["id"], "capture-1")
        self.assertEqual(envelope.observations[0]["data"]["latest"]["frame_id"], "fresh")

    def test_exact_observation_alias_bypasses_llm(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post") as llm_post, patch(
            "robot_sandbox.tools.observation_executor._post_json",
            return_value={"status": "ok", "front_distance_estimate_cm": 40, "has_image": True},
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="看一下前面",
                router_config=ROUTER_CONFIG,
                cloud_config={"camera_server": "http://camera.local"},
                dispatch_mode="dry_run",
                source="unit",
            )
        llm_post.assert_not_called()
        self.assertEqual(envelope.tool_calls[0].tool, "camera_snapshot")
        self.assertEqual(envelope.observations[0]["status"], "completed")
        self.assertTrue(envelope.observations[0]["preflight"])
        self.assertEqual(envelope.tasks, [])

    def test_route_transcript_reports_observation_plan(self) -> None:
        with patch(
            "robot_sandbox.tools.observation_executor._post_json",
            return_value={"status": "ok", "front_distance_estimate_cm": 40, "has_image": True},
        ):
            routed = route_transcript(
                base_dir=BASE_DIR,
                text="看一下前面",
                router_config=ROUTER_CONFIG,
                cloud_config={"camera_server": "http://camera.local"},
                route_action=True,
                source="unit",
                device_id="unit-device",
            )
        self.assertEqual(routed["plan"]["route"], "observation")
        self.assertEqual(routed["plan"]["skill_id"], "camera_snapshot")
        self.assertEqual(routed["observation"]["tool"], "camera_snapshot")
        self.assertIsNone(routed["action_task"])

    def test_camera_snapshot_records_failed_capture_metadata(self) -> None:
        requested_at = 1000.0
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("camera_snapshot"), finish_response(2)],
        ), patch(
            "robot_sandbox.tools.observation_executor._post_json",
            return_value={"ok": True, "task": {"id": "capture-1", "kind": "camera", "requested_at": requested_at}},
        ), patch(
            "robot_sandbox.tools.observation_executor._get_json",
            side_effect=[
                {
                    "task_id": "capture-1",
                    "updated_at": requested_at + 1,
                    "has_image": True,
                    "capture_error": "Connection reset by peer",
                },
                {"task": {"id": "capture-1", "status": "failed", "capture_error": "Connection reset by peer"}},
            ],
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="camera snapshot",
                router_config=ROUTER_CONFIG,
                cloud_config={"camera_server": "http://camera.local"},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.observations[0]["status"], "failed")
        self.assertIn("Connection reset by peer", envelope.observations[0]["error"])
        self.assertEqual(envelope.observations[0]["data"]["latest"]["task_id"], "capture-1")

    def test_observation_failure_is_recorded_and_loop_can_finish(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[observation_response("camera_snapshot"), finish_response(2)],
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="观察一下前方情况",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.observations[0]["tool"], "camera_snapshot")
        self.assertEqual(envelope.observations[0]["status"], "failed")
        self.assertIn("camera_server is required", envelope.observations[0]["error"])
        self.assertEqual(envelope.final_response, "done")

    def test_ask_confirmation_records_pending_observation(self) -> None:
        response = llm_response(
            {
                "protocol_version": "react_v1_single_tool",
                "tool_call": {
                    "tool": "ask_confirmation",
                    "args": {"question": "\u8981\u524d\u8fdb\u5417\uff1f", "timeout_ms": 10000},
                },
            }
        )
        with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[response, finish_response(2)]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="\u597d\u50cf\u662f\u524d\u8fdb",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.observations[0]["tool"], "ask_confirmation")
        self.assertEqual(envelope.observations[0]["status"], "pending")
        self.assertEqual(envelope.observations[0]["data"]["question"], "\u8981\u524d\u8fdb\u5417\uff1f")
        self.assertEqual(envelope.observations[0]["data"]["timeout_ms"], 10000)
        self.assertEqual(envelope.observations[0]["data"]["timeout_s"], 10)

    def test_multiple_native_tool_calls_are_executed_in_order_from_deferred_queue(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post", side_effect=[multi_tool_response(), finish_response(2)]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="\u8bf7\u524d\u8fdb\u7136\u540e\u53f3\u8f6c",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual([task.skill_id for task in envelope.tasks], ["move_forward", "turn_right"])
        self.assertEqual(envelope.react_turns[0]["deferred_tool_calls"][0]["id"], "call_second")
        self.assertIn("multiple_tool_calls_collapsed_to_first", envelope.react_turns[0]["warnings"])
        self.assertIn("deferred_tool_call_replayed", envelope.react_turns[1]["warnings"])
        self.assertEqual(len(envelope.react_turns[0]["message_for_history"]["tool_calls"]), 1)

    def test_invalid_native_tool_arguments_records_agent_error(self) -> None:
        with patch("robot_sandbox.agent.react_agent.requests.post", return_value=invalid_native_arguments_response()):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="\u8bf7\u524d\u8fdb\u4e00\u6b65",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.tasks, [])
        self.assertEqual(envelope.dispatch_results, [])
        self.assertIn("invalid_function_arguments_json", envelope.errors[0]["message"])

    def test_finish_accepts_message_field(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[
                action_response("turn_left", text="左转", order=1),
                llm_response({"protocol_version": "react_v1_single_tool", "type": "finish", "message": "WALL-E"}),
            ],
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="请左转一下",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.final_response, "WALL-E")
        self.assertEqual(envelope.validated_tool_calls[-1].args["message"], "WALL-E")

    def test_sequence_forward_then_look_up_uses_real_rules(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[
                observation_response("camera_snapshot", order=1),
                action_response("move_forward", text="先前进", order=2),
                action_response("look_up", text="再向上看", order=3),
                finish_response(4),
            ],
        ), patch("robot_sandbox.tools.observation_executor._post_json", return_value={"status": "ok", "front_distance_estimate_cm": 40}):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="先前进再向上看",
                router_config=ROUTER_CONFIG,
                cloud_config={"camera_server": "http://camera.local"},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual([task.skill_id for task in envelope.tasks], ["move_forward", "look_up"])
        self.assertEqual([result["status"] for result in envelope.dispatch_results], ["dry_run", "dry_run"])
        self.assertTrue(envelope.safety_result["allowed"])

    def test_left_turn_with_negated_look_up_only_executes_turn(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[action_response("turn_left", text="左转", order=1), finish_response(2)],
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="左转，不要往上看",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual([task.skill_id for task in envelope.tasks], ["turn_left"])
        self.assertEqual(envelope.dispatch_results[0]["status"], "dry_run")
        self.assertTrue(envelope.safety_result["allowed"])

    def test_llm_react_agent_generates_sequence_without_rule_fallback(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[
                observation_response("camera_snapshot", order=1),
                action_response("move_forward", text="先往前走", order=2),
                action_response("move_backward", text="再往后走", order=3),
                action_response("look_up", text="抬头看", order=4),
                action_response("look_down", text="低头看", order=5),
                finish_response(6),
            ],
        ) as post, patch("robot_sandbox.tools.observation_executor._post_json", return_value={"status": "ok", "front_distance_estimate_cm": 40}):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="先往前走，再往后走，抬头看，不要往前走了，低头看",
                router_config=ROUTER_CONFIG,
                cloud_config={"camera_server": "http://camera.local"},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(post.call_count, 6)
        self.assertEqual([task.skill_id for task in envelope.tasks], ["move_forward", "move_backward", "look_up", "look_down"])
        self.assertTrue(envelope.safety_result["allowed"])

    def test_obstacle_then_forward_15cm_uses_llm_not_rule_path(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[
                observation_response("inspect_scene", order=1),
                action_response("move_forward", text="前进15cm", order=2, distance_cm=15),
                finish_response(3),
            ],
        ) as post, patch(
            "robot_sandbox.tools.observation_executor._post_json",
            return_value={"ok": True},
        ), patch(
            "robot_sandbox.tools.observation_executor._get_bytes",
            return_value=(b"fake-jpeg", "image/jpeg"),
        ), patch(
            "robot_sandbox.tools.observation_executor._call_vision_analyze",
            return_value={"answer": "前方没有障碍物", "model": "qwen25vl7b-q4km.gguf"},
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="看看前面有没有障碍物，没有障碍物的话前进15cm",
                router_config=ROUTER_CONFIG,
                cloud_config={
                    "camera_server": "http://camera.local",
                    "vision_analyze_url": "http://vision.local/analyze-json",
                    "vision_llm_model": "qwen25vl7b-q4km.gguf",
                },
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertGreaterEqual(post.call_count, 2)
        self.assertFalse(any(turn.get("preflight") for turn in envelope.react_turns))
        self.assertEqual(envelope.observations[0]["tool"], "inspect_scene")
        self.assertEqual([task.skill_id for task in envelope.tasks], ["move_forward"])
        self.assertEqual(envelope.tasks[0].settings_override["unit_distance_cm"], 15.0)
        self.assertEqual(envelope.dispatch_results[0]["result"]["settings_override"]["unit_distance_cm"], 15.0)

    def test_left_turn_then_backward_10cm_uses_llm_not_rule_path(self) -> None:
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[
                action_response("turn_left", text="先左转", order=1),
                action_response("move_backward", text="后退10cm", order=2, distance_cm=10),
                finish_response(3),
            ],
        ) as post:
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="先左转然后后退10cm",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(post.call_count, 3)
        self.assertFalse(any(turn.get("preflight") for turn in envelope.react_turns))
        self.assertEqual([task.skill_id for task in envelope.tasks], ["turn_left", "move_backward"])
        self.assertEqual(envelope.tasks[1].settings_override["unit_distance_cm"], 10.0)


class LatencyTraceabilityTest(unittest.TestCase):
    """Locks in the timestamp + latency-breakdown contract used for problem
    localization, replay reproduction, and RL training data."""

    def _route(self, text: str, raw: dict | None = None) -> dict:
        # Exact-action aliases bypass the LLM; patch defensively like sibling tests.
        with patch(
            "robot_sandbox.agent.react_agent.requests.post",
            side_effect=[action_response("turn_left", text=text), finish_response()],
        ):
            return route_transcript(
                base_dir=BASE_DIR,
                text=text,
                router_config=ROUTER_CONFIG,
                cloud_config={},
                route_action=False,
                source="unit",
                raw=raw,
            )

    def test_capture_and_transcribe_timestamps_forwarded_from_raw(self) -> None:
        capture_at = 1_700_000_000.0
        routed = self._route("左转", raw={"capture_at": capture_at, "asr_done_at": capture_at + 0.85})
        env = routed["envelope"]
        self.assertEqual(env["t_capture"], capture_at)
        self.assertEqual(env["t_transcribe"], capture_at + 0.85)
        # ASR stage equals the capture->transcribe gap (850 ms here).
        self.assertAlmostEqual(env["latency_ms"]["asr"], 850.0, places=1)
        self.assertIn("ingest", env["latency_ms"])

    def test_agent_and_dispatch_timestamps_set_for_non_llm_path(self) -> None:
        # Emergency / exact-action paths must still record agent + dispatch spans.
        env = self._route("左转")["envelope"]
        for field in ("t_agent_start", "t_agent_end", "t_dispatch_start", "t_dispatch_end"):
            self.assertIsNotNone(env[field], f"{field} should be set on the exact-action path")
        self.assertLessEqual(env["t_agent_start"], env["t_agent_end"])
        self.assertLessEqual(env["t_dispatch_start"], env["t_dispatch_end"])
        for stage in ("agent", "dispatch", "total"):
            self.assertIn(stage, env["latency_ms"])

    def test_latency_breakdown_survives_save_load_round_trip(self) -> None:
        capture_at = 1_700_000_000.0
        routed = self._route("左转", raw={"capture_at": capture_at, "asr_done_at": capture_at + 0.5})
        original = DecisionEnvelope(**routed["envelope"])
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            save_envelope(data_dir, original)
            loaded = load_envelope(data_dir, original.envelope_id)
        for field in (
            "t_capture",
            "t_transcribe",
            "t_agent_start",
            "t_agent_end",
            "t_dispatch_start",
            "t_dispatch_end",
            "latency_ms",
            "transcript",
        ):
            self.assertEqual(getattr(original, field), getattr(loaded, field), f"{field} changed across round-trip")


class VehicleLatencyTest(unittest.TestCase):
    """Envelope owns the full web->server->car latency/log chain (task 2)."""

    def test_vehicle_execution_extracted_from_action_task(self) -> None:
        from robot_sandbox.tools.dispatcher import _vehicle_execution_from_task

        final_task = {
            "id": "t1",
            "status": "complete",
            "claim_latency_seconds": 0.27,
            "completion_latency_seconds": 1.70,
            "output": "[INFO] 向左转 -> turn_left\n[INFO] elapsed_seconds=1.002",
        }
        ve = _vehicle_execution_from_task(final_task)
        self.assertEqual(ve["vehicle_claim"], 270.0)
        self.assertEqual(ve["vehicle_exec"], 1430.0)  # (completion - claim) * 1000
        self.assertEqual(ve["vehicle_ros"], 1002.0)   # parsed from ROS output
        self.assertIn("output_tail", ve)

    def test_vehicle_stages_fold_into_latency_breakdown(self) -> None:
        env = DecisionEnvelope()
        t = 1_700_000_000.0
        env.t_capture, env.t_transcribe = t, t + 0.3
        env.t_dispatch_start, env.t_dispatch_end = t + 0.5, t + 2.0
        env.vehicle_execution = {"vehicle_claim": 270.0, "vehicle_exec": 1430.0, "vehicle_ros": 1002.0}
        lm = env.compute_latency()
        self.assertEqual(lm["vehicle_claim"], 270.0)
        self.assertEqual(lm["vehicle_exec"], 1430.0)
        self.assertEqual(lm["vehicle_ros"], 1002.0)
        self.assertIn("asr", lm)  # web-side stages still present

    def test_no_vehicle_execution_leaves_latency_clean(self) -> None:
        env = DecisionEnvelope()
        t = 1_700_000_000.0
        env.t_capture, env.t_transcribe = t, t + 0.3
        lm = env.compute_latency()
        self.assertNotIn("vehicle_claim", lm)


if __name__ == "__main__":
    unittest.main()
