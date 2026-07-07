"""Execute a LoopCase against the audio recognition pipeline.

三种模式：
  plan_only         — 只走规划器，不真正下发机器人指令
  simulate          — 规划 + 模拟执行（无真实机器人）
  execute_on_robot  — 完整链路：规划 → 通过 fc_server 真实执行 → 观测

规划器类型：
  rule  — 纯规则+别名匹配（无需 LLM，适合测试 alias 覆盖度）
  llm   — 完整 LLM ReAct 链路（需要 router_config 带 LLM 端点和 token）
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Literal

from .case import CaseResult, LoopCase, RunMode
from .evaluator import apply_obs, evaluate
from .observer import RobotObserver

PlannerMode = Literal["rule", "llm"]

# 路径到 robot_sandbox 包根目录
_AUDIO_PKG = Path(__file__).resolve().parents[2] / "robot_sandbox"
_CATALOG = _AUDIO_PKG.parent / "action_move" / "skill_catalog.json"
if not _CATALOG.exists():
    _CATALOG = _AUDIO_PKG / "skills" / "skill_catalog.json"


def _route_rule(transcript: str) -> tuple[str, str, float]:
    """规则+别名匹配，无需 LLM。返回 (skill_id, route, latency_ms)。"""
    import sys
    pkg_parent = str(_AUDIO_PKG.parent)
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)

    from robot_sandbox.legacy.planner import RuleBasedTaskPlanner
    from robot_sandbox.skills.face_router import is_face_skill

    t0 = time.monotonic()
    planner = RuleBasedTaskPlanner(_CATALOG)
    task = planner.plan(transcript)
    latency_ms = (time.monotonic() - t0) * 1000.0

    if task is None:
        return "", "none", latency_ms
    route = "face" if is_face_skill(task.skill_id) else ("action" if task.skill_id else "none")
    return task.skill_id, route, latency_ms


def _route_llm(
    transcript: str,
    router_config: dict[str, Any] | None,
    cloud_config: dict[str, Any] | None,
) -> tuple[str, str, float]:
    """LLM ReAct 规划，需要 router_config 含 react_agent.llm 配置。"""
    import sys
    pkg_parent = str(_AUDIO_PKG.parent)
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)

    from robot_sandbox.harness.react_loop import route_transcript

    t0 = time.monotonic()
    result = route_transcript(
        base_dir=_AUDIO_PKG,
        text=transcript,
        router_config=router_config,
        cloud_config=cloud_config,
        route_action=False,     # plan_only: 不真正下发云端任务
        source="loop_engineering",
    )
    latency_ms = (time.monotonic() - t0) * 1000.0

    plan = result.get("plan") or {}
    skill_id = plan.get("skill_id", "") or result.get("skill_id", "")
    route = plan.get("route") or ("none" if not skill_id else "action")
    return skill_id, route, latency_ms


def _route(
    transcript: str,
    planner: PlannerMode,
    router_config: dict[str, Any] | None = None,
    cloud_config: dict[str, Any] | None = None,
) -> tuple[str, str, float]:
    """统一路由接口，根据 planner 类型分发。"""
    if planner == "rule":
        return _route_rule(transcript)
    return _route_llm(transcript, router_config, cloud_config)


def _dispatch_to_robot(skill_id: str, fc_url: str, timeout: int = 15) -> tuple[bool, str, float]:
    """通过 fc_server 将技能下发到实车，返回 (ok, error, latency_ms)。

    将技能 ID 映射到 drive_start 或 edge_execute 指令。
    对于 move_* / turn_* 技能，调用 /api/drive_start 然后等待后调用 /api/drive_stop。
    """
    DRIVE_SKILLS = {
        "move_forward", "move_backward", "move_left", "move_right",
        "turn_left", "turn_right",
    }
    base = fc_url.rstrip("/")

    def post(path: str, data: dict) -> dict:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            base + path, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    t0 = time.monotonic()
    try:
        if skill_id in DRIVE_SKILLS:
            d = post("/drive_start", {"skill_id": skill_id, "sensitivity": 1.0})
            time.sleep(1.2)   # 执行约 1.2s
            post("/drive_stop", {})
        elif skill_id in {"look_left", "look_right", "look_up", "look_down", "reset_pose"}:
            # 云台技能通过 edge_ros_controller 执行（通过 /stop 触发 emergency_stop 效果不佳，
            # 这里直接向 edge_ros_controller POST）
            edge_url = "http://raspberrypi:8765" if "127.0.0.1" in base or "raspberrypi" in base else None
            if edge_url:
                body = json.dumps({"action": skill_id, "settings": {}}).encode()
                req = urllib.request.Request(
                    edge_url + "/execute", data=body,
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    d = json.loads(r.read())
            else:
                return False, "look_*/reset_pose 需要在机器人本地网络执行", 0.0
        elif skill_id == "emergency_stop":
            d = post("/stop", {})
        else:
            return False, f"未知技能: {skill_id!r}", 0.0
        latency_ms = (time.monotonic() - t0) * 1000.0
        ok = bool(d.get("ok"))
        return ok, d.get("error", ""), latency_ms
    except urllib.error.URLError as e:
        return False, f"fc_server 不可达: {e}", 0.0
    except Exception as e:
        return False, str(e), 0.0


class LoopRunner:
    """执行单个或批量 LoopCase。

    fc_url       : fc_server API 基础 URL（含 /api）
    observer     : RobotObserver 实例，None 时在需要时自动创建
    planner      : "rule"（无 LLM，测试别名覆盖）或 "llm"（需 router_config）
    router_config: LLM 规划器配置（仅 planner="llm" 时需要）
    cloud_config : 云端任务下发配置（execute_on_robot 时可选）
    """

    def __init__(
        self,
        fc_url: str = "https://www.wangyutang.cn/function_center/api",
        observer: RobotObserver | None = None,
        planner: PlannerMode = "rule",
        router_config: dict[str, Any] | None = None,
        cloud_config: dict[str, Any] | None = None,
    ) -> None:
        self.fc_url = fc_url
        self.observer = observer
        self.planner: PlannerMode = planner
        self.router_config = router_config
        self.cloud_config = cloud_config

    def run_case(self, case: LoopCase, mode: RunMode = "plan_only") -> CaseResult:
        result = CaseResult(
            case_id=case.id,
            mode=mode,
            transcript=case.transcript,
        )

        # ── 规划 ────────────────────────────────────────────────────────────
        try:
            skill_id, route, lat = _route(
                case.transcript, self.planner, self.router_config, self.cloud_config
            )
        except Exception as e:
            result.notes.append(f"✗ 规划异常: {e}")
            result.passed = False
            return evaluate(case, result)

        result.planned_skill_id = skill_id
        result.planned_route = route
        result.planning_latency_ms = round(lat, 1)

        if mode == "plan_only":
            return evaluate(case, result)

        # ── 实车执行 ─────────────────────────────────────────────────────────
        if mode == "execute_on_robot" and case.expected_skill_id:
            # 执行前观测
            if case.obs_check:
                result.pre_sonar_cm = self.observer.sonar()

            ok, err, lat_exec = _dispatch_to_robot(
                skill_id, self.fc_url.rstrip("/").removesuffix("/api")
            )
            result.action_dispatched = True
            result.action_ok = ok
            result.action_error = err
            result.execution_latency_ms = round(lat_exec, 1)

            # 执行后观测
            if case.obs_check:
                time.sleep(0.5)  # 等待动作稳定
                result.post_sonar_cm = self.observer.sonar()
                apply_obs(case, result)

        return evaluate(case, result)

    def run_batch(self, cases: list[LoopCase], mode: RunMode = "plan_only") -> list[CaseResult]:
        results = []
        for case in cases:
            r = self.run_case(case, mode)
            results.append(r)
        return results
