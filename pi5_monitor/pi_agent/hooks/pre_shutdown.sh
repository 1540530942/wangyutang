#!/bin/bash
# 由 systemd pi5-monitor-sentinel.service 的 ExecStop 调用。
# 在系统关机流程中被执行，负责收集关机前最后的系统状态并上传。
# 必须在 TimeoutStopSec 内完成（建议配置为 20s）。

set -euo pipefail

LOGFILE="/var/log/pi5-monitor/pre_shutdown.log"
SNAPSHOT_FILE="/tmp/pi5_shutdown_snapshot_$(date +%s).json"
SERVER="${PI5_MONITOR_SERVER:-http://110.40.154.41:8098}"
DEVICE_ID="${PI5_DEVICE_ID:-pi5-robot-01}"
API_KEY="${PI5_MONITOR_API_KEY:-changeme}"

log() { echo "[$(date -Is)] $*" | tee -a "$LOGFILE"; }

log "=== pre_shutdown.sh triggered ==="

# ── 1. 收集关机原因 ──────────────────────────────────────────────────
# systemd 在关机时会设置 WATCHDOG_USEC, MAINPID 等环境变量，
# 并可以通过 systemctl 查询关机原因。
SHUTDOWN_REASON="unknown"
if systemctl list-jobs 2>/dev/null | grep -q "shutdown.target\|reboot.target\|halt.target\|poweroff.target"; then
    SHUTDOWN_REASON=$(systemctl list-jobs 2>/dev/null | grep -E "shutdown|reboot|halt|poweroff" | head -1 || echo "unknown")
fi

# ── 2. 谁触发了关机 ─────────────────────────────────────────────────
WHO_SHUTDOWN="unknown"

# 方法A: journald logind 记录（最可靠）
WHO_SHUTDOWN_LOGIND=$(journalctl -n 50 --no-pager -o short-iso -t systemd-logind 2>/dev/null \
    | grep -iE "power|shutdown|reboot|halt" | tail -5 || echo "")

# 方法B: auditd 记录（需要 auditd 运行）
WHO_SHUTDOWN_AUDIT=$(ausearch -k pi5_shutdown --interpret -ts recent 2>/dev/null | tail -20 || echo "not available")

# 方法C: 最近的 sudo 命令
WHO_SHUTDOWN_SUDO=$(grep -E "sudo.*shutdown|sudo.*reboot|sudo.*halt|sudo.*poweroff|sudo.*systemctl.*(?:poweroff|reboot|halt)" \
    /var/log/auth.log 2>/dev/null | tail -10 || echo "")

# ── 3. 当前登录用户 ──────────────────────────────────────────────────
CURRENT_USERS=$(who 2>/dev/null || echo "")
LAST_LOGINS=$(last -n 30 2>/dev/null || echo "")

# ── 4. 最近 journald 100 条 ──────────────────────────────────────────
RECENT_JOURNAL=$(journalctl -n 100 --no-pager -o short-iso 2>/dev/null | tail -100 || echo "")

# ── 5. 进程列表 ──────────────────────────────────────────────────────
PROCESS_LIST=$(ps aux --sort=-pcpu 2>/dev/null | head -30 || echo "")

# ── 6. 组装 JSON 快照 ────────────────────────────────────────────────
python3 - <<PYEOF > "$SNAPSHOT_FILE"
import json, sys

data = {
    "type": "pre_shutdown_hook",
    "device_id": "${DEVICE_ID}",
    "captured_at": "$(date -Is)",
    "shutdown_reason": $(echo "${SHUTDOWN_REASON}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    "who_shutdown_logind": $(echo "${WHO_SHUTDOWN_LOGIND}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    "who_shutdown_audit": $(echo "${WHO_SHUTDOWN_AUDIT}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    "who_shutdown_sudo": $(echo "${WHO_SHUTDOWN_SUDO}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    "current_users": $(echo "${CURRENT_USERS}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    "last_logins": $(echo "${LAST_LOGINS}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    "recent_journal": $(echo "${RECENT_JOURNAL}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    "process_list": $(echo "${PROCESS_LIST}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")
}
print(json.dumps(data, ensure_ascii=False))
PYEOF

log "Snapshot written to $SNAPSHOT_FILE"

# ── 7. 上传到服务器（最多重试 3 次，每次 5 秒超时）────────────────────
for i in 1 2 3; do
    if curl -sf \
        -X POST "$SERVER/api/monitor/shutdown_snapshot" \
        -H "Content-Type: application/json" \
        -H "X-Device-ID: $DEVICE_ID" \
        -H "X-API-Key: $API_KEY" \
        --max-time 5 \
        -d @"$SNAPSHOT_FILE"; then
        log "Snapshot uploaded successfully (attempt $i)"
        exit 0
    fi
    log "Upload attempt $i failed, retrying..."
    sleep 2
done

log "All upload attempts failed. Snapshot saved locally at $SNAPSHOT_FILE"
exit 0  # 即使上传失败也要让关机继续
