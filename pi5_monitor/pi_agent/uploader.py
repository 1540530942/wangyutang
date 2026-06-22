"""
将本地缓存的事件批量上传到监控服务器。
心跳和事件走不同接口，心跳优先级更高。
"""
import logging
import threading
import time
from datetime import datetime

import requests

from local_db import LocalDB

logger = logging.getLogger("uploader")

UPLOAD_INTERVAL = 5   # 秒：正常上传间隔
RETRY_INTERVAL = 15   # 秒：失败后重试间隔
BATCH_SIZE = 50       # 每次最多上传多少条事件


class Uploader(threading.Thread):
    """
    后台线程，循环将 LocalDB 中未上传的事件 POST 到服务器。
    网络断开时不崩溃，等待重试。
    """

    def __init__(self, server_url: str, device_id: str, api_key: str, db: LocalDB):
        super().__init__(daemon=True, name="Uploader")
        self.server_url = server_url.rstrip("/")
        self.device_id = device_id
        self.api_key = api_key
        self.db = db
        self._stop_evt = threading.Event()
        self._session = requests.Session()
        self._session.headers.update(
            {"X-Device-ID": device_id, "X-API-Key": api_key, "Content-Type": "application/json"}
        )

    def upload_events(self) -> bool:
        """上传一批待发送事件，返回是否成功。"""
        pending = self.db.get_pending_events(limit=BATCH_SIZE)
        if not pending:
            return True

        ids = [p[0] for p in pending]
        payloads = [p[1] for p in pending]
        try:
            resp = self._session.post(
                f"{self.server_url}/api/monitor/events",
                json={"device_id": self.device_id, "events": payloads},
                timeout=10,
            )
            if resp.status_code == 200:
                self.db.mark_uploaded(ids)
                logger.debug("Uploaded %d events", len(ids))
                return True
            logger.warning("Server rejected events: %s %s", resp.status_code, resp.text[:200])
        except requests.RequestException as e:
            logger.warning("Upload failed: %s", e)
        return False

    def upload_heartbeat(self, heartbeat: dict) -> bool:
        """立即发送一次心跳，失败时存本地。"""
        heartbeat["device_id"] = self.device_id
        try:
            resp = self._session.post(
                f"{self.server_url}/api/monitor/heartbeat",
                json=heartbeat,
                timeout=5,
            )
            return resp.status_code == 200
        except requests.RequestException as e:
            logger.warning("Heartbeat failed: %s", e)
            return False

    def upload_imminent_shutdown(self, event: dict) -> bool:
        """
        立即上传关机快照（最高优先级）。
        使用更短的超时和独立重试，因为系统正在关机，时间有限。
        """
        event["device_id"] = self.device_id
        for attempt in range(3):
            try:
                resp = self._session.post(
                    f"{self.server_url}/api/monitor/shutdown_snapshot",
                    json=event,
                    timeout=8,
                )
                if resp.status_code == 200:
                    logger.info("Shutdown snapshot uploaded successfully")
                    return True
            except requests.RequestException as e:
                logger.warning("Shutdown snapshot upload attempt %d failed: %s", attempt + 1, e)
            time.sleep(1)
        # 最后一搏：写本地
        self.db.save_event(event)
        return False

    def run(self):
        while not self._stop_evt.is_set():
            ok = self.upload_events()
            interval = UPLOAD_INTERVAL if ok else RETRY_INTERVAL
            self._stop_evt.wait(interval)

    def stop(self):
        self._stop_evt.set()
