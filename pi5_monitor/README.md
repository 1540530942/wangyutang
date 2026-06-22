# pi5-monitor — 树莓派失联溯源系统

## 解决的问题

树莓派5（pi5-robot-01）偶尔无故关机或断联，需要精确知道：
- **谁**（哪个用户/进程）触发了关机
- **什么时间**
- **做了什么**（具体命令）
- **还是硬件/电源问题导致的**

---

## 架构

```
┌─────────────────────────────────────────────────────┐
│                   树莓派5                             │
│                                                      │
│  ┌─────────────────────────────────────────────┐    │
│  │           pi_agent (systemd 服务)            │    │
│  │                                              │    │
│  │  AuthCollector  → 监控 /var/log/auth.log     │    │
│  │  NetworkCollect → ip monitor link            │    │
│  │  PowerCollect   → vcgencmd + dmesg           │    │
│  │  ShutdownCollect→ journald + inhibitor lock  │    │
│  │  HeartbeatSender→ 每 15 秒发一次心跳         │    │
│  │                                              │    │
│  │  LocalDB (SQLite) ← 所有事件先存本地         │    │
│  │  Uploader → 批量推送到服务器                 │    │
│  └─────────────────────────────────────────────┘    │
│                                                      │
│  ┌──────────────────────────────────────────┐        │
│  │  pre_shutdown.sh (systemd ExecStop)      │        │
│  │  · 采集 who/last/journal/auditd 快照     │        │
│  │  · 3 次重试上传到服务器                  │        │
│  └──────────────────────────────────────────┘        │
│                                                      │
│  auditd 规则: 捕获 shutdown/reboot/systemctl 执行者  │
└─────────────────────────────────────────────────────┘
                         ↕  HTTP  (端口 8098)
┌─────────────────────────────────────────────────────┐
│              腾讯云服务器 (110.40.154.41)             │
│                                                      │
│  ┌──────────────────────────────────────────┐        │
│  │  FastAPI 服务器 (app.py)                  │        │
│  │  · /api/monitor/heartbeat                │        │
│  │  · /api/monitor/events                   │        │
│  │  · /api/monitor/shutdown_snapshot        │        │
│  │  · /api/monitor/devices                  │        │
│  │  · /api/monitor/incidents/{id}/report    │        │
│  └──────────────────────────────────────────┘        │
│                                                      │
│  Watchdog → 90 秒无心跳 → 开启 Incident              │
│  Analyzer → 分析事件时间线 + 快照 → 生成原因摘要      │
│  Alerter  → 企业微信 / Telegram / Webhook 推送        │
└─────────────────────────────────────────────────────┘
```

---

## 监控的事件类型

| 事件类型 | 来源 | 捕获内容 |
|---------|------|---------|
| `ssh_login` | auth.log | 谁从哪个 IP 登录了 |
| `ssh_disconnect` | auth.log | SSH 连接断开 |
| `sudo_cmd` | auth.log | 谁用 sudo 执行了什么（标记关机相关命令） |
| `network_change` | ip monitor | 网卡 UP/DOWN 变化 |
| `power_status` | vcgencmd | CPU 温度、节流状态 |
| `power_undervoltage` | dmesg/journal | 电压不足告警 |
| `power_overtemp` | dmesg/journal | 过热告警 |
| `oom_kill` | dmesg/journal | OOM 进程被杀 |
| `disk_error` | dmesg/journal | I/O 错误、文件系统错误 |
| `shutdown_signal_detected` | journald | 关机信号被检测到 |
| `imminent_shutdown` | SIGTERM/inhibitor | 系统即将关机（包含完整快照） |
| `pre_shutdown_hook` | ExecStop 脚本 | 关机前最终快照（audit+who+journal） |

---

## 部署

### 1. 服务器端（腾讯云）

```bash
# 在项目根目录
cd pi5_monitor
docker-compose up -d

# 或直接安装
sudo bash deploy/install_server.sh
```

编辑 `/etc/pi5-monitor-server/env` 填写告警 Webhook：
```
PI5_MONITOR_API_KEY=your_secret_key
PI5_ALERT_WECHAT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

### 2. 树莓派端

```bash
# 将 pi5_monitor 目录传到 Pi 上
scp -r pi5_monitor pi@<pi_ip>:/tmp/

