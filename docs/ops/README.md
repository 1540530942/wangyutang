# 远程运维手册

本目录保存 WSL、Spark 和 Raspberry Pi 等移动网络设备的通用运维经验。
配套配置示例位于 [`../../ops/`](../../ops/README.md)。

## 最重要的边界

- GitHub 中的 `.service`、`.conf.example` 和 `.config.example` 都只是文件，上传后不会在任何服务器上自动运行或生效。
- 必须由维护者将对应示例复制到指定设备，替换占位符，完成语法检查并显式启用。
- WSL、Spark、Pi 分别安装自己的 systemd 服务；固定公网 hub（本文称为 Korea）安装 sshd keepalive 和 SSH 客户端别名。
- 每台设备必须使用不同的 hub 回环端口。端口与设备的映射由维护者配置，不由服务器猜测。
- 不向仓库提交密码、私钥、SSID、真实公网地址、真实 Tailnet 地址或未经脱敏的 `authorized_keys`。

## 文档导航

- [resilient-ssh-architecture.md](resilient-ssh-architecture.md)：主链路、备用链路、文件安装位置和端到端验证。
- [safe-remote-network-changes.md](safe-remote-network-changes.md)：Wi-Fi、SSH、防火墙和路由变更的防失联流程。
- [ssh-incident-evidence.md](ssh-incident-evidence.md)：间歇 SSH 故障的现场取证与错误分类。
- [usb-and-out-of-band-recovery.md](usb-and-out-of-band-recovery.md)：USB、串口、HDMI 和管理控制台的真实能力边界。
- [runtime-verification.md](runtime-verification.md)：避免假阳性，并把 Git、制品、云端和边缘运行版本串成证据链。

## 配置责任矩阵

| 文件 | 安装位置 | 生效设备 | 作用 |
|---|---|---|---|
| `ops/systemd/korea-wsl-reverse-ssh.service.example` | WSL `/etc/systemd/system/korea-wsl-reverse-ssh.service` | WSL | WSL 主动建立并维护反向隧道 |
| `ops/systemd/spark-korea-reverse-ssh.service.example` | Spark `/etc/systemd/system/spark-korea-reverse-ssh.service` | Spark | Spark 主动建立并维护反向隧道 |
| `ops/systemd/pi-korea-reverse-ssh.service.example` | Pi `/etc/systemd/system/pi-korea-reverse-ssh.service` | Raspberry Pi | Pi 主动建立并维护反向隧道 |
| `ops/ssh/korea-sshd-tunnel-keepalive.conf.example` | Korea `/etc/ssh/sshd_config.d/99-tunnel-keepalive.conf` | Korea sshd | 缩短部分失联连接的发现时间；不能保证清理所有 `CLOSE_WAIT` 或释放旧端口 |
| `ops/ssh/korea-device-aliases.config.example` | Korea `~/.ssh/config` 的受管片段 | Korea SSH 客户端 | 把设备别名映射到三个不同的回环端口 |
| `ops/ssh/check-reverse-tunnels.sh` | Korea 任意只读诊断目录 | Korea 运维终端 | 同时核对监听、TCP 状态和真实 SSH 登录；不修改服务 |

仓库文件与已安装文件没有自动同步关系。仓库更新后，现网配置不会自动变化；安装现网配置也不会自动回写仓库。
