from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.report_generator import generate_report  # noqa: E402
from replay.replay_session import replay_session  # noqa: E402
from tools.validate_session import validate_session  # noqa: E402


def run_regression(cases_dir: Path) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for manifest in sorted(cases_dir.glob("**/manifest.json")):
        session_dir = manifest.parent
        errors = validate_session(session_dir)
        if errors:
            results.append({"session_dir": str(session_dir), "ok": False, "stage": "validate", "errors": errors})
            continue
        replay_session(session_dir)
        report = generate_report(session_dir)
        results.append({"session_dir": str(session_dir), "ok": True, "session_id": report.get("session_id")})
    return {"ok": all(item.get("ok") for item in results), "count": len(results), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run replay/eval over a directory of standard session packages.")
    parser.add_argument("cases_dir", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = run_regression(args.cases_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