# 在 Pi 上安装
sudo bash /tmp/pi5_monitor/deploy/install_pi.sh
```

编辑 `/etc/pi5-monitor/env`：
```
PI5_MONITOR_SERVER=http://110.40.154.41:8098
PI5_DEVICE_ID=pi5-robot-01
PI5_MONITOR_API_KEY=your_secret_key
```

---

## 查询失联报告

```bash
# 设置环境变量（一次）
export PI5_MONITOR_SERVER=http://110.40.154.41:8098
export PI5_MONITOR_API_KEY=your_secret_key
export PI5_DEVICE_ID=pi5-robot-01

# 列出所有设备状态
python3 deploy/query_incident.py devices

# 列出所有失联事件
python3 deploy/query_incident.py incidents

# 查看事件 #3 的完整报告（谁？什么时间？做了什么？）
python3 deploy/query_incident.py report --incident 3

# 查看最近 2 小时的事件流
python3 deploy/query_incident.py events --since 2h

# 只看关机相关和 sudo 事件
python3 deploy/query_incident.py events --since 6h --types sudo_cmd,shutdown_signal_detected,imminent_shutdown
```

---

## 典型报告输出示例

```
============================================================
失联事件 #3 分析报告
============================================================
设备:         pi5-robot-01
检测时间:     2026-06-21 14:35:00 UTC
最后心跳:     2026-06-21 14:33:47 UTC
恢复时间:     2026-06-21 14:58:22 UTC
失联时长:     1415秒

──────────────────────────────────────────────────────────
原因分析:
可能原因:
[2026-06-21 14:33:20 UTC] 用户 'ubuntu' 通过 sudo 执行了: /sbin/shutdown -h now

──────────────────────────────────────────────────────────
关机前快照分析:
[auditd] 用户 uid=1000 执行了命令: shutdown
[logind] Power off requested by user ubuntu
[sudo] 最近的关机相关 sudo 命令:
  Jun 21 14:33:18 pi5 sudo: ubuntu : COMMAND=/sbin/shutdown -h now
[who] 关机时登录中的用户: ubuntu   pts/0   2026-06-21 14:20 (192.168.1.105)

──────────────────────────────────────────────────────────
失联前事件时间线:
  [2026-06-21 14:20:13 UTC] ssh_login        user=ubuntu from=192.168.1.105
  [2026-06-21 14:33:18 UTC] sudo_cmd         user=ubuntu cmd=/sbin/shutdown -h now
  [2026-06-21 14:33:19 UTC] shutdown_signal_detected  System is going down
  [2026-06-21 14:33:20 UTC] imminent_shutdown SIGTERM received
```

---

## 文件结构

```
pi5_monitor/
├── pi_agent/                     # 运行在树莓派5上
│   ├── agent.py                  # 主入口
│   ├── heartbeat.py              # 心跳发送
│   ├── local_db.py               # 本地 SQLite 缓冲
│   ├── uploader.py               # 事件上传
│   ├── collectors/
│   │   ├── auth_collector.py     # SSH/sudo 监控
│   │   ├── network_collector.py  # 网络接口监控
│   │   ├── power_collector.py    # 电源/温度监控
│   │   └── shutdown_collector.py # 关机信号监控
│   ├── hooks/
│   │   └── pre_shutdown.sh       # 关机前快照脚本
│   └── pi5-monitor-agent.service # systemd 服务
├── server/                       # 运行在腾讯云
│   ├── app.py                    # FastAPI 应用
│   ├── database.py               # SQLite 存储
│   ├── watchdog.py               # 心跳看门狗
│   ├── alerter.py                # 告警发送
│   └── requirements.txt
├── analyzer/
│   └── analyzer.py               # 失联原因分析
├── deploy/
│   ├── install_pi.sh             # Pi 端安装脚本
│   ├── install_server.sh         # 服务器安装脚本
│   └── query_incident.py         # 命令行查询工具
├── docker-compose.yml
├── Dockerfile.server
└── README.md
```
