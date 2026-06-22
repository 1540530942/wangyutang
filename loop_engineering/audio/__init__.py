"""Loop engineering framework for the audio recognition module.

Usage (plan_only mode, no robot needed):

    from pathlib import Path
    from loop_engineering.audio.session import LoopSession

    session = LoopSession(
        cases_path=Path("loop_engineering/audio/cases/baseline.json"),
        mode="plan_only",
    )
    report = session.run()
    report.print_summary()
    report.save(Path("loop_engineering/audio/results/latest.json"))

Usage (execute_on_robot mode, real robot required):

    session = LoopSession(
        cases_path=Path("loop_engineering/audio/cases/baseline.json"),
        mode="execute_on_robot",
        fc_url="https://www.wangyutang.cn/function_center/api",
        tags=["move", "basic"],   # 仅执行移动基础测试
    )
    report = session.run()
    report.print_summary()
"""
from .case import CaseResult, LoopCase, RunMode
from .evaluator import evaluate
from .observer import RobotObserver, RobotState
from .runner import LoopRunner
from .session import LoopSession, SessionReport, load_cases

__all__ = [
    "CaseResult", "LoopCase", "RunMode",
    "evaluate",
    "RobotObserver", "RobotState",
    "LoopRunner",
    "LoopSession", "SessionReport", "load_cases",
]
