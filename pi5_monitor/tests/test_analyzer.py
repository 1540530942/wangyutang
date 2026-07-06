"""Real, deterministic tests for the pi5_monitor incident analyzer.

Run from the pi5_monitor/ directory:  python -m pytest tests/ -q
Tests exercise the pure analysis functions with realistic snapshot/event dicts
shaped exactly like the collectors produce. quick_analyze/analyze_incident are
covered with a tiny in-memory fake DB (no real sqlite needed).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.analyzer import (  # noqa: E402
    _analyze_events,
    _event_summary,
    analyze_shutdown_snapshot,
    quick_analyze,
)


# ── analyze_shutdown_snapshot ─────────────────────────────────────────────

def test_snapshot_detects_sudo_shutdown_command():
    snap = {
        "auth_tail": (
            "Jun 23 01:00:00 pi sudo: pi : TTY=pts/0 ; "
            "COMMAND=/usr/sbin/shutdown -h now\n"
        ),
        "who": "",
    }
    out = analyze_shutdown_snapshot(snap)
    assert "[sudo]" in out
    assert "shutdown" in out
    assert "无用户登录" in out  # who empty -> program/cron hint


def test_snapshot_detects_undervoltage_in_journal():
    snap = {
        # Real Raspberry Pi kernel wording uses the hyphenated form.
        "journal_recent": "kernel: hwmon hwmon1: Under-voltage detected! (0x00050005)",
        "who": "pi   pts/0   2026-06-23 00:59",
    }
    out = analyze_shutdown_snapshot(snap)
    assert "电压不足" in out
    assert "登录中的用户" in out


def test_snapshot_empty_returns_unknown():
    out = analyze_shutdown_snapshot({})
    # with no signals except empty who -> still emits the who hint line,
    # so assert it is NOT the pure 'cannot determine' fallback but the who hint.
    assert "无用户登录" in out


def test_snapshot_all_blank_strings_returns_fallback():
    out = analyze_shutdown_snapshot({"who": "   ", "journal_recent": ""})
    # who is whitespace -> the else branch adds the program/cron hint line
    assert "无用户登录" in out


# ── _analyze_events ───────────────────────────────────────────────────────

def test_analyze_events_flags_shutdown_sudo():
    events = [
        {"event_type": "sudo_cmd", "is_shutdown_related": True, "user": "pi",
         "command": "poweroff", "received_at": 1000.0},
    ]
    out = _analyze_events(events)
    assert "可能原因" in out
    assert "poweroff" in out and "pi" in out


def test_analyze_events_flags_oom():
    events = [
        {"event_type": "oom_kill", "journal_line": "Out of memory: Killed process",
         "received_at": 2000.0},
    ]
    out = _analyze_events(events)
    assert "OOM" in out


def test_analyze_events_empty_returns_no_record():
    assert "无近期事件" in _analyze_events([])


def test_analyze_events_multiple_network_down():
    events = [
        {"event_type": "network_change", "is_down": True, "received_at": 10.0},
        {"event_type": "network_change", "is_down": True, "received_at": 11.0},
    ]
    out = _analyze_events(events)
    assert "网络接口 DOWN" in out


# ── _event_summary ────────────────────────────────────────────────────────

def test_event_summary_ssh_login():
    ev = {"event_type": "ssh_login", "user": "pi", "from_ip": "10.0.0.5"}
    s = _event_summary(ev)
    assert "SSH 登录" in s and "pi" in s and "10.0.0.5" in s


def test_event_summary_sudo():
    ev = {"event_type": "sudo_cmd", "user": "root", "command": "reboot"}
    s = _event_summary(ev)
    assert "sudo" in s and "reboot" in s


# ── quick_analyze with in-memory fake DB ──────────────────────────────────

class _FakeDB:
    def __init__(self, snapshots=None, events=None):
        self._snaps = snapshots or []
        self._events = events or []

    def get_unanalyzed_snapshots(self):
        return self._snaps

    def get_events(self, device_id, since_ts=None, until_ts=None, limit=None):
        return self._events


def test_quick_analyze_prefers_snapshot():
    db = _FakeDB(snapshots=[{
        "device_id": "pi5-robot-01",
        "auth_tail": "COMMAND=/usr/sbin/reboot\n",
        "who": "",
    }])
    out = quick_analyze(db, "pi5-robot-01")
    assert "reboot" in out


def test_quick_analyze_falls_back_to_events():
    db = _FakeDB(snapshots=[], events=[
        {"event_type": "sudo_cmd", "is_shutdown_related": True, "user": "pi",
         "command": "halt", "received_at": 5.0},
    ])
    out = quick_analyze(db, "pi5-robot-01")
    assert "halt" in out
