"""必过功能集 — 技能路由端到端校验。

分三组：
1. TestExactAliasRouting   — exact-alias 快速路径绕过 LLM
2. TestRegistryRouteSmoke  — skill_registry 路由字段正确
3. TestDispatchEnvelopeFormat — dispatch envelope 格式合法（mock LLM）
4. TestToolValidatorSmoke  — validate_tool_call 对合法调用不拒绝
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from audio_recognition.core.envelope import DecisionEnvelope, ToolCall
from audio_recognition.harness.react_loop import decide_transcript
from audio_recognition.skills.registry import load_skill_registry
from audio_recognition.tools.tool_validator import validate_tool_call

from smoke.cases import SKILL_CASES, UNIQUE_SKILL_CASES, SkillCase

_BASE = Path(__file__).resolve().parents[1] / "audio_recognition"
_REGISTRY_PATH = str(_BASE / "skills" / "registry.yaml")
_CATALOG_PATH = str(_BASE / "tests" / "fixtures" / "skill_catalog.fixture.json")

_ROUTER_CONFIG = {
    "skill_catalog": _CATALOG_PATH,
    "skill_registry": _REGISTRY_PATH,
    "react_agent": {
        "mode": "llm",
        "llm": {"endpoint": "http://llm.local", "model": "test-model"},
    },
}

_FRONT_CLEAR = {
    "available": True,
    "front_distance_estimate_cm": 50.0,
    "confidence": 0.95,
}

_LLM_PATCH = "audio_recognition.agent.react_agent.requests.post"
_OBS_PATCH = "audio_recognition.tools.observation_executor._get_json"


def _action_llm(skill_id: str, *, order: int = 1, duration_ms: int = 800) -> Mock:
    resp = Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "dispatch_action",
                        "arguments": json.dumps({
                            "skill_id": skill_id,
                            "order": order,
                            "duration_ms": duration_ms,
                            "wait_until": "completed",
                            "confidence": 0.95,
                            "text": skill_id,
                        }, ensure_ascii=False),
                    },
                }],
            }
        }],
    }
    return resp


def _obs_llm(tool: str) -> Mock:
    resp = Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_obs",
                    "type": "function",
                    "function": {
                        "name": tool,
                        "arguments": json.dumps({"order": 1}, ensure_ascii=False),
                    },
                }],
            }
        }],
    }
    return resp


def _finish_llm() -> Mock:
    resp = Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "done"}}],
    }
    return resp


def _decide(text: str, llm_sides=None, obs_return=None) -> DecisionEnvelope:
    ctx = {}
    if llm_sides is not None:
        ctx[_LLM_PATCH] = llm_sides
    if obs_return is not None:
        ctx[_OBS_PATCH] = obs_return

    if ctx:
        patches = [
            patch(k, side_effect=v if isinstance(v, list) else None,
                  return_value=v if not isinstance(v, list) else None)
            for k, v in ctx.items()
        ]
        combined = patches[0]
        for p in patches[1:]:
            combined = combined.__class__(p.attribute, p.new, p.spec, p.create,
                                          p.spec_set, p.autospec, p.new_callable,
                                          **p.kwargs)
        # simpler: use contextlib.ExitStack
        import contextlib
        with contextlib.ExitStack() as stack:
            for k, v in ctx.items():
                if isinstance(v, list):
                    stack.enter_context(patch(k, side_effect=v))
                else:
                    stack.enter_context(patch(k, return_value=v))
            return decide_transcript(
                base_dir=_BASE,
                text=text,
                router_config=_ROUTER_CONFIG,
                cloud_config={"sensor_server": "http://sensor.local"},
                dispatch_mode="dry_run",
                source="smoke",
            )
    return decide_transcript(
        base_dir=_BASE,
        text=text,
        router_config=_ROUTER_CONFIG,
        cloud_config={"sensor_server": "http://sensor.local"},
        dispatch_mode="dry_run",
        source="smoke",
    )


# ──────────────────────────────────────────────────────────────────────────────

class TestExactAliasRouting(unittest.TestCase):
    """exact-alias 指令绕过 LLM，直接命中技能。"""

    def _assert_exact(self, case: SkillCase, *, obs_return=None) -> None:
        with patch(_LLM_PATCH) as llm_mock:
            if obs_return is not None:
                with patch(_OBS_PATCH, return_value=obs_return):
                    env = decide_transcript(
                        base_dir=_BASE,
                        text=case.transcript,
                        router_config=_ROUTER_CONFIG,
                        cloud_config={"sensor_server": "http://sensor.local"},
                        dispatch_mode="dry_run",
                        source="smoke",
                    )
            else:
                env = decide_transcript(
                    base_dir=_BASE,
                    text=case.transcript,
                    router_config=_ROUTER_CONFIG,
                    cloud_config={},
                    dispatch_mode="dry_run",
                    source="smoke",
                )
        llm_mock.assert_not_called()
        if case.skill_id == "emergency_stop":
            ids = [r.get("skill_id") for r in env.dispatch_results]
        else:
            ids = [t.skill_id for t in env.tasks]
        self.assertIn(
            case.skill_id, ids,
            f"[{case.skill_id}] transcript={case.transcript!r} ids={ids}",
        )

    def test_emergency_stop_english(self):
        c = next(c for c in SKILL_CASES if c.skill_id == "emergency_stop" and "stop" in c.transcript)
        self._assert_exact(c)

    def test_emergency_stop_chinese(self):
        c = next(c for c in SKILL_CASES if c.skill_id == "emergency_stop" and "急停" in c.transcript)
        self._assert_exact(c)

    def test_move_forward_exact(self):
        c = next(c for c in SKILL_CASES if c.skill_id == "move_forward" and c.exact_alias)
        self._assert_exact(c, obs_return=_FRONT_CLEAR)

    def test_move_backward_exact(self):
        c = next(c for c in SKILL_CASES if c.skill_id == "move_backward" and c.exact_alias)
        self._assert_exact(c)

    def test_move_left_exact(self):
        c = next(c for c in SKILL_CASES if c.skill_id == "move_left" and c.exact_alias)
        self._assert_exact(c)

    def test_move_right_exact(self):
        c = next(c for c in SKILL_CASES if c.skill_id == "move_right" and c.exact_alias)
        self._assert_exact(c)

    def test_turn_left_exact(self):
        c = next(c for c in SKILL_CASES if c.skill_id == "turn_left" and c.exact_alias)
        self._assert_exact(c)

    def test_turn_right_exact(self):
        c = next(c for c in SKILL_CASES if c.skill_id == "turn_right" and c.exact_alias)
        self._assert_exact(c)


class TestRegistryRouteSmoke(unittest.TestCase):
    """每个必过技能的 registry 路由字段必须正确。"""

    def setUp(self):
        self._reg = load_skill_registry(
            registry_path=_REGISTRY_PATH,
            catalog_path=_CATALOG_PATH,
        )

    def _check(self, case: SkillCase) -> None:
        spec = self._reg.get(case.skill_id)
        self.assertIsNotNone(spec, f"技能未注册: {case.skill_id}")
        self.assertEqual(
            spec.tool, case.tool,
            f"[{case.skill_id}] tool 期望={case.tool} 实际={spec.tool}",
        )

    def test_emergency_stop_registered(self):
        self._check(UNIQUE_SKILL_CASES["emergency_stop"])

    def test_reset_pose_registered(self):
        self._check(UNIQUE_SKILL_CASES["reset_pose"])

    def test_move_forward_registered(self):
        self._check(UNIQUE_SKILL_CASES["move_forward"])

    def test_move_backward_registered(self):
        self._check(UNIQUE_SKILL_CASES["move_backward"])

    def test_move_left_registered(self):
        self._check(UNIQUE_SKILL_CASES["move_left"])

    def test_move_right_registered(self):
        self._check(UNIQUE_SKILL_CASES["move_right"])

    def test_turn_left_registered(self):
        self._check(UNIQUE_SKILL_CASES["turn_left"])

    def test_turn_right_registered(self):
        self._check(UNIQUE_SKILL_CASES["turn_right"])

    def test_look_left_registered(self):
        self._check(UNIQUE_SKILL_CASES["look_left"])

    def test_look_right_registered(self):
        self._check(UNIQUE_SKILL_CASES["look_right"])

    def test_look_up_registered(self):
        self._check(UNIQUE_SKILL_CASES["look_up"])

    def test_look_down_registered(self):
        self._check(UNIQUE_SKILL_CASES["look_down"])

    def test_rgb_on_registered(self):
        self._check(UNIQUE_SKILL_CASES["rgb_on"])

    def test_rgb_off_registered(self):
        self._check(UNIQUE_SKILL_CASES["rgb_off"])

    def test_front_distance_registered(self):
        self._check(UNIQUE_SKILL_CASES["front_distance"])

    def test_camera_snapshot_registered(self):
        self._check(UNIQUE_SKILL_CASES["camera_snapshot"])

    def test_inspect_scene_registered(self):
        self._check(UNIQUE_SKILL_CASES["inspect_scene"])


class TestDispatchEnvelopeFormat(unittest.TestCase):
    """dispatch 后的 envelope 内容格式必须合法。"""

    def _action_env(self, case: SkillCase) -> DecisionEnvelope:
        with patch(_LLM_PATCH, side_effect=[_action_llm(case.skill_id), _finish_llm()]):
            return decide_transcript(
                base_dir=_BASE,
                text=case.transcript,
                router_config=_ROUTER_CONFIG,
                cloud_config={},
                dispatch_mode="dry_run",
                source="smoke",
            )

    def _obs_env(self, case: SkillCase) -> DecisionEnvelope:
        with (
            patch(_LLM_PATCH, side_effect=[_obs_llm(case.tool), _finish_llm()]),
            patch(_OBS_PATCH, return_value=_FRONT_CLEAR),
        ):
            return decide_transcript(
                base_dir=_BASE,
                text=case.transcript,
                router_config=_ROUTER_CONFIG,
                cloud_config={"sensor_server": "http://sensor.local"},
                dispatch_mode="dry_run",
                source="smoke",
            )

    def _assert_task(self, env: DecisionEnvelope, skill_id: str) -> None:
        ids = [t.skill_id for t in env.tasks]
        self.assertIn(skill_id, ids, f"[{skill_id}] tasks={ids}")

    def _assert_obs(self, env: DecisionEnvelope, tool: str) -> None:
        tools = [o.get("tool") for o in env.observations]
        self.assertIn(tool, tools, f"[{tool}] observations={env.observations}")

    def test_reset_pose_task(self):
        self._assert_task(self._action_env(UNIQUE_SKILL_CASES["reset_pose"]), "reset_pose")

    def test_look_left_task(self):
        self._assert_task(self._action_env(UNIQUE_SKILL_CASES["look_left"]), "look_left")

    def test_look_right_task(self):
        self._assert_task(self._action_env(UNIQUE_SKILL_CASES["look_right"]), "look_right")

    def test_look_up_task(self):
        self._assert_task(self._action_env(UNIQUE_SKILL_CASES["look_up"]), "look_up")

    def test_look_down_task(self):
        self._assert_task(self._action_env(UNIQUE_SKILL_CASES["look_down"]), "look_down")

    def test_rgb_on_task(self):
        self._assert_task(self._action_env(UNIQUE_SKILL_CASES["rgb_on"]), "rgb_on")

    def test_rgb_off_task(self):
        self._assert_task(self._action_env(UNIQUE_SKILL_CASES["rgb_off"]), "rgb_off")

    def test_front_distance_observation(self):
        self._assert_obs(self._obs_env(UNIQUE_SKILL_CASES["front_distance"]), "front_distance")

    def test_camera_snapshot_observation(self):
        with (
            patch(_LLM_PATCH, side_effect=[_obs_llm("camera_snapshot"), _finish_llm()]),
            patch(
                "audio_recognition.tools.observation_executor._post_json",
                return_value={"image_url": "http://cam.local/snap.jpg", "captured_at": 1.0},
            ),
        ):
            env = decide_transcript(
                base_dir=_BASE,
                text="拍照",
                router_config=_ROUTER_CONFIG,
                cloud_config={"camera_server": "http://cam.local"},
                dispatch_mode="dry_run",
                source="smoke",
            )
        self._assert_obs(env, "camera_snapshot")


class TestToolValidatorSmoke(unittest.TestCase):
    """validate_tool_call 对合法调用不拒绝，对超时调用必须拒绝。"""

    def setUp(self):
        self._reg = load_skill_registry(
            registry_path=_REGISTRY_PATH,
            catalog_path=_CATALOG_PATH,
        )

    def _valid_call(self, skill_id: str) -> tuple[DecisionEnvelope, ToolCall]:
        spec = self._reg.get(skill_id)
        duration = spec.max_duration_ms if spec and spec.max_duration_ms else 800
        env = DecisionEnvelope(device_id="test", source="smoke", transcript=skill_id)
        call = ToolCall(
            tool="dispatch_action",
            args={
                "skill_id": skill_id,
                "order": 1,
                "duration_ms": duration,
                "wait_until": "completed",
                "confidence": 0.9,
                "text": skill_id,
            },
        )
        return env, call

    def _check_accepted(self, skill_id: str) -> None:
        env, call = self._valid_call(skill_id)
        task = validate_tool_call(env, call, registry_path=_REGISTRY_PATH, catalog_path=_CATALOG_PATH)
        self.assertIsNotNone(task, f"[{skill_id}] 合法调用被拒绝; errors={env.errors}")

    def test_validator_accepts_reset_pose(self):
        self._check_accepted("reset_pose")

    def test_validator_accepts_move_forward(self):
        self._check_accepted("move_forward")

    def test_validator_accepts_move_backward(self):
        self._check_accepted("move_backward")

    def test_validator_accepts_move_left(self):
        self._check_accepted("move_left")

    def test_validator_accepts_move_right(self):
        self._check_accepted("move_right")

    def test_validator_accepts_turn_left(self):
        self._check_accepted("turn_left")

    def test_validator_accepts_turn_right(self):
        self._check_accepted("turn_right")

    def test_validator_accepts_look_left(self):
        self._check_accepted("look_left")

    def test_validator_accepts_look_right(self):
        self._check_accepted("look_right")

    def test_validator_accepts_look_up(self):
        self._check_accepted("look_up")

    def test_validator_accepts_look_down(self):
        self._check_accepted("look_down")

    def test_validator_accepts_rgb_on(self):
        self._check_accepted("rgb_on")

    def test_validator_accepts_rgb_off(self):
        self._check_accepted("rgb_off")

    def test_validator_clips_duration_over_limit(self):
        """validator 对超时 duration 应裁剪到 max_duration_ms，而非拒绝。"""
        spec = self._reg.get("turn_left")
        if spec is None or spec.max_duration_ms is None:
            self.skipTest("turn_left 无 max_duration_ms")
        env = DecisionEnvelope(device_id="test", source="smoke", transcript="turn_left")
        call = ToolCall(
            tool="dispatch_action",
            args={
                "skill_id": "turn_left",
                "order": 1,
                "duration_ms": spec.max_duration_ms + 5000,
                "wait_until": "completed",
                "confidence": 0.9,
                "text": "turn_left",
            },
        )
        task = validate_tool_call(env, call, registry_path=_REGISTRY_PATH, catalog_path=_CATALOG_PATH)
        self.assertIsNotNone(task, "合法技能超时 duration 不应被直接拒绝")
        self.assertLessEqual(
            task.duration_ms, spec.max_duration_ms,
            f"duration 应被裁剪到 {spec.max_duration_ms}ms，实际={task.duration_ms}",
        )


if __name__ == "__main__":
    unittest.main()
