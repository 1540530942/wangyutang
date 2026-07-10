"""全链路 session 追踪工具:给定 session_id(或 --list)输出可复盘的时间线。

覆盖两种落盘布局:
  标准包  <root>/sessions/<YYYY-MM-DD>/<session_id>/   (manifest + events/*.jsonl)
  legacy  <root>/recordings/<YYYY-MM-DD>/<session_id>/ (full.wav + session.json)

用法:
  python tools/trace_session.py --data-root /app/data --list [--limit 20]
  python tools/trace_session.py --data-root /app/data --session <id 或 id 前缀>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.event_logger import read_jsonl  # noqa: E402

EVENT_FILES = [
    "runtime_events.jsonl",
    "vad_runtime.jsonl",
    "asr_runtime.jsonl",
    "tts_runtime.jsonl",
    "bargein_runtime.jsonl",
]


def find_sessions(data_root: Path) -> list[dict[str, Any]]:
    """扫描标准包与 legacy 目录,返回按时间倒序的 session 摘要列表。"""
    found: dict[str, dict[str, Any]] = {}
    for pkg_dir in sorted(data_root.glob("sessions/*/*/")):
        manifest_path = pkg_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        session_id = str(manifest.get("session_id") or pkg_dir.name)
        found[session_id] = {
            "session_id": session_id,
            "day": pkg_dir.parent.name,
            "package_dir": pkg_dir,
            "legacy_dir": None,
            "manifest": manifest,
        }
    for rec_dir in sorted(data_root.glob("recordings/*/*/")):
        session_path = rec_dir / "session.json"
        if not session_path.exists():
            continue
        try:
            meta = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        session_id = str(meta.get("session_id") or rec_dir.name)
        entry = found.setdefault(
            session_id,
            {"session_id": session_id, "day": rec_dir.parent.name, "package_dir": None, "manifest": None, "legacy_dir": None},
        )
        entry["legacy_dir"] = rec_dir
        entry["legacy_meta"] = meta
    return sorted(found.values(), key=lambda item: (str(item["day"]), item["session_id"]), reverse=True)


def summarize(entry: dict[str, Any]) -> str:
    manifest = entry.get("manifest") or {}
    legacy = entry.get("legacy_meta") or {}
    duration_ms = int(manifest.get("duration_ms") or round(float(legacy.get("duration_seconds") or 0) * 1000))
    utterances = legacy.get("utterances") or []
    texts = "; ".join(str(item.get("text") or "") for item in utterances if item.get("text")) or "-"
    source = manifest.get("source") or ("legacy" if entry.get("legacy_dir") else "?")
    capture = manifest.get("capture_point") or "?"
    return (
        f"{entry['day']}  {entry['session_id']:<24} {duration_ms/1000:6.1f}s"
        f"  句数={len(utterances):<3} 来源={source:<17} 采集点={capture:<17} 文本: {texts[:60]}"
    )


def load_events(package_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str, str]] = set()
    for filename in EVENT_FILES:
        for event in read_jsonl(package_dir / "events" / filename):
            key = (int(event.get("ts_ms", 0)), str(event.get("type", "")), str(event.get("segment_id", "")), str(event.get("turn_id", "")))
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    events.sort(key=lambda item: (int(item.get("ts_ms", 0)), str(item.get("type", ""))))
    return events


def format_event(event: dict[str, Any]) -> str:
    ts = int(event.get("ts_ms", 0))
    etype = str(event.get("type", "?"))
    detail_keys = ("segment_id", "turn_id", "tts_id", "text", "wake_status", "status", "reason", "source", "start_ms", "end_ms", "audio_start_ms", "audio_end_ms")
    details = " ".join(
        f"{key}={event[key]}" for key in detail_keys if event.get(key) not in (None, "", [])
    )
    command = event.get("command")
    if isinstance(command, dict):
        skill = command.get("skill_id") or (command.get("plan") or {}).get("skill_id") or ""
        details += f" skill={skill}" if skill else " command=<dict>"
    return f"  {ts:>8}ms  {etype:<32} {details}"


def trace_one(entry: dict[str, Any]) -> None:
    print(f"=== Session {entry['session_id']} ({entry['day']}) ===")
    manifest = entry.get("manifest")
    package_dir: Path | None = entry.get("package_dir")
    legacy_dir: Path | None = entry.get("legacy_dir")
    if manifest:
        print(
            f"来源={manifest.get('source')}  设备={manifest.get('device_id')}  时长={manifest.get('duration_ms')}ms"
            f"  采集点={manifest.get('capture_point', '?')}  proc_same_as_raw={manifest.get('proc_same_as_raw', '?')}"
        )
    if package_dir:
        print(f"标准包: {package_dir}")
        for wav in sorted((package_dir / "audio").glob("*.wav")):
            print(f"  音频: {wav.name} ({wav.stat().st_size} bytes)")
    if legacy_dir:
        print(f"legacy: {legacy_dir}")
        full = legacy_dir / "full.wav"
        if full.exists():
            print(f"  音频: full.wav ({full.stat().st_size} bytes)")
    if package_dir:
        events = load_events(package_dir)
        print(f"--- 事件时间线({len(events)} 条) ---")
        for event in events:
            print(format_event(event))
    legacy_meta = entry.get("legacy_meta") or {}
    utterances = legacy_meta.get("utterances") or []
    if utterances and not package_dir:
        print(f"--- utterances({len(utterances)} 句,legacy) ---")
        for item in utterances:
            print(
                f"  [{item.get('index')}] {item.get('vad_start_seconds')}s~{item.get('vad_end_seconds')}s"
                f" 文本='{item.get('text')}' 唤醒={item.get('wake_status')} 技能={item.get('skill_id')} 状态={item.get('status')}"
            )
    reports = (package_dir / "reports" / "eval_result.json") if package_dir else None
    if reports and reports.exists():
        print(f"评测报告: {reports}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace an audio_interact session end to end.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--session", type=str, default="", help="session_id 或其前缀")
    parser.add_argument("--list", action="store_true", help="列出最近 session 摘要")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    entries = find_sessions(args.data_root)
    if args.list or not args.session:
        for entry in entries[: args.limit]:
            print(summarize(entry))
        if not entries:
            print("(未找到任何 session)")
        return 0

    matches = [e for e in entries if e["session_id"] == args.session] or [
        e for e in entries if e["session_id"].startswith(args.session)
    ]
    if not matches:
        print(f"未找到 session: {args.session}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"匹配到 {len(matches)} 个,请给更长前缀:", file=sys.stderr)
        for entry in matches[:10]:
            print(f"  {entry['day']} {entry['session_id']}", file=sys.stderr)
        return 1
    trace_one(matches[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
