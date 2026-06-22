"""
失联原因分析器。

analyze_shutdown_snapshot(snapshot)  — 从关机前快照提取关键信息
quick_analyze(db, device_id)         — 快速启发式分析（设备刚恢复时用）
analyze_incident(db, incident_id)    — 完整事件报告（供 API 调用）

分析策略（优先级从高到低）：
  1. shutdown_snapshot 中的 audit / logind / sudo 记录 → 最直接
  2. 关机前 5 分钟内的 sudo_cmd 事件（is_shutdown_related=True）
  3. 关机前 5 分钟内的 ssh_login / ssh_disconnect 事件
  4. power_undervoltage / power_overtemp 事件
  5. oom_kill 事件
  6. network_change（所有接口 DOWN）
  7. 兜底：时间线 + "原因不明"
"""
import json
import re
import time
from datetime import datetime, timezone

# 正则：从 sudo 记录里提取关机命令
_SUDO_SHUTDOWN = re.compile(
    r"COMMAND=.*?(shutdown|reboot|halt|poweroff|systemctl.*(?:poweroff|reboot|halt))",
    re.IGNORECASE,
)
# 正则：从 logind 记录里找是谁要求关机
_LOGIND_POWER = re.compile(
    r"(?:Power|Shutdown|Reboot|Halt).*?(?:requested|initiated).*?(?:by|from)\s+(\S+)",
    re.IGNORECASE,
)
# 正则：auditd 记录中的 auid（实际触发用户）
_AUDIT_AUID = re.compile(r"auid=(\d+)|acct=(\S+)", re.IGNORECASE)
_AUDIT_CMD = re.compile(r"a0=\"?(\S+)\"?", re.IGNORECASE)


def _ts_str(unix_ts: float) -> str:
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ─────────────────────────────────────────────────────────────────────
# 关机快照分析
# ─────────────────────────────────────────────────────────────────────

def analyze_shutdown_snapshot(snapshot: dict) -> str:
    """
    从 pre_shutdown 快照中提取关键线索，返回人类可读摘要。
    """
    lines = []

    # 方法1: auditd 记录（最权威）
    audit = snapshot.get("audit_shutdown") or snapshot.get("who_shutdown_audit", "")
    if audit and "not available" not in audit and audit.strip():
        m_auid = _AUDIT_AUID.search(audit)
        m_cmd = _AUDIT_CMD.search(audit)
        if m_auid or m_cmd:
            user = m_auid.group(1) or m_auid.group(2) if m_auid else "unknown"
            cmd = m_cmd.group(1) if m_cmd else "unknown command"
            lines.append(f"[auditd] 用户 uid={user} 执行了命令: {cmd}")

    # 方法2: systemd-logind 记录
    logind = snapshot.get("logind_recent") or snapshot.get("who_shutdown_logind", "")
    if logind and logind.strip():
        for line in logind.splitlines():
            m = _LOGIND_POWER.search(line)
            if m:
                lines.append(f"[logind] {line.strip()}")
                break

    # 方法3: sudo + shutdown 关键词
    auth_tail = snapshot.get("auth_tail", "")
    sudo_shutdowns = [
        ln.strip()
        for ln in auth_tail.splitlines()
        if _SUDO_SHUTDOWN.search(ln)
    ]
    if sudo_shutdowns:
        lines.append(f"[sudo] 最近的关机相关 sudo 命令:")
        lines.extend(f"  {l}" for l in sudo_shutdowns[-3:])

    # 方法4: 当前登录用户
    who = snapshot.get("who", "")
    if who.strip():
        lines.append(f"[who] 关机时登录中的用户: {who.strip()}")
    else:
        lines.append("[who] 关机时无用户登录（可能是程序/计划任务触发）")

    # 方法5: 最近 journal 中有无 OOM/undervoltage
    journal = snapshot.get("journal_recent") or snapshot.get("recent_journal", "")
    for keyword, label in [
        ("under-voltage", "电压不足 (undervoltage)"),
        ("Out of memory", "内存耗尽 (OOM)"),
        ("thermal", "过热 (thermal)"),
        ("filesystem error", "文件系统错误"),
        ("I/O error", "I/O 错误"),
    ]:
        if keyword.lower() in journal.lower():
            lines.append(f"[journal] 检测到: {label}")

    if not lines:
        return "无法从快照中确定关机原因，需要人工分析日志。"

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# 快速启发式分析（设备刚恢复时）
# ─────────────────────────────────────────────────────────────────────

def quick_analyze(db, device_id: str) -> str:
    """
    查看最近的事件，做快速启发式判断。
    优先查找 shutdown_snapshot，其次查事件流。
    """
    # 查最近一个未分析的 shutdown_snapshot
    snapshots = db.get_unanalyzed_snapshots()
    for snap in snapshots:
        if snap.get("device_id") == device_id:
            return analyze_shutdown_snapshot(snap)

    # 查最近 10 分钟的事件
    since = time.time() - 600
    events = db.get_events(device_id, since_ts=since, limit=200)
    return _analyze_events(events)


