from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from audio_recognition.tools.dispatcher import dispatch_envelope
from audio_recognition.core.envelope import DecisionEnvelope, ToolCall
from audio_recognition.storage.envelope_store import load_envelope, save_envelope
from audio_recognition.harness.react_loop import decide_transcript, route_transcript
from audio_recognition.storage.replay import replay_envelope
from audio_recognition.skills.registry import load_skill_registry
from audio_recognition.tools.tool_schema import build_react_tools_schema, tool_skill_groups
from audio_recognition.tools.tool_validator import validate_tool_calls


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


def action_response(skill_id: str, *, text: str = "", order: int = 1, duration_ms: int = 800) -> Mock:
    return llm_response(
        {
            "protocol_version": "react_v1_single_tool",
            "reasoning_summary": f"dispatch {skill_id}",
            "tool_call": {
                "tool": "dispatch_action",
                "args": {
                    "skill_id": skill_id,
                    "order": order,
                    "duration_ms": duration_ms,
                    "wait_until": "completed",
                    "confidence": 0.9,
                    "text": text or skill_id,
                },
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
        self.assertIn("recent_camera_snapshot", forward.pre_conditions)
        self.assertIn("front_distance_clear", forward.pre_conditions)

    def test_default_llm_config_targets_qwen32_common_api(self) -> None:
        from audio_recognition.agent.react_agent import DEFAULT_LLM_ENDPOINT, DEFAULT_LLM_MODEL, build_llm_react_agent

        self.assertEqual(DEFAULT_LLM_ENDPOINT, "https://www.wangyutang.cn/common/api/llm/qwen3-32b/chat/completions")
        self.assertEqual(DEFAULT_LLM_MODEL, "qwen3-32b")
        agent = build_llm_react_agent(base_dir=BASE_DIR, router_config={"react_agent": {"mode": "llm"}})
        self.assertEqual(agent.endpoint, DEFAULT_LLM_ENDPOINT)
        self.assertEqual(agent.model, DEFAULT_LLM_MODEL)
        self.assertIn("WALL-E", agent._system_prompt())
        self.assertIn("react_v1_single_tool", agent._system_prompt())
        self.assertIn("one tool_call", agent._system_prompt())

    def test_simple_command_generates_envelope_tool_task_and_dry_run(self) -> None:
        with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="左转",
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
            "audio_recognition.agent.react_agent.requests.post",
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

    def test_native_tool_call_is_normalized_to_internal_tool_call(self) -> None:
        with patch(
            "audio_recognition.agent.react_agent.requests.post",
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
                text="左转",
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

    def test_move_forward_requires_recent_camera_observation(self) -> None:
        with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[action_response("move_forward", text="前进"), finish_response(2)]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="前进",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.tasks[0].skill_id, "move_forward")
        self.assertEqual(envelope.tasks[0].status, "rejected")
        self.assertEqual(envelope.dispatch_results[0]["status"], "rejected")
        self.assertEqual(envelope.safety_result["reason"], "recent_camera_snapshot_required")

    def test_move_forward_rejects_when_front_distance_too_close(self) -> None:
        with patch(
            "audio_recognition.agent.react_agent.requests.post",
            side_effect=[observation_response("camera_snapshot", order=1), action_response("move_forward", text="前进", order=2), finish_response(3)],
        ), patch("audio_recognition.tools.observation_executor._post_json", return_value={"status": "ok", "front_distance_estimate_cm": 1}):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="看一下前面然后前进",
                router_config=ROUTER_CONFIG,
                cloud_config={"camera_server": "http://camera.local"},
                dispatch_mode="dry_run",
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
            "audio_recognition.agent.react_agent.requests.post",
            side_effect=[observation_response("front_distance", order=1), action_response("move_forward", text="前进", order=2), finish_response(3)],
        ), patch("audio_recognition.tools.observation_executor._get_json", return_value={"available": True, "front_distance_estimate_cm": 40, "confidence": 0.9}):
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

    def test_negative_instruction_is_rejected_by_safety(self) -> None:
        with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="不要左转")]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="不要左转",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.safety_result["reason"], "negative_instruction_detected")
        self.assertEqual(envelope.tasks[0].status, "rejected")

    def test_emergency_stop_preflight_bypasses_llm(self) -> None:
        with patch("audio_recognition.agent.react_agent.requests.post") as post:
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
        with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="左转",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        envelope.dispatch_results = []
        with patch("audio_recognition.tools.executors.create_action_task", return_value={"task": {"id": "task-1"}}) as create_action_task:
            envelope = dispatch_envelope(envelope, cloud_config={"action_enabled": True, "action_server": "http://action.local"}, source="unit", dispatch_mode="cloud_queue")
        create_action_task.assert_called_once()
        self.assertEqual(envelope.dispatch_results[0]["status"], "completed")

    def test_local_first_dispatch_posts_to_edge_controller(self) -> None:
        with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="左转",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        envelope.dispatch_results = []
        with patch("audio_recognition.tools.dispatcher._post_json", return_value={"ok": True, "skill_id": "turn_left"}) as post_json:
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
        with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
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
            with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
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
            with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[action_response("turn_left", text="左转"), finish_response()]):
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
            "audio_recognition.agent.react_agent.requests.post",
            side_effect=[observation_response("front_distance"), finish_response(2)],
        ), patch("audio_recognition.tools.observation_executor._get_json", return_value={"available": True, "front_distance_estimate_cm": 42.5, "confidence": 0.9}) as get_json:
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

    def test_observation_tool_writes_observation_and_continues(self) -> None:
        with patch(
            "audio_recognition.agent.react_agent.requests.post",
            side_effect=[observation_response("get_robot_state"), finish_response(2)],
        ), patch("audio_recognition.tools.observation_executor._get_json", return_value={"status": "ok", "battery_pct": 82}):
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
            "audio_recognition.agent.react_agent.requests.post",
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
        ), patch("audio_recognition.tools.observation_executor._post_json", return_value={"status": "ok", "front_distance_estimate_cm": 40, "has_person": False}) as post_json:
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="看一下前面",
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

    def test_observation_failure_is_recorded_and_loop_can_finish(self) -> None:
        with patch(
            "audio_recognition.agent.react_agent.requests.post",
            side_effect=[observation_response("camera_snapshot"), finish_response(2)],
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="\u770b\u4e00\u4e0b\u524d\u9762",
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
        with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[response, finish_response(2)]):
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

    def test_multiple_native_tool_calls_are_collapsed_to_first_and_deferred(self) -> None:
        with patch("audio_recognition.agent.react_agent.requests.post", side_effect=[multi_tool_response(), finish_response(2)]):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="\u524d\u8fdb\u7136\u540e\u53f3\u8f6c",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual([task.skill_id for task in envelope.tasks], ["move_forward"])
        self.assertEqual(envelope.react_turns[0]["deferred_tool_calls"][0]["id"], "call_second")
        self.assertIn("multiple_tool_calls_collapsed_to_first", envelope.react_turns[0]["warnings"])
        self.assertEqual(len(envelope.react_turns[0]["message_for_history"]["tool_calls"]), 1)

    def test_invalid_native_tool_arguments_records_agent_error(self) -> None:
        with patch("audio_recognition.agent.react_agent.requests.post", return_value=invalid_native_arguments_response()):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="\u524d\u8fdb",
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
            "audio_recognition.agent.react_agent.requests.post",
            side_effect=[
                action_response("turn_left", text="左转", order=1),
                llm_response({"protocol_version": "react_v1_single_tool", "type": "finish", "message": "WALL-E"}),
            ],
        ):
            envelope = decide_transcript(
                base_dir=BASE_DIR,
                text="左转",
                router_config=ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="unit",
            )
        self.assertEqual(envelope.final_response, "WALL-E")
        self.assertEqual(envelope.validated_tool_calls[-1].args["message"], "WALL-E")

    def test_sequence_forward_then_look_up_uses_real_rules(self) -> None:
        with patch(
            "audio_recognition.agent.react_agent.requests.post",
            side_effect=[
                observation_response("camera_snapshot", order=1),
                action_response("move_forward", text="先前进", order=2),
                action_response("look_up", text="再向上看", order=3),
                finish_response(4),
            ],
        ), patch("audio_recognition.tools.observation_executor._post_json", return_value={"status": "ok", "front_distance_estimate_cm": 40}):
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
            "audio_recognition.agent.react_agent.requests.post",
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
            "audio_recognition.agent.react_agent.requests.post",
            side_effect=[
                observation_response("camera_snapshot", order=1),
                action_response("move_forward", text="先往前走", order=2),
                action_response("move_backward", text="再往后走", order=3),
                action_response("look_up", text="抬头看", order=4),
                action_response("look_down", text="低头看", order=5),
                finish_response(6),
            ],
        ) as post, patch("audio_recognition.tools.observation_executor._post_json", return_value={"status": "ok", "front_distance_estimate_cm": 40}):
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


if __name__ == "__main__":
    unittest.main()
