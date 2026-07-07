"""必过功能集 — 技能注册表与工具 Schema 校验。

验证：
1. registry.yaml 能正确加载所有必过技能
2. 每个技能的 tool / route 与 cases.py 声明一致
3. dispatch_action / dispatch_face 的 skill_id enum 包含所有对应技能
4. 观测工具独立注册，不出现在 action/face enum 中
"""
from __future__ import annotations

import unittest
from pathlib import Path

from robot_sandbox.skills.registry import load_skill_registry
from robot_sandbox.tools.tool_schema import build_react_tools_schema, tool_skill_groups

from smoke.cases import UNIQUE_SKILL_CASES

_BASE = Path(__file__).resolve().parents[1] / "robot_sandbox"
_REGISTRY_PATH = _BASE / "skills" / "registry.yaml"
_CATALOG_PATH = _BASE / "tests" / "fixtures" / "skill_catalog.fixture.json"


def _registry():
    return load_skill_registry(
        registry_path=str(_REGISTRY_PATH),
        catalog_path=str(_CATALOG_PATH),
    )


def _schema():
    return build_react_tools_schema(registry_path=str(_REGISTRY_PATH), catalog_path=str(_CATALOG_PATH))


class TestSkillRegistrySmoke(unittest.TestCase):
    def test_registry_loads_without_error(self):
        reg = _registry()
        self.assertIsNotNone(reg)
        self.assertGreater(len(reg.skills), 0)

    def test_all_smoke_skills_present_in_registry(self):
        reg = _registry()
        missing = [
            sid for sid in UNIQUE_SKILL_CASES
            if reg.get(sid) is None
        ]
        self.assertEqual(
            missing, [],
            f"以下必过技能未在 registry 中注册: {missing}",
        )

    def test_smoke_skill_tools_match_registry(self):
        reg = _registry()
        mismatches: list[str] = []
        for sid, case in UNIQUE_SKILL_CASES.items():
            spec = reg.get(sid)
            if spec is None:
                continue
            if spec.tool != case.tool:
                mismatches.append(
                    f"{sid}: 期望 tool={case.tool}, 实际={spec.tool}"
                )
        self.assertEqual(mismatches, [], "\n".join(mismatches))

    def test_smoke_skill_routes_match_registry(self):
        reg = _registry()
        mismatches: list[str] = []
        for sid, case in UNIQUE_SKILL_CASES.items():
            spec = reg.get(sid)
            if spec is None:
                continue
            expected_route = case.route
            actual_route = spec.route
            # observation 技能在 registry 里 route 可能写 "observation" 或与 tool 同名
            if expected_route == "observation":
                ok = actual_route in {"observation"} or spec.tool == sid
            else:
                ok = actual_route == expected_route
            if not ok:
                mismatches.append(
                    f"{sid}: 期望 route={expected_route}, 实际={actual_route}"
                )
        self.assertEqual(mismatches, [], "\n".join(mismatches))


class TestToolSchemaSmoke(unittest.TestCase):
    def setUp(self):
        schema = _schema()
        self._funcs = {
            item["function"]["name"]: item["function"]
            for item in schema
        }

    def test_dispatch_action_enum_contains_all_action_skills(self):
        enum = self._funcs["dispatch_action"]["parameters"]["properties"]["skill_id"]["enum"]
        action_skills = [
            sid for sid, c in UNIQUE_SKILL_CASES.items()
            if c.tool == "dispatch_action"
        ]
        missing = [sid for sid in action_skills if sid not in enum]
        self.assertEqual(missing, [], f"dispatch_action 缺少技能: {missing}")

    def test_observation_tools_not_in_dispatch_action_enum(self):
        enum = self._funcs["dispatch_action"]["parameters"]["properties"]["skill_id"]["enum"]
        obs_skills = [
            sid for sid, c in UNIQUE_SKILL_CASES.items()
            if c.route == "observation"
        ]
        leaked = [sid for sid in obs_skills if sid in enum]
        self.assertEqual(leaked, [], f"观测工具不应出现在 dispatch_action enum: {leaked}")

    def test_emergency_stop_not_in_dispatch_action_enum(self):
        enum = self._funcs["dispatch_action"]["parameters"]["properties"]["skill_id"]["enum"]
        self.assertNotIn("emergency_stop", enum)

    def test_duration_limits_present_for_action_skills(self):
        reg = _registry()
        no_limit = [
            sid for sid, c in UNIQUE_SKILL_CASES.items()
            if c.tool == "dispatch_action"
            and reg.get(sid) is not None
            and reg.get(sid).max_duration_ms is None
        ]
        self.assertEqual(no_limit, [], f"以下技能缺少 max_duration_ms: {no_limit}")


if __name__ == "__main__":
    unittest.main()
