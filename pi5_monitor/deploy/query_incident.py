#!/usr/bin/env python3
"""
命令行工具：查询失联事件报告。
用法:
  python3 query_incident.py                           # 列出所有失联事件
  python3 query_incident.py --incident 5              # 查看事件 #5 的完整报告
  python3 query_incident.py --events --since 1h       # 查看最近 1 小时的事件流
  python3 query_incident.py --devices                 # 查看所有设备在线状态
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

SERVER = os.environ.get("PI5_MONITOR_SERVER", "http://110.40.154.41:8098")
API_KEY = os.environ.get("PI5_MONITOR_API_KEY", "changeme")
DEVICE_ID = os.environ.get("PI5_DEVICE_ID", "pi5-robot-01")

HEADERS = {"X-API-Key": API_KEY, "X-Device-ID": DEVICE_ID}


def _ts(unix_ts: float | None) -> str:
    if unix_ts is None:
        return "—"
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_duration(s: str) -> float:
    """'1h', '30m', '1d' → seconds"""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return float(s[:-1]) * units.get(s[-1], 1)


def cmd_devices(args):
    resp = requests.get(f"{SERVER}/api/monitor/devices", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    devices = resp.json()["devices"]
    if not devices:
        print("没有已注册的设备。")
        return
    print(f"{'设备ID':<20} {'状态':<8} {'最后心跳':<28} {'距今(秒)':<12}")
    print("-" * 70)
    for d in devices:
        status = "🟢 在线" if d["online"] else "🔴 离线"
        age = f"{d['last_heartbeat_age_s']:.0f}" if d["last_heartbeat_age_s"] else "—"
        print(f"{d['device_id']:<20} {status:<8} {d['last_heartbeat'] or '—':<28} {age:<12}")


def cmd_incidents(args):
    resp = requests.get(
        f"{SERVER}/api/monitor/incidents",
        headers=HEADERS,
        params={"device_id": DEVICE_ID},
        timeout=10,
    )
    resp.raise_for_status()
    incidents = resp.json()["incidents"]
    if not incidents:
        print("无失联记录。")
        return
    print(f"{'ID':<6} {'检测时间':<28} {'时长(秒)':<10} {'已解决':<8} {'原因摘要'}")
    print("-" * 100)
    for inc in incidents:
        resolved = "✓" if inc["resolved"] else "✗"
        duration = f"{inc['duration_s']:.0f}" if inc.get("duration_s") else "—"
        cause = (inc.get("cause_summary") or "—")[:60]
        print(f"#{inc['id']:<5} {_ts(inc['detected_at']):<28} {duration:<10} {resolved:<8} {cause}")


def cmd_incident_report(args):
    resp = requests.get(
        f"{SERVER}/api/monitor/incidents/{args.incident}/report",
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    report = resp.json()

    print(f"\n{'='*60}")
    print(f"失联事件 #{report['incident_id']} 分析报告")
    print(f"{'='*60}")
    print(f"设备:         {report['device_id']}")
    print(f"检测时间:     {report['detected_at']}")
    print(f"最后心跳:     {report['last_heartbeat']}")
    print(f"恢复时间:     {report['came_back_at']}")
    print(f"失联时长:     {report['duration_s']:.0f}秒" if report.get("duration_s") else "失联时长: 未恢复")
    print(f"已解决:       {'是' if report['resolved'] else '否'}")
    print(f"\n{'─'*60}")
    print("原因分析:")
    print(report.get("cause_summary", "—"))

    if report.get("shutdown_snapshot_analysis"):
        print(f"\n{'─'*60}")
        print("关机前快照分析:")
        print(report["shutdown_snapshot_analysis"])

    if report.get("pre_offline_timeline"):
        print(f"\n{'─'*60}")
        print("失联前事件时间线:")
        for ev in report["pre_offline_timeline"]:
            print(f"  [{ev['time']}] {ev['type']:<30} {ev['summary']}")
    print()


def cmd_events(args):
    since = time.time() - parse_duration(args.since) if args.since else None
    resp = requests.get(
        f"{SERVER}/api/monitor/events/{DEVICE_ID}",
        headers=HEADERS,
        params={"since": since, "limit": args.limit, "types": args.types},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    events = data["events"]
    print(f"共 {data['count']} 条事件\n")
    for ev in reversed(events):
        ts = _ts(ev.get("received_at"))
        etype = ev.get("event_type", "unknown")
        print(f"[{ts}] {etype:<30} {_summarize(ev)}")


def _summarize(ev: dict) -> str:
    t = ev.get("event_type", "")
    if t == "ssh_login":
        return f"user={ev.get('user')} from={ev.get('from_ip')}"
    if t == "ssh_disconnect":
        return f"from={ev.get('from_ip')}"
    if t == "sudo_cmd":
        return f"user={ev.get('user')} cmd={ev.get('command','')[:60]}"
    if t == "network_change":
        return f"{ev.get('interface')} {ev.get('old_state')}→{ev.get('new_state')}"
    if t == "power_status":
        return f"temp={ev.get('cpu_temp_c')}°C flags={ev.get('throttle',{}).get('flags')}"
    return str(ev)[:80]


def main():
    global SERVER, API_KEY, DEVICE_ID, HEADERS
    parser = argparse.ArgumentParser(description="Pi5 Monitor 查询工具")
    parser.add_argument("--server", default=SERVER)
    parser.add_argument("--api-key", default=API_KEY)
    parser.add_argument("--device", default=DEVICE_ID)

    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("devices", help="列出所有设备状态")
    sub.add_parser("incidents", help="列出失联事件")

    rp = sub.add_parser("report", help="查看失联事件完整报告")
    rp.add_argument("--incident", "-i", type=int, required=True)

    ep = sub.add_parser("events", help="查看事件流")
    ep.add_argument("--since", "-s", default="1h", help="时间范围，如 1h/30m/1d")
    ep.add_argument("--limit", "-n", type=int, default=100)
    ep.add_argument("--types", "-t", default=None, help="逗号分隔的事件类型")

    args = parser.parse_args()
    SERVER = args.server
    API_KEY = args.api_key
    DEVICE_ID = args.device
    HEADERS = {"X-API-Key": API_KEY, "X-Device-ID": DEVICE_ID}

    if args.cmd == "devices":
        cmd_devices(args)
    elif args.cmd == "incidents":
        cmd_incidents(args)
    elif args.cmd == "report":
        cmd_incident_report(args)
    elif args.cmd == "events":
        cmd_events(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
