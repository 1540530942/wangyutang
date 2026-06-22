"""
捕获关机/重启命令的发起者。

策略（多重保障）：
  1. 监听 journald 的 shutdown 相关消息
  2. 解析 /var/log/auth.log 中的 sudo shutdown/reboot
  3. 持有 systemd inhibitor lock — Pi 关机前系统必须先释放此锁，
     我们在锁被释放的瞬间抓取当时的登录用户、正在运行的进程快照，
     并立刻上传到服务器。
  4. 注册 signal.SIGTERM 处理器（systemd stop 时会发 SIGTERM）
"""
import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

_SHUTDOWN_PATTERNS = [
    re.compile(r"(shutdown|reboot|halt|poweroff)", re.IGNORECASE),
    re.compile(r"systemctl\s+(poweroff|reboot|halt|shutdown)", re.IGNORECASE),
    re.compile(r"Reached target.*(?:shutdown|reboot|halt)", re.IGNORECASE),
    re.compile(r"System is going down", re.IGNORECASE),
]


def _snapshot_system_state() -> dict:
    """
    在即将关机时采集系统快照：当前登录用户、最近的 auth 日志、进程列表。
    必须在几秒内完成，因为系统正在关机。
    """
    snapshot = {"captured_at": datetime.utcnow().isoformat()}

    # who is logged in right now
    try:
        snapshot["who"] = subprocess.check_output(["who"], text=True, timeout=3).strip()
    except Exception as e:
        snapshot["who"] = f"error: {e}"

    # last logins
    try:
        snapshot["last"] = subprocess.check_output(
            ["last", "-n", "20"], text=True, timeout=3
        ).strip()
    except Exception as e:
        snapshot["last"] = f"error: {e}"

    # recent auth.log lines (last 50)
    try:
        with open("/var/log/auth.log") as f:
            lines = f.readlines()
            snapshot["auth_tail"] = "".join(lines[-50:])
    except Exception as e:
        snapshot["auth_tail"] = f"error: {e}"

    # running processes
    try:
        snapshot["processes"] = subprocess.check_output(
            ["ps", "aux", "--sort=-pcpu"], text=True, timeout=3
        ).strip()
    except Exception as e:
        snapshot["processes"] = f"error: {e}"

    # recent journald messages (last 5 minutes)
    try:
        snapshot["journal_recent"] = subprocess.check_output(
            ["journalctl", "-n", "100", "--no-pager", "-o", "short-iso"],
            text=True,
            timeout=5,
        ).strip()
    except Exception as e:
        snapshot["journal_recent"] = f"error: {e}"

    # try to identify who triggered shutdown via journald
    try:
        who_shutdown = subprocess.check_output(
            [
                "journalctl",
                "-n",
                "30",
                "--no-pager",
                "-o",
                "short-iso",
                "-t",
                "systemd-logind",
            ],
            text=True,
            timeout=3,
        ).strip()
        snapshot["logind_recent"] = who_shutdown
    except Exception as e:
        snapshot["logind_recent"] = f"error: {e}"

    # auditd: look for recent shutdown command executions
    try:
        audit_out = subprocess.check_output(
            ["ausearch", "-k", "pi5_shutdown", "--interpret", "-ts", "recent"],
            text=True,
            timeout=3,
        ).strip()
        snapshot["audit_shutdown"] = audit_out
    except Exception as e:
        snapshot["audit_shutdown"] = f"not available: {e}"

    return snapshot


class ShutdownCollector(threading.Thread):
    """
    监听 journald 流，检测关机相关条目，
    同时通过持有 systemd inhibitor lock 确保在关机前有机会运行最后的快照上传。
    """

    def __init__(
        self,
        on_event: Callable[[dict], None],
        on_imminent_shutdown: Callable[[dict], None],
    ):
        super().__init__(daemon=True, name="ShutdownCollector")
        self.on_event = on_event
        self.on_imminent_shutdown = on_imminent_shutdown
        self._stop_evt = threading.Event()
        self._inhibitor_fd: int | None = None

    def _acquire_inhibitor_lock(self):
        """
        向 systemd-logind 申请 inhibitor lock (delay 类型)。
        当系统收到 shutdown 请求时，会等所有 delay 锁被释放（最多 InhibitDelayMaxSec 秒）。
        我们在这段时间里上传快照，然后主动释放锁。
        """
        try:
            import dbus

            bus = dbus.SystemBus()
            manager = dbus.Interface(
                bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1"),
                "org.freedesktop.login1.Manager",
            )
            fd = manager.Inhibit(
                "shutdown:reboot",
                "pi5-monitor",
                "Capturing shutdown forensics before poweroff",
                "delay",
            )
            self._inhibitor_fd = fd.take()
        except Exception as e:
            # dbus 不可用时降级到无锁模式
            self._inhibitor_fd = None

    def _release_inhibitor_lock(self):
        if self._inhibitor_fd is not None:
            try:
                os.close(self._inhibitor_fd)
            except Exception:
                pass
            self._inhibitor_fd = None

    def _handle_sigterm(self, signum, frame):
        """systemd stop 发来 SIGTERM → 系统即将关机，立即采集快照。"""
        snapshot = _snapshot_system_state()
        event = {
            "type": "imminent_shutdown",
            "trigger": "SIGTERM",
            "collected_at": datetime.utcnow().isoformat(),
            "snapshot": snapshot,
        }
        self.on_imminent_shutdown(event)
        self._release_inhibitor_lock()

    def run(self):
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        self._acquire_inhibitor_lock()

        proc = subprocess.Popen(
            ["journalctl", "-f", "-n", "0", "-o", "short-iso"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            while not self._stop_evt.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                for pattern in _SHUTDOWN_PATTERNS:
                    if pattern.search(line):
                        event = {
                            "type": "shutdown_signal_detected",
                            "collected_at": datetime.utcnow().isoformat(),
                            "journal_line": line.strip(),
                        }
                        # 如果检测到 "System is going down" 这种最终消息，立即采集快照
                        if "going down" in line.lower() or "reached target" in line.lower():
                            snapshot = _snapshot_system_state()
                            event["snapshot"] = snapshot
                            self.on_imminent_shutdown(event)
                            self._release_inhibitor_lock()
                        else:
                            self.on_event(event)
                        break
        finally:
            proc.kill()
            self._release_inhibitor_lock()

    def stop(self):
        self._stop_evt.set()
