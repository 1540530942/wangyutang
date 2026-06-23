"""Run a complete loop engineering session: load cases → run → report → suggest."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .case import CaseResult, LoopCase, RunMode
from .observer import RobotObserver
from .runner import LoopRunner, PlannerMode


def load_cases(path: Path) -> list[LoopCase]:
    """从 JSON 文件加载 LoopCase 列表。

    支持字段: id, transcript, expected_skill_id, expected_route,
              obs_check (可选), tags (可选)
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        LoopCase(
            id=c["id"],
            transcript=c["transcript"],
            expected_skill_id=c.get("expected_skill_id", ""),
            expected_route=c.get("expected_route", "action"),
            obs_check=c.get("obs_check", {}),
            tags=c.get("tags", []),
        )
        for c in raw
    ]


@dataclass
class SessionReport:
    session_id: str
    mode: RunMode
    total: int
    passed: int
    failed: int
    avg_score: float
    avg_planning_latency_ms: float
    results: list[CaseResult]
    failures: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def print_summary(self) -> None:
        w = 70
        print("=" * w)
        print(f"  Loop Engineering Session  [{self.session_id}]  mode={self.mode}")
        print("=" * w)
        print(f"  Total: {self.total}  Passed: {self.passed}  Failed: {self.failed}")
        print(f"  Pass rate: {self.pass_rate:.1%}  Avg score: {self.avg_score:.3f}")
        print(f"  Avg planning latency: {self.avg_planning_latency_ms:.0f}ms")
        if self.failures:
            print(f"\n  Failures ({len(self.failures)}):")
            for f in self.failures:
                print(f"    [{f['id']}] {f['transcript']!r}")
                for n in f.get("notes", []):
                    print(f"       {n}")
        if self.suggestions:
            print(f"\n  Suggestions:")
            for s in self.suggestions:
                print(f"    → {s}")
        print("=" * w)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pass_rate"] = self.pass_rate
        return d

    def save(self, out_path: Path) -> None:
        out_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _build_suggestions(failures: list[dict[str, Any]]) -> list[str]:
    """根据失败案例生成改进建议（规则式，不调 LLM）。"""
    suggestions: list[str] = []
    route_mismatch = [f for f in failures if "路由期望" in " ".join(f.get("notes", []))]
    skill_mismatch = [f for f in failures if "技能期望" in " ".join(f.get("notes", []))]
    exec_fail = [f for f in failures if "执行失败" in " ".join(f.get("notes", []))]
    obs_fail = [f for f in failures if "观测未通过" in " ".join(f.get("notes", []))]

    if route_mismatch:
        ids = [f["id"] for f in route_mismatch]
        suggestions.append(f"路由类型识别错误 ({len(route_mismatch)} 条): {ids[:3]}… 检查 LLM 规划 prompt 或 skill_catalog 的 route 字段")
    if skill_mismatch:
        ids = [f["id"] for f in skill_mismatch]
        suggestions.append(f"技能 ID 映射错误 ({len(skill_mismatch)} 条): {ids[:3]}… 为这些文本添加 aliases 或检查 react 规划 prompt")
    if exec_fail:
        suggestions.append(f"执行失败 ({len(exec_fail)} 条)：检查 fc_server 连通性和 edge_ros_controller 状态")
    if obs_fail:
        suggestions.append(f"观测未通过 ({len(obs_fail)} 条)：机器人实际移动量与期望不符，考虑调整 move_duration_ms_at_5cm 参数")
    return suggestions


class LoopSession:
    """一个完整的循环工程会话。

    planner       : "rule"（无 LLM，测试别名覆盖）或 "llm"（需 router_config）
    router_config : 规划器 LLM 配置（仅 planner="llm" 时需要）
    cloud_config  : 云端任务配置（execute_on_robot 时可选）
    """

    def __init__(
        self,
        cases_path: Path,
        mode: RunMode = "plan_only",
        planner: PlannerMode = "rule",
        fc_url: str = "https://www.wangyutang.cn/function_center/api",
        router_config: dict[str, Any] | None = None,
        cloud_config: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.cases_path = cases_path
        self.mode = mode
        self.planner = planner
        self.fc_url = fc_url
        self.router_config = router_config
        self.cloud_config = cloud_config
        self.filter_tags = tags

    def run(self) -> SessionReport:
        cases = load_cases(self.cases_path)
        if self.filter_tags:
            cases = [c for c in cases if any(t in c.tags for t in self.filter_tags)]

        observer = RobotObserver(self.fc_url) if self.mode == "execute_on_robot" else None
        runner = LoopRunner(
            fc_url=self.fc_url, observer=observer,
            planner=self.planner, router_config=self.router_config,
            cloud_config=self.cloud_config,
        )

        session_id = time.strftime("%Y%m%d_%H%M%S")
        results = runner.run_batch(cases, self.mode)

        passed = sum(1 for r in results if r.passed)
        avg_score = sum(r.score for r in results) / len(results) if results else 0.0
        avg_lat = sum(r.planning_latency_ms for r in results) / len(results) if results else 0.0

        failures = [
            {
                "id": r.case_id,
                "transcript": r.transcript,
                "planned_skill_id": r.planned_skill_id,
                "planned_route": r.planned_route,
                "score": r.score,
                "notes": r.notes,
            }
            for r in results if not r.passed
        ]
        suggestions = _build_suggestions(failures)

        return SessionReport(
            session_id=session_id,
            mode=self.mode,
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            avg_score=round(avg_score, 3),
            avg_planning_latency_ms=round(avg_lat, 1),
            results=results,
            failures=failures,
            suggestions=suggestions,
        )
