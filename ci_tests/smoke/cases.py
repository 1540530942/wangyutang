"""必过功能集 — 测试用例数据。

每条 SkillCase 对应一个技能（不含 face_* 表情类），包含：
- transcript: 代表性语音指令
- skill_id:   期望解析到的技能 ID
- tool:       期望调用的工具名
- route:      期望路由分类
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillCase:
    skill_id: str
    transcript: str
    tool: str
    route: str
    # 若为 True，exact-alias 路径应直接命中，不走 LLM
    exact_alias: bool = False


SKILL_CASES: list[SkillCase] = [
    # ── 急停 ────────────────────────────────────────────────────────────
    SkillCase(
        skill_id="emergency_stop",
        transcript="stop",
        tool="emergency_stop",
        route="action",
        exact_alias=True,
    ),
    SkillCase(
        skill_id="emergency_stop",
        transcript="急停",
        tool="emergency_stop",
        route="action",
        exact_alias=True,
    ),
    # ── 复位 ─────────────────────────────────────────────────────────────
    SkillCase(
        skill_id="reset_pose",
        transcript="reset",
        tool="dispatch_action",
        route="action",
    ),
    # ── 底盘移动 ──────────────────────────────────────────────────────────
    SkillCase(
        skill_id="move_forward",
        transcript="向前走",
        tool="dispatch_action",
        route="action",
        exact_alias=True,
    ),
    SkillCase(
        skill_id="move_forward",
        transcript="前进",
        tool="dispatch_action",
        route="action",
        exact_alias=True,
    ),
    SkillCase(
        skill_id="move_backward",
        transcript="后退",
        tool="dispatch_action",
        route="action",
        exact_alias=True,
    ),
    SkillCase(
        skill_id="move_left",
        transcript="左移",
        tool="dispatch_action",
        route="action",
        exact_alias=True,
    ),
    SkillCase(
        skill_id="move_right",
        transcript="右移",
        tool="dispatch_action",
        route="action",
        exact_alias=True,
    ),
    # ── 原地转向 ───────────────────────────────────────────────────────────
    SkillCase(
        skill_id="turn_left",
        transcript="左转",
        tool="dispatch_action",
        route="action",
        exact_alias=True,
    ),
    SkillCase(
        skill_id="turn_right",
        transcript="右转",
        tool="dispatch_action",
        route="action",
        exact_alias=True,
    ),
    # ── 摄像头转向 ─────────────────────────────────────────────────────────
    SkillCase(
        skill_id="look_left",
        transcript="向左看",
        tool="dispatch_action",
        route="action",
    ),
    SkillCase(
        skill_id="look_right",
        transcript="向右看",
        tool="dispatch_action",
        route="action",
    ),
    SkillCase(
        skill_id="look_up",
        transcript="向上看",
        tool="dispatch_action",
        route="action",
    ),
    SkillCase(
        skill_id="look_down",
        transcript="向下看",
        tool="dispatch_action",
        route="action",
    ),
    # ── RGB 灯光 ───────────────────────────────────────────────────────────
    SkillCase(
        skill_id="rgb_on",
        transcript="开灯",
        tool="dispatch_action",
        route="action",
    ),
    SkillCase(
        skill_id="rgb_off",
        transcript="关灯",
        tool="dispatch_action",
        route="action",
    ),
    # ── 观测工具 ───────────────────────────────────────────────────────────
    SkillCase(
        skill_id="front_distance",
        transcript="前方距离",
        tool="front_distance",
        route="observation",
    ),
    SkillCase(
        skill_id="camera_snapshot",
        transcript="拍照",
        tool="camera_snapshot",
        route="observation",
    ),
    SkillCase(
        skill_id="inspect_scene",
        transcript="前面有什么",
        tool="inspect_scene",
        route="observation",
    ),
]

# 按 skill_id 去重（保留第一条），便于唯一性校验
UNIQUE_SKILL_CASES: dict[str, SkillCase] = {}
for _c in SKILL_CASES:
    UNIQUE_SKILL_CASES.setdefault(_c.skill_id, _c)
