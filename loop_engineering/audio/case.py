"""Loop engineering case definitions for the audio recognition module."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RouteKind = Literal["action", "face", "observation", "none"]
RunMode = Literal["plan_only", "simulate", "execute_on_robot"]


@dataclass
class LoopCase:
    """A single test case for loop engineering.

    transcript    : 语音识别后的文本（跳过 STT，直接测试规划）
    expected_skill_id : 期望路由到的技能 ID（空字符串表示期望无动作）
    expected_route    : 期望的路由类型
    obs_check     : 期望的机器人状态变化（可选，用于实车执行模式）
                    支持：{"sonar_delta_cm": ..., "min_sonar_cm": ...}
    tags          : 用于分组的标签（如 "move", "look", "alias", "negative"）
    """
    id: str
    transcript: str
    expected_skill_id: str
    expected_route: RouteKind = "action"
    obs_check: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass
class CaseResult:
    """执行一个 LoopCase 的完整结果。"""
    case_id: str
    mode: RunMode
    transcript: str

    # 规划阶段
    planned_skill_id: str = ""
    planned_route: str = ""
    planning_ok: bool = False       # 路由是否符合期望
    planning_latency_ms: float = 0.0

    # 执行阶段（execute_on_robot 模式）
    action_dispatched: bool = False
    action_ok: bool = False
    action_error: str = ""
    execution_latency_ms: float = 0.0

    # 观测阶段（execute_on_robot 模式）
    pre_sonar_cm: float = -1.0
    post_sonar_cm: float = -1.0
    sonar_delta_cm: float = 0.0
    obs_passed: bool | None = None  # None 表示无观测标准

    # 综合评分
    passed: bool = False
    score: float = 0.0              # 0.0-1.0
    notes: list[str] = field(default_factory=list)
