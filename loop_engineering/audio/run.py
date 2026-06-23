"""CLI entry point for loop engineering sessions.

Examples:
  # 仅测试规划（无需机器人连接）
  python -m loop_engineering.audio.run

  # 仅测试 move 类技能
  python -m loop_engineering.audio.run --tags move

  # 在实车上执行（需要机器人在线）
  python -m loop_engineering.audio.run --mode execute_on_robot --tags move,basic

  # 保存结果
  python -m loop_engineering.audio.run --out results/run1.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from loop_engineering.audio.session import LoopSession

DEFAULT_CASES = Path(__file__).parent / "cases" / "baseline.json"
DEFAULT_FC_URL = "https://www.wangyutang.cn/function_center/api"


def main() -> int:
    parser = argparse.ArgumentParser(description="TurboPi Audio Loop Engineering")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="Case JSON 文件路径")
    parser.add_argument("--mode", choices=["plan_only", "simulate", "execute_on_robot"],
                        default="plan_only", help="运行模式")
    parser.add_argument("--fc-url", default=DEFAULT_FC_URL, help="fc_server API URL")
    parser.add_argument("--tags", help="按标签过滤（逗号分隔，如 move,basic）")
    parser.add_argument("--out", type=Path, help="结果输出文件路径（JSON）")
    args = parser.parse_args()

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None

    print(f"[loop-engineering/audio] mode={args.mode}  cases={args.cases.name}  tags={tags or 'all'}")

    if args.mode == "execute_on_robot":
        print(f"[loop-engineering/audio] fc_url={args.fc_url}")
        print("[loop-engineering/audio] 警告: 将向真实机器人下发指令，请确保场地安全！")

    session = LoopSession(
        cases_path=args.cases,
        mode=args.mode,
        fc_url=args.fc_url,
        tags=tags,
    )
    report = session.run()
    report.print_summary()

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        report.save(args.out)
        print(f"\n[loop-engineering/audio] 结果已保存到 {args.out}")

    return 0 if report.pass_rate >= 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
