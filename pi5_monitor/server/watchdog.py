"""
心跳看门狗。
定期检查每台设备的最后心跳时间，超过阈值就开启 offline incident。
设备恢复后自动关闭 incident 并触发分析。
"""
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger("watchdog")

HEARTBEAT_TIMEOUT = 90    # 秒：超过此时间无心跳则判定为失联
CHECK_INTERVAL = 20       # 秒：看门狗轮询间隔


class DeviceState:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.online = True
        self.incident_id: int | None = None
        self.last_heartbeat_ts: float | None = None


class Watchdog(threading.Thread):
    """
    看门狗线程。
    on_offline(device_id, incident_id)  — Pi 刚下线时调用
    on_online(device_id, incident_id)   — Pi 恢复上线时调用
    """

    def __init__(
        self,
        db,
        on_offline: Callable[[str, int], None],
        on_online: Callable[[str, int, str], None],
    ):
        super().__init__(daemon=True, name="Watchdog")
        self.db = db
        self.on_offline = on_offline
        self.on_online = on_online
        self._stop_evt = threading.Event()
        self._states: dict[str, DeviceState] = {}
        self._lock = threading.Lock()

    def register_device(self, device_id: str):
        with self._lock:
            if device_id not in self._states:
                self._states[device_id] = DeviceState(device_id)

    def update_heartbeat(self, device_id: str, ts: float):
        """Pi 发来心跳时调用，更新最后在线时间。"""
        with self._lock:
            state = self._states.setdefault(device_id, DeviceState(device_id))
            was_offline = not state.online
            state.last_heartbeat_ts = ts
            state.online = True

            if was_offline and state.incident_id is not None:
                incident_id = state.incident_id
                state.incident_id = None
                # 在锁外通知（避免死锁）
                threading.Thread(
                    target=self._handle_recovery,
                    args=(device_id, incident_id),
                    daemon=True,
                ).start()

    def _handle_recovery(self, device_id: str, incident_id: int):
        cause = self._analyze_cause(device_id, incident_id)
        self.db.close_incident(incident_id, cause)
        self.on_online(device_id, incident_id, cause)
        logger.info("Device %s back online. Incident #%d closed. Cause: %s",
                    device_id, incident_id, cause)

    def _analyze_cause(self, device_id: str, incident_id: int) -> str:
        """
        快速启发式分析：查看 shutdown_snapshots 和最近的事件，
        给出一个人类可读的原因摘要。
        详细分析交给 analyzer.py。
        """
        from analyzer.analyzer import quick_analyze
        try:
            return quick_analyze(self.db, device_id)
        except Exception as e:
            logger.warning("quick_analyze failed: %s", e)
            return "analysis pending"

    def run(self):
        logger.info("Watchdog started. timeout=%ds check_interval=%ds",
                    HEARTBEAT_TIMEOUT, CHECK_INTERVAL)
        while not self._stop_evt.is_set():
            now = time.time()
            with self._lock:
                states_snapshot = list(self._states.values())

            for state in states_snapshot:
                if state.last_heartbeat_ts is None:
                    continue
                age = now - state.last_heartbeat_ts
                if age > HEARTBEAT_TIMEOUT and state.online:
                    with self._lock:
                        state.online = False
                    incident_id = self.db.open_incident(
                        state.device_id, state.last_heartbeat_ts
                    )
                    with self._lock:
                        state.incident_id = incident_id
                    logger.warning(
                        "Device %s OFFLINE — last heartbeat %.0fs ago. Incident #%d opened.",
                        state.device_id, age, incident_id,
                    )
                    self.on_offline(state.device_id, incident_id)

            self._stop_evt.wait(CHECK_INTERVAL)

    def stop(self):
        self._stop_evt.set()
