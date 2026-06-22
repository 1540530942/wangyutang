"""
每隔 HEARTBEAT_INTERVAL 秒向服务器发送一次心跳。
心跳包含：时间戳、系统运行时间、内存使用、CPU 温度、网络接口状态、
当前登录用户。
服务器如果超过 HEARTBEAT_TIMEOUT 秒未收到心跳，就认为 Pi 已失联。
"""
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Callable

HEARTBEAT_INTERVAL = 15   # 秒：心跳间隔
HEARTBEAT_TIMEOUT = 60    # 服务器侧超时阈值（写在这里仅供参考，服务器自行配置）


def _uptime_seconds() -> float:
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return -1.0


def _mem_usage_mb() -> dict:
    result = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:", "MemFree:"):
                    result[parts[0].rstrip(":")] = int(parts[1]) // 1024  # KB→MB
    except Exception:
        pass
    return result


def _cpu_temp() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None


def _active_users() -> list[str]:
    try:
        out = subprocess.check_output(["who", "-q"], text=True, timeout=2)
        users = out.split("\n")[0].split()
        return users
    except Exception:
        return []


def _net_interfaces() -> dict[str, str]:
    result = {}
    try:
        out = subprocess.check_output(["ip", "-br", "link"], text=True, timeout=2)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                result[parts[0].split("@")[0]] = parts[1]
    except Exception:
        pass
    return result


def build_heartbeat(device_id: str) -> dict:
    return {
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_s": _uptime_seconds(),
        "memory_mb": _mem_usage_mb(),
        "cpu_temp_c": _cpu_temp(),
        "active_users": _active_users(),
        "net_interfaces": _net_interfaces(),
    }


class HeartbeatSender(threading.Thread):
    def __init__(self, device_id: str, send_fn: Callable[[dict], bool]):
        super().__init__(daemon=True, name="HeartbeatSender")
        self.device_id = device_id
        self.send_fn = send_fn
        self._stop_evt = threading.Event()

    def run(self):
        while not self._stop_evt.is_set():
            hb = build_heartbeat(self.device_id)
            self.send_fn(hb)
            self._stop_evt.wait(HEARTBEAT_INTERVAL)

    def stop(self):
        self._stop_evt.set()
