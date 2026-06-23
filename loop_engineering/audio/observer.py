"""Observe real robot state via fc_server (https://www.wangyutang.cn/function_center/api)."""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any


@dataclass
class RobotState:
    reachable: bool
    container_ok: bool
    edge_ok: bool
    sonar_cm: float = -1.0        # -1 表示测量失败
    raw: dict[str, Any] | None = None


class RobotObserver:
    """通过 fc_server API 读取机器人真实状态。

    fc_url: fc_server 基础 URL，不带尾斜杠
    默认指向本地 Pi（内网用 raspberrypi:8088）或通过 Caddy（公网用 wangyutang.cn）
    """

    def __init__(self, fc_url: str = "https://www.wangyutang.cn/function_center/api", timeout: int = 6) -> None:
        self.fc_url = fc_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(f"{self.fc_url}{path}", timeout=self.timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            return {"_error": str(e)}

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def sonar(self) -> float:
        """返回超声波距离(cm)，失败时返回 -1.0。"""
        d = self._get("/sonar")
        if d.get("ok") and d.get("distance_cm", -1) > 0:
            return float(d["distance_cm"])
        return -1.0

    def snapshot(self) -> RobotState:
        """取一次完整快照（健康 + 超声波）。"""
        h = self.health()
        return RobotState(
            reachable=not bool(h.get("_error")),
            container_ok=h.get("container") == "running",
            edge_ok=bool(h.get("edge_ok")),
            sonar_cm=self.sonar(),
            raw=h,
        )
