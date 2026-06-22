"""
监控 /var/log/auth.log，捕获：
  - SSH 登录 / 登出（谁在什么 IP 何时连入/断开）
  - sudo 命令（谁用 sudo 执行了什么）
  - su 切换（谁切换到哪个用户）
"""
import re
import subprocess
import threading
from datetime import datetime
from typing import Callable

# 匹配 SSH 登录
_SSH_ACCEPTED = re.compile(
    r"(\w+\s+\d+\s[\d:]+)\s+\S+\s+sshd\[\d+\]:\s+Accepted\s+\w+\s+for\s+(\S+)\s+from\s+([\d.:a-fA-F]+)\s+port\s+(\d+)"
)
# 匹配 SSH 登出 / 连接关闭
_SSH_DISCONNECT = re.compile(
    r"(\w+\s+\d+\s[\d:]+)\s+\S+\s+sshd\[\d+\]:\s+(?:Received disconnect|Disconnected from|Connection closed)\s+.*?(?:from\s+([\d.:a-fA-F]+))?"
)
# 匹配 sudo
_SUDO_CMD = re.compile(
    r"(\w+\s+\d+\s[\d:]+)\s+\S+\s+sudo\s*:\s+(\S+)\s+:.*?COMMAND=(.*)"
)
# 匹配 shutdown / reboot / halt 被 sudo 执行
_SHUTDOWN_KEYWORDS = {"shutdown", "reboot", "halt", "poweroff", "init", "systemctl"}


def parse_line(line: str) -> dict | None:
    """解析单行 auth.log，返回结构化事件，无法识别返回 None。"""
    m = _SSH_ACCEPTED.search(line)
    if m:
        return {
            "type": "ssh_login",
            "time_str": m.group(1),
            "user": m.group(2),
            "from_ip": m.group(3),
            "port": int(m.group(4)),
            "raw": line.strip(),
        }

    m = _SSH_DISCONNECT.search(line)
    if m:
        return {
            "type": "ssh_disconnect",
            "time_str": m.group(1),
            "from_ip": m.group(2) or "unknown",
            "raw": line.strip(),
        }

    m = _SUDO_CMD.search(line)
    if m:
        cmd = m.group(3).strip()
        is_dangerous = any(k in cmd.lower() for k in _SHUTDOWN_KEYWORDS)
        return {
            "type": "sudo_cmd",
            "time_str": m.group(1),
            "user": m.group(2),
            "command": cmd,
            "is_shutdown_related": is_dangerous,
            "raw": line.strip(),
        }

    return None


class AuthLogCollector(threading.Thread):
    """
    持续 tail /var/log/auth.log，每解析到一条事件就调用 on_event 回调。
    使用 subprocess + tail -F 保证日志轮转后自动跟踪。
    """

    AUTH_LOG = "/var/log/auth.log"

    def __init__(self, on_event: Callable[[dict], None]):
        super().__init__(daemon=True, name="AuthLogCollector")
        self.on_event = on_event
        self._stop_evt = threading.Event()

    def run(self):
        proc = subprocess.Popen(
            ["tail", "-F", "-n", "0", self.AUTH_LOG],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            while not self._stop_evt.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                event = parse_line(line)
                if event:
                    event["collected_at"] = datetime.utcnow().isoformat()
                    self.on_event(event)
        finally:
            proc.kill()

    def stop(self):
        self._stop_evt.set()
