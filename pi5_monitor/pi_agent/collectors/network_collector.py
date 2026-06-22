"""
监控网络接口状态变化。
通过 `ip monitor link` 实时捕获网卡 UP/DOWN 事件，
同时每 10 秒轮询一次当前所有接口的状态作为兜底。
"""
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

_LINK_CHANGE = re.compile(r"\d+:\s+(\S+):.*state\s+(\S+)", re.IGNORECASE)
_IGNORED_IFACES = {"lo"}


def _get_iface_states() -> dict[str, str]:
    """返回 {iface: state} 字典，state 为 UP / DOWN / UNKNOWN 等。"""
    result = {}
    try:
        out = subprocess.check_output(["ip", "-br", "link"], text=True)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                iface = parts[0].split("@")[0]
                state = parts[1]
                if iface not in _IGNORED_IFACES:
                    result[iface] = state
    except Exception:
        pass
    return result


class NetworkCollector(threading.Thread):
    """
    双轨监控网络：
      1. ip monitor link — 实时事件流
      2. 轮询 ip -br link — 兜底确认
    """

    POLL_INTERVAL = 10  # 秒

    def __init__(self, on_event: Callable[[dict], None]):
        super().__init__(daemon=True, name="NetworkCollector")
        self.on_event = on_event
        self._stop_evt = threading.Event()
        self._last_states: dict[str, str] = {}

    def _emit(self, iface: str, old_state: str | None, new_state: str):
        event = {
            "type": "network_change",
            "collected_at": datetime.utcnow().isoformat(),
            "interface": iface,
            "old_state": old_state,
            "new_state": new_state,
            "is_down": new_state.upper() in ("DOWN", "UNKNOWN", "LOWERLAYERDOWN"),
        }
        self.on_event(event)

    def _monitor_stream(self):
        """在子线程中运行 ip monitor link。"""
        proc = subprocess.Popen(
            ["ip", "monitor", "link"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            while not self._stop_evt.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                m = _LINK_CHANGE.search(line)
                if m:
                    iface, new_state = m.group(1), m.group(2).upper()
                    if iface not in _IGNORED_IFACES:
                        old = self._last_states.get(iface)
                        if old != new_state:
                            self._last_states[iface] = new_state
                            self._emit(iface, old, new_state)
        finally:
            proc.kill()

    def run(self):
        self._last_states = _get_iface_states()

        stream_thread = threading.Thread(
            target=self._monitor_stream, daemon=True, name="ip-monitor"
        )
        stream_thread.start()

        while not self._stop_evt.is_set():
            time.sleep(self.POLL_INTERVAL)
            current = _get_iface_states()
            for iface, state in current.items():
                old = self._last_states.get(iface)
                if old != state:
                    self._last_states[iface] = state
                    self._emit(iface, old, state)
            # 检测消失的接口
            for iface in list(self._last_states):
                if iface not in current:
                    old = self._last_states.pop(iface)
                    self._emit(iface, old, "REMOVED")

    def stop(self):
        self._stop_evt.set()
