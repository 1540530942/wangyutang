"""Score a CaseResult against its LoopCase expectations."""
from __future__ import annotations

from .case import CaseResult, LoopCase


def evaluate(case: LoopCase, result: CaseResult) -> CaseResult:
    """填写 result.passed, result.score, result.notes。就地修改并返回。"""
    notes: list[str] = []
    score = 0.0

    # 1. 规划是否正确（权重 60%）
    route_ok = result.planned_route == case.expected_route
    skill_ok = (
        result.planned_skill_id == case.expected_skill_id
        if case.expected_skill_id
        else result.planned_skill_id == ""
    )
    planning_ok = route_ok and skill_ok
    result.planning_ok = planning_ok

    if planning_ok:
        score += 0.6
        notes.append("✓ 规划正确")
    else:
        if not route_ok:
            notes.append(f"✗ 路由期望 {case.expected_route!r}，实际 {result.planned_route!r}")
        if not skill_ok:
            notes.append(f"✗ 技能期望 {case.expected_skill_id!r}，实际 {result.planned_skill_id!r}")

    # 2. 执行是否成功（权重 30%，仅 execute_on_robot 模式）
    if result.mode == "execute_on_robot":
        if case.expected_skill_id:  # 有动作期望
            if result.action_dispatched and result.action_ok:
                score += 0.3
                notes.append("✓ 执行成功")
            elif result.action_dispatched:
                score += 0.1
                notes.append(f"⚠ 执行失败: {result.action_error}")
            else:
                notes.append("✗ 指令未下发")
        else:
            score += 0.3  # 期望无动作，执行阶段不扣分
            notes.append("✓ 无动作（符合期望）")
    else:
        score += 0.3  # plan_only/simulate 跳过执行评分

    # 3. 机器人观测是否符合期望（权重 10%）
    if result.obs_passed is None:
        score += 0.1  # 无观测标准，不扣分
    elif result.obs_passed:
        score += 0.1
        notes.append(f"✓ 观测通过（声纳 Δ={result.sonar_delta_cm:+.1f}cm）")
    else:
        obs = case.obs_check
        notes.append(
            f"✗ 观测未通过：期望 {obs}，"
            f"前={result.pre_sonar_cm:.1f}cm 后={result.post_sonar_cm:.1f}cm"
        )

    result.score = round(score, 3)
    result.passed = planning_ok and (result.obs_passed is not False)
    result.notes = notes
    return result


def _check_sonar_obs(obs_check: dict, pre_cm: float, post_cm: float, delta_cm: float) -> bool:
    """检查观测标准是否满足。"""
    if "sonar_delta_cm" in obs_check:
        expected = float(obs_check["sonar_delta_cm"])
        tolerance = float(obs_check.get("tolerance_cm", 3.0))
        if abs(delta_cm - expected) > tolerance:
            return False
    if "min_sonar_cm" in obs_check:
        if post_cm < float(obs_check["min_sonar_cm"]):
            return False
    return True


def apply_obs(case: LoopCase, result: CaseResult) -> None:
    """根据观测数据填写 result.sonar_delta_cm 和 result.obs_passed。"""
    if result.pre_sonar_cm > 0 and result.post_sonar_cm > 0:
        result.sonar_delta_cm = round(result.post_sonar_cm - result.pre_sonar_cm, 1)
    if not case.obs_check:
        result.obs_passed = None
        return
    result.obs_passed = _check_sonar_obs(
        case.obs_check, result.pre_sonar_cm, result.post_sonar_cm, result.sonar_delta_cm
    )
