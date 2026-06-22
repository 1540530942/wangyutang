"""
告警发送模块。支持：
  - 企业微信机器人 Webhook
  - Telegram Bot
  - 通用 Webhook (POST JSON)
从环境变量读取配置，任何一项为空则跳过对应渠道。
"""
import logging
import os
from datetime import datetime, timezone

import requests

logger = logging.getLogger("alerter")

WECHAT_WEBHOOK = os.environ.get("PI5_ALERT_WECHAT_WEBHOOK", "")
TELEGRAM_TOKEN = os.environ.get("PI5_ALERT_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("PI5_ALERT_TELEGRAM_CHAT_ID", "")
GENERIC_WEBHOOK = os.environ.get("PI5_ALERT_WEBHOOK", "")


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def send_offline_alert(device_id: str, incident_id: int, last_heartbeat_ts: float | None):
    last_seen = (
        datetime.fromtimestamp(last_heartbeat_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if last_heartbeat_ts
        else "unknown"
    )
    text = (
        f"🔴 [PI5-MONITOR] 设备离线告警\n"
        f"设备: {device_id}\n"
        f"事件ID: #{incident_id}\n"
        f"最后心跳: {last_seen}\n"
        f"检测时间: {_now_str()}"
    )
    _send_all(text)


def send_online_alert(device_id: str, incident_id: int, cause: str):
    text = (
        f"🟢 [PI5-MONITOR] 设备恢复上线\n"
        f"设备: {device_id}\n"
        f"事件ID: #{incident_id}\n"
        f"恢复时间: {_now_str()}\n"
        f"失联原因分析: {cause}"
    )
    _send_all(text)


def send_shutdown_snapshot_alert(device_id: str, summary: str):
    text = (
        f"⚠️ [PI5-MONITOR] 收到关机前快照\n"
        f"设备: {device_id}\n"
        f"时间: {_now_str()}\n"
        f"分析摘要:\n{summary}"
    )
    _send_all(text)


def _send_all(text: str):
    if WECHAT_WEBHOOK:
        _send_wechat(text)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        _send_telegram(text)
    if GENERIC_WEBHOOK:
        _send_generic_webhook(text)
    # 始终打印到日志
    logger.info("ALERT: %s", text)


def _send_wechat(text: str):
    try:
        resp = requests.post(
            WECHAT_WEBHOOK,
            json={"msgtype": "text", "text": {"content": text}},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning("WeChat webhook failed: %s", resp.text[:200])
    except Exception as e:
        logger.warning("WeChat webhook error: %s", e)


def _send_telegram(text: str):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning("Telegram failed: %s", resp.text[:200])
    except Exception as e:
        logger.warning("Telegram error: %s", e)


def _send_generic_webhook(text: str):
    try:
        resp = requests.post(
            GENERIC_WEBHOOK,
            json={"text": text, "source": "pi5-monitor"},
            timeout=5,
        )
        if resp.status_code not in (200, 201, 204):
            logger.warning("Generic webhook failed: %s", resp.status_code)
    except Exception as e:
        logger.warning("Generic webhook error: %s", e)