def _analyze_events(events: list[dict]) -> str:
    if not events:
        return "无近期事件记录，无法确定原因。"

    # 按时间升序排列
    events_sorted = sorted(events, key=lambda e: e.get("received_at", 0))

    causes = []

    # 找关机相关 sudo
    for ev in events_sorted:
        if ev.get("event_type") == "sudo_cmd" and ev.get("is_shutdown_related"):
            user = ev.get("user", "unknown")
            cmd = ev.get("command", "unknown")
            ts = _ts_str(ev["received_at"])
            causes.append(f"[{ts}] 用户 '{user}' 通过 sudo 执行了: {cmd}")

    # 找电源问题
    for ev in events_sorted:
        if ev.get("event_type") in ("power_undervoltage", "power_overtemp"):
            ts = _ts_str(ev["received_at"])
            causes.append(f"[{ts}] 检测到电源问题: {ev.get('event_type')}")

    # 找 OOM
    for ev in events_sorted:
        if ev.get("event_type") == "oom_kill":
            ts = _ts_str(ev["received_at"])
            causes.append(f"[{ts}] 检测到 OOM: {ev.get('journal_line', '')}")

    # 找网络全断
    down_events = [e for e in events_sorted
                   if e.get("event_type") == "network_change" and e.get("is_down")]
    if down_events and len(down_events) >= 2:
        ts = _ts_str(down_events[-1]["received_at"])
        causes.append(f"[{ts}] 检测到多个网络接口 DOWN（可能是断网）")

    if causes:
        return "可能原因:\n" + "\n".join(causes)
    return "无法从事件流确定关机原因。"


# ─────────────────────────────────────────────────────────────────────
# 完整 Incident 报告（供 API 返回）
# ─────────────────────────────────────────────────────────────────────

def analyze_incident(db, incident_id: int) -> dict:
    """生成一份完整的失联事件报告。"""
    incidents = db.get_incidents("*", limit=1000)
    incident = next((i for i in incidents if i["id"] == incident_id), None)
    if not incident:
        return {"error": f"Incident #{incident_id} not found"}

    device_id = incident["device_id"]
    detected_at = incident["detected_at"]
    last_hb = incident.get("last_heartbeat")

    # 查失联前 10 分钟的事件
    window_start = (last_hb or detected_at) - 600
    window_end = detected_at + 60
    events = db.get_events(device_id, since_ts=window_start, until_ts=window_end, limit=500)
    events_sorted = sorted(events, key=lambda e: e.get("received_at", 0))

    # 查 shutdown snapshot
    snapshots = [
        s for s in db.get_unanalyzed_snapshots()
        if s.get("device_id") == device_id
        and abs(s.get("received_at", 0) - detected_at) < 300
    ]
    snapshot_analysis = analyze_shutdown_snapshot(snapshots[0]) if snapshots else None

    # 时间线
    timeline = []
    for ev in events_sorted:
        timeline.append(
            {
                "time": _ts_str(ev["received_at"]),
                "type": ev.get("event_type", "unknown"),
                "summary": _event_summary(ev),
            }
        )

    cause = incident.get("cause_summary") or _analyze_events(events)

    return {
        "incident_id": incident_id,
        "device_id": device_id,
        "detected_at": _ts_str(detected_at),
        "last_heartbeat": _ts_str(last_hb) if last_hb else None,
        "came_back_at": _ts_str(incident["came_back_at"]) if incident.get("came_back_at") else None,
        "duration_s": incident.get("duration_s"),
        "resolved": bool(incident.get("resolved")),
        "cause_summary": cause,
        "shutdown_snapshot_analysis": snapshot_analysis,
        "pre_offline_timeline": timeline,
    }


def _event_summary(ev: dict) -> str:
    t = ev.get("event_type", "")
    if t == "ssh_login":
        return f"SSH 登录: 用户={ev.get('user')} 来自={ev.get('from_ip')}"
    if t == "ssh_disconnect":
        return f"SSH 断开: 来自={ev.get('from_ip')}"
    if t == "sudo_cmd":
        return f"sudo: 用户={ev.get('user')} 命令={ev.get('command')}"
    if t == "network_change":
        return f"网络变化: {ev.get('interface')} {ev.get('old_state')}→{ev.get('new_state')}"
    if t == "power_status":
        flags = ev.get("throttle", {}).get("flags", [])
        temp = ev.get("cpu_temp_c")
        return f"电源状态: temp={temp}°C flags={flags}"
    if t in ("power_undervoltage", "power_overtemp"):
        return f"电源警告: {ev.get('journal_line', '')}"
    if t == "oom_kill":
        return f"OOM: {ev.get('journal_line', '')}"
    if t in ("shutdown_signal_detected", "imminent_shutdown"):
        return f"关机信号: {ev.get('journal_line', ev.get('trigger', ''))}"
    return json.dumps(ev, ensure_ascii=False)[:120]
