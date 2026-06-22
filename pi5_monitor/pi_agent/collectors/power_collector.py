"""
监控树莓派的电源健康状态。
树莓派5 在电压不足、过温等情况下会在 dmesg / journald 中留下记录，
这些往往是自动关机的前兆。
"""
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

# 树莓派5 电压不足警告
_UNDER_VOLTAGE = re.compile(r"under.?voltage|low.?voltage|throttl", re.IGNORECASE)
# 过温
_OVER_TEMP = re.compile(r"over.?temp|thermal|temperature.*warning|cpu.*hot", re.IGNORECASE)
# OOM
_OOM = re.compile(r"Out of memory|OOM|killed process", re.IGNORECASE)
# 磁盘/文件系统错误（可能导致只读挂载后死机）
_DISK_ERR = re.compile(r"I/O error|filesystem error|ext4.error|mmcblk.*error", re.IGNORECASE)

_THROTTLE_BITS = {
    0: "under-voltage detected",
    1: "arm frequency capped",
    2: "currently throttled",
    3: "soft temperature limit active",
    16: "under-voltage has occurred",
    17: "arm frequency capping has occurred",
    18: "throttling has occurred",
    19: "soft temperature limit has occurred",
}


def _read_throttle_state() -> dict:
    """读取树莓派 vcgencmd 的节流状态。"""
    result = {"raw": None, "flags": [], "healthy": True}
    try:
        out = subprocess.check_output(
            ["vcgencmd", "get_throttled"], text=True, timeout=2
        ).strip()
        result["raw"] = out
        hex_val = int(out.split("=")[1], 16)
        for bit, desc in _THROTTLE_BITS.items():
            if hex_val & (1 << bit):
                result["flags"].append(desc)
        result["healthy"] = hex_val == 0
    except FileNotFoundError:
        result["raw"] = "vcgencmd not found (not on Pi?)"
    except Exception as e:
        result["raw"] = f"error: {e}"
    return result


def _read_cpu_temp() -> float | None:
    """读取 CPU 温度 (°C)。"""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None


class PowerCollector(threading.Thread):
    """
    每隔 POLL_INTERVAL 秒轮询一次电源健康状态，
    同时监听 journald 中的电源/温度相关告警。
    """

    POLL_INTERVAL = 30  # 秒

    def __init__(self, on_event: Callable[[dict], None]):
        super().__init__(daemon=True, name="PowerCollector")
        self.on_event = on_event
        self._stop_evt = threading.Event()
        self._last_throttle_raw: str | None = None

    def _check_power(self):
        throttle = _read_throttle_state()
        cpu_temp = _read_cpu_temp()
        event = {
            "type": "power_status",
            "collected_at": datetime.utcnow().isoformat(),
            "throttle": throttle,
            "cpu_temp_c": cpu_temp,
            "is_problem": not throttle["healthy"] or (cpu_temp is not None and cpu_temp > 80),
        }
        # 只在状态发生变化或有问题时上报
        if event["is_problem"] or throttle["raw"] != self._last_throttle_raw:
            self._last_throttle_raw = throttle["raw"]
            self.on_event(event)

    def _monitor_journal(self):
        """监听 journald 中的电源/温度/OOM/磁盘错误。"""
        proc = subprocess.Popen(
            ["journalctl", "-f", "-n", "0", "-o", "short-iso", "-k"],  # -k = kernel messages
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            while not self._stop_evt.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                for pattern, event_type in [
                    (_UNDER_VOLTAGE, "power_undervoltage"),
                    (_OVER_TEMP, "power_overtemp"),
                    (_OOM, "oom_kill"),
                    (_DISK_ERR, "disk_error"),
                ]:
                    if pattern.search(line):
                        self.on_event(
                            {
                                "type": event_type,
                                "collected_at": datetime.utcnow().isoformat(),
                                "journal_line": line.strip(),
                                "is_problem": True,
                            }
                        )
                        break
        finally:
            proc.kill()

    def run(self):
        journal_thread = threading.Thread(
            target=self._monitor_journal, daemon=True, name="PowerJournalMonitor"
        )
        journal_thread.start()

        while not self._stop_evt.is_set():
            self._check_power()
            self._stop_evt.wait(self.POLL_INTERVAL)

    def stop(self):
        self._stop_evt.set()
