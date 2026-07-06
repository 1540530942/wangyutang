#!/usr/bin/env bash
# Run every module's test suite from its own directory (the required CWD for
# their intra-module imports) and archive the REAL pytest output under
# test_results/<module>/. Prints a final summary table.
#
# Usage:  bash scripts/run_all_module_tests.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv/bin/activate"
OUT="$ROOT/test_results"
STAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"
# shellcheck disable=SC1090
source "$VENV" 2>/dev/null

# module | test target (relative to module dir)
MODULES=(
  "action_move|tests/"
  "audio_interact|tests/"
  "audio_recognition|tests/"
  "camera_snapshot|tests/"
  "common_api_manager|tests/"
  "pi5_robot|tests/"
  "slam_mapping|tests/"
  "slam_mapping/2d_action|tests/"
  "smoke|."
  "pose_tracker|tests/"
  "simulation|tests/"
  "loop_engineering|tests/"
  "pi5_monitor|tests/"
  "smile_face|tests/"
  "robot_gateway|tests/"
  "common_sense|tests/"
)

summary=()
overall_ok=0

for entry in "${MODULES[@]}"; do
  mod="${entry%%|*}"
  target="${entry##*|}"
  safe="${mod//\//_}"
  dest="$OUT/$safe"
  mkdir -p "$dest"
  logfile="$dest/pytest_output.txt"

  {
    echo "# module : $mod"
    echo "# target : $target"
    echo "# run at : $STAMP"
    echo "# cwd    : $mod"
    echo "----------------------------------------------------------------------"
  } > "$logfile"

  ( cd "$ROOT/$mod" && python -m pytest "$target" -v 2>&1 ) >> "$logfile"
  rc=$?

  line="$(grep -E '[0-9]+ (passed|failed|error)' "$logfile" | tail -1)"
  [ -z "$line" ] && line="(no result line — see log)"
  if [ $rc -ne 0 ]; then overall_ok=1; verdict="FAIL"; else verdict="PASS"; fi
  summary+=("$(printf '%-26s %-6s %s' "$mod" "$verdict" "$line")")
done

echo ""
echo "==================== SUMMARY ($STAMP) ===================="
for s in "${summary[@]}"; do echo "$s"; done
echo "========================================================="
[ $overall_ok -eq 0 ] && echo "ALL MODULES PASS" || echo "SOME MODULES FAILED"
exit $overall_ok
