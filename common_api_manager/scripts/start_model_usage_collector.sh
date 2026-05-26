#!/usr/bin/env bash
set -euo pipefail

LOG_DIR=/data/models/logs
PID_FILE="$LOG_DIR/model_usage_collector.pid"
SCRIPT="$LOG_DIR/model_usage_collector.py"

mkdir -p "$LOG_DIR"

if [[ -s "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "model_usage_collector already running: $(cat "$PID_FILE")"
  exit 0
fi

nohup python3 "$SCRIPT" \
  --host 127.0.0.1 \
  --port 18080 \
  --log-dir "$LOG_DIR" \
  >> "$LOG_DIR/model_usage_collector.stdout.log" \
  2>> "$LOG_DIR/model_usage_collector.stderr.log" &

echo "$!" > "$PID_FILE"
echo "model_usage_collector started: $(cat "$PID_FILE")"
