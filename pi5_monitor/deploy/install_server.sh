#!/bin/bash
# 在腾讯云服务器上安装 pi5-monitor server。
# 以 root 执行: sudo bash install_server.sh
set -euo pipefail

INSTALL_DIR="/opt/pi5-monitor-server"
SERVICE_FILE="/etc/systemd/system/pi5-monitor-server.service"

echo "=== 安装 pi5-monitor server ==="

apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv

mkdir -p "$INSTALL_DIR"
cp -r "$(dirname "$0")/../server/." "$INSTALL_DIR/server/"
cp -r "$(dirname "$0")/../analyzer/." "$INSTALL_DIR/server/analyzer/"

python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/server/requirements.txt"

mkdir -p /var/lib/pi5-monitor-server /var/log/pi5-monitor-server

# 环境变量配置
mkdir -p /etc/pi5-monitor-server
cat > /etc/pi5-monitor-server/env <<'ENVEOF'
PI5_MONITOR_API_KEY=changeme
PI5_ALERT_WECHAT_WEBHOOK=
PI5_ALERT_TELEGRAM_TOKEN=
PI5_ALERT_TELEGRAM_CHAT_ID=
PI5_ALERT_WEBHOOK=
ENVEOF
echo "请编辑 /etc/pi5-monitor-server/env 填写告警 Webhook"

# systemd service
cat > "$SERVICE_FILE" <<'SVCEOF'
[Unit]
Description=Pi5 Monitor Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/pi5-monitor-server/server
ExecStart=/opt/pi5-monitor-server/venv/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 8098
Restart=always
RestartSec=5
EnvironmentFile=-/etc/pi5-monitor-server/env
StandardOutput=append:/var/log/pi5-monitor-server/server.log
StandardError=append:/var/log/pi5-monitor-server/server.log

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable pi5-monitor-server
systemctl start pi5-monitor-server

echo ""
echo "✓ 服务器安装完成，端口 8098"
echo "  查看日志: journalctl -u pi5-monitor-server -f"
echo "  健康检查: curl http://localhost:8098/api/monitor/health"
