"""
pi5-monitor 主入口。
将所有 collector 串联起来，所有事件流向：
  event → LocalDB → Uploader → 服务器

关机快照走单独的紧急通道（upload_imminent_shutdown）。
"""
import logging
import os
import sys
import time
from pathlib import Path

from collectors.auth_collector import AuthLogCollector
from collectors.network_collector import NetworkCollector
from collectors.power_collector import PowerCollector
from collectors.shutdown_collector import ShutdownCollector
from heartbeat import HeartbeatSender
from local_db import LocalDB
from uploader import Uploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/pi5-monitor/agent.log"),
    ],
)
logger = logging.getLogger("agent")

# ──────────────────────────────────────────────
# 配置（从环境变量读取，方便 systemd EnvironmentFile）
# ──────────────────────────────────────────────
SERVER_URL = os.environ.get("PI5_MONITOR_SERVER", "http://110.40.154.41:8098")
DEVICE_ID = os.environ.get("PI5_DEVICE_ID", "pi5-robot-01")
API_KEY = os.environ.get("PI5_MONITOR_API_KEY", "changeme")


def main():
    Path("/var/log/pi5-monitor").mkdir(parents=True, exist_ok=True)
    Path("/var/lib/pi5-monitor").mkdir(parents=True, exist_ok=True)

    db = LocalDB()
    uploader = Uploader(SERVER_URL, DEVICE_ID, API_KEY, db)

    def on_event(event: dict):
        row_id = db.save_event(event)
        logger.info("[EVENT] #%d type=%s", row_id, event.get("type"))

    def on_imminent_shutdown(event: dict):
        logger.warning("[SHUTDOWN IMMINENT] Uploading snapshot NOW")
        uploader.upload_imminent_shutdown(event)

    auth_collector = AuthLogCollector(on_event)
    network_collector = NetworkCollector(on_event)
    power_collector = PowerCollector(on_event)
    shutdown_collector = ShutdownCollector(on_event, on_imminent_shutdown)
    heartbeat_sender = HeartbeatSender(DEVICE_ID, uploader.upload_heartbeat)

    # 启动所有子线程
    uploader.start()
    auth_collector.start()
    network_collector.start()
    power_collector.start()
    shutdown_collector.start()
    heartbeat_sender.start()

    logger.info(
        "pi5-monitor agent started. device_id=%s server=%s", DEVICE_ID, SERVER_URL
    )

    try:
        while True:
            pending = db.count_pending()
            if pending > 0:
                logger.debug("Pending events in local DB: %d", pending)
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down agent...")
        for t in [uploader, auth_collector, network_collector, power_collector,
                  shutdown_collector, heartbeat_sender]:
            t.stop()


if __name__ == "__main__":
    main()
