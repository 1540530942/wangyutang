"""Real, deterministic tests for the loop_engineering audio evaluator.

Run from the loop_engineering/ directory:  python -m pytest tests/ -q
The evaluator is pure: it scores a CaseResult against a LoopCase. Scores are
verified by the documented weights (plan 60% / exec 30% / obs 10%).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio.case import CaseResult, LoopCase  # noqa: E402
from audio.evaluator import apply_obs, evaluate  # noqa: E402


def _case(**kw):
    base = dict(id="c1", transcript="前进", expected_skill_id="move_forward",
                expected_route="action")
    base.update(kw)
    return LoopCase(**base)


def _result(**kw):
    base = dict(case_id="c1", mode="plan_only", transcript="前进")
    base.update(kw)
    return CaseResult(**base)


# ── planning score (60%) ──────────────────────────────────────────────────

def test_correct_plan_only_scores_full():
    """plan_only: planning 0.6 + exec-skip 0.3 + no-obs 0.1 = 1.0."""
    case = _case()
    res = _result(planned_skill_id="move_forward", planned_route="action")
    evaluate(case, res)
    assert res.planning_ok is True
    assert res.passed is True
    assert res.score == 1.0


def test_wrong_route_fails_planning():
    case = _case()
    res = _result(planned_skill_id="move_forward", planned_route="face")
    evaluate(case, res)
    assert res.planning_ok is False
    assert res.passed is False
    assert any("路由期望" in n for n in res.notes)


def test_wrong_skill_fails_planning():
    case = _case()
    res = _result(planned_skill_id="turn_left", planned_route="action")
    evaluate(case, res)
    assert res.planning_ok is False
    assert any("技能期望" in n for n in res.notes)


def test_expected_no_action_passes_with_empty_skill():
    """When a case expects no action (empty skill id), an empty planned skill
    on the 'none' route is correct."""
    case = _case(expected_skill_id="", expected_route="none")
    res = _result(planned_skill_id="", planned_route="none")
    evaluate(case, res)
    assert res.planning_ok is True
    assert res.passed is True


# ── execution score (30%, execute_on_robot only) ──────────────────────────

def test_execute_on_robot_success_adds_execution_score():
    case = _case()
    res = _result(mode="execute_on_robot", planned_skill_id="move_forward",
                  planned_route="action", action_dispatched=True, action_ok=True)
    evaluate(case, res)
    # 0.6 plan + 0.3 exec + 0.1 no-obs
    assert res.score == 1.0
    assert any("执行成功" in n for n in res.notes)


def test_execute_on_robot_dispatch_failure_partial_score():
    case = _case()
    res = _result(mode="execute_on_robot", planned_skill_id="move_forward",
                  planned_route="action", action_dispatched=True, action_ok=False,
                  action_error="timeout")
    evaluate(case, res)
    # 0.6 plan + 0.1 partial exec + 0.1 no-obs
    assert res.score == 0.8
    assert any("执行失败" in n for n in res.notes)


def test_execute_on_robot_not_dispatched_no_execution_score():
    case = _case()
    res = _result(mode="execute_on_robot", planned_skill_id="move_forward",
                  planned_route="action", action_dispatched=False)
    evaluate(case, res)
    # 0.6 plan + 0.0 exec + 0.1 no-obs
    assert res.score == 0.7
    assert any("未下发" in n for n in res.notes)


# ── observation score (10%) via apply_obs + evaluate ──────────────────────

def test_apply_obs_computes_delta_and_pass():
    """sonar goes 50 -> 40 (delta -10). Expect -10 within tolerance -> pass."""
    case = _case(obs_check={"sonar_delta_cm": -10.0, "tolerance_cm": 3.0})
    res = _result(mode="execute_on_robot", planned_skill_id="move_forward",
                  planned_route="action", action_dispatched=True, action_ok=True,
                  pre_sonar_cm=50.0, post_sonar_cm=40.0)
    apply_obs(case, res)
    assert res.sonar_delta_cm == -10.0
    assert res.obs_passed is True
    evaluate(case, res)
    assert res.score == 1.0
    assert res.passed is True


def test_apply_obs_out_of_tolerance_fails_obs():
    """Expected delta -10 but actual -2 (|−2−(−10)|=8 > tol 3) -> obs fail."""
    case = _case(obs_check={"sonar_delta_cm": -10.0, "tolerance_cm": 3.0})
    res = _result(mode="execute_on_robot", planned_skill_id="move_forward",
                  planned_route="action", action_dispatched=True, action_ok=True,
                  pre_sonar_cm=50.0, post_sonar_cm=48.0)
    apply_obs(case, res)
    assert res.obs_passed is False
    evaluate(case, res)
    # passed requires obs_passed is not False -> must be False overall
    assert res.passed is False


def test_apply_obs_no_check_sets_none():
    case = _case(obs_check={})
    res = _result(pre_sonar_cm=50.0, post_sonar_cm=40.0)
    apply_obs(case, res)
    assert res.obs_passed is None


def test_min_sonar_floor_fails_when_too_close():
    case = _case(obs_check={"min_sonar_cm": 15.0})
    res = _result(mode="execute_on_robot", planned_skill_id="move_forward",
                  planned_route="action", action_dispatched=True, action_ok=True,
                  pre_sonar_cm=30.0, post_sonar_cm=10.0)
    apply_obs(case, res)
    assert res.obs_passed is False
