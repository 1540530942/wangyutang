#!/bin/bash
# 在树莓派5上安装 pi5-monitor agent。
# 以 root 执行: sudo bash install_pi.sh
set -euo pipefail

INSTALL_DIR="/opt/pi5-monitor"
SERVICE_FILE="/etc/systemd/system/pi5-monitor-agent.service"
ENV_FILE="/etc/pi5-monitor/env"

echo "=== 安装 pi5-monitor agent ==="

# ── 1. 安装系统依赖 ──────────────────────────────────────────────────
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv \
    auditd curl iproute2 util-linux

# ── 2. 复制文件 ──────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
cp -r "$(dirname "$0")/../pi_agent/." "$INSTALL_DIR/pi_agent/"

# ── 3. 创建虚拟环境 ──────────────────────────────────────────────────
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/pi_agent/requirements.txt"

# ── 4. 创建日志 / 数据目录 ───────────────────────────────────────────
mkdir -p /var/log/pi5-monitor /var/lib/pi5-monitor

# ── 5. 配置文件 ──────────────────────────────────────────────────────
mkdir -p /etc/pi5-monitor
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'ENVEOF'
PI5_MONITOR_SERVER=http://110.40.154.41:8098
PI5_DEVICE_ID=pi5-robot-01
PI5_MONITOR_API_KEY=changeme
ENVEOF
    echo "请编辑 $ENV_FILE 填写正确的服务器地址和 API Key"
fi

# ── 6. 设置 hook 可执行权限 ──────────────────────────────────────────
chmod +x "$INSTALL_DIR/pi_agent/hooks/pre_shutdown.sh"

# ── 7. 安装 systemd service ──────────────────────────────────────────
cp "$INSTALL_DIR/pi_agent/pi5-monitor-agent.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable pi5-monitor-agent
systemctl start pi5-monitor-agent

echo "=== 配置 auditd 捕获关机命令 ==="
# 监控 shutdown/reboot/halt/poweroff/systemctl 的执行
cat >> /etc/audit/rules.d/pi5-monitor.rules <<'AUDITEOF'
# pi5-monitor: 捕获关机相关命令的发起者
-a always,exit -F arch=b64 -S execve -F exe=/sbin/shutdown -k pi5_shutdown
-a always,exit -F arch=b64 -S execve -F exe=/sbin/reboot -k pi5_shutdown
-a always,exit -F arch=b64 -S execve -F exe=/sbin/halt -k pi5_shutdown
-a always,exit -F arch=b64 -S execve -F exe=/sbin/poweroff -k pi5_shutdown
-a always,exit -F arch=b64 -S execve -F exe=/usr/bin/systemctl -k pi5_shutdown
AUDITEOF
systemctl enable auditd
systemctl restart auditd

echo ""
echo "✓ 安装完成"
echo "  查看日志: journalctl -u pi5-monitor-agent -f"
echo "  查看状态: systemctl status pi5-monitor-agent"
echo "  编辑配置: nano $ENV_FILE && systemctl restart pi5-monitor-agent"
