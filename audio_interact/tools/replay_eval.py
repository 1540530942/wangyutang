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


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay and evaluate an audio_interact session package.")
    parser.add_argument("--session_dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None, help="Override eval_result.json output path.")
    parser.add_argument("--skip-validate", action="store_true")
    args = parser.parse_args()

    if not args.skip_validate:
        errors = validate_session(args.session_dir)
        if errors:
            print(json.dumps({"ok": False, "stage": "validate", "errors": errors}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

    replay_outputs = replay_session(args.session_dir)
    report = generate_report(args.session_dir, output_path=args.output)
    print(
        json.dumps(
            {
                "ok": True,
                "session_id": report.get("session_id"),
                "replay_chunks": sum(1 for item in replay_outputs if item.get("type") == "replay.chunk"),
                "eval_result": str(args.output or (args.session_dir / "reports" / "eval_result.json")),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

