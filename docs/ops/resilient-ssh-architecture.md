# 韧性 SSH 架构与部署

## 架构

固定公网 Linux hub 只作为会合点。移动设备主动向 hub 建立 SSH 连接，并把 hub 的一个回环端口转发回设备本机的 SSH 端口：

```text
WSL   -> HUB:22 -> HUB 127.0.0.1:<WSL_PORT>   -> WSL 127.0.0.1:22
Spark -> HUB:22 -> HUB 127.0.0.1:<SPARK_PORT> -> Spark 127.0.0.1:22
Pi    -> HUB:22 -> HUB 127.0.0.1:<PI_PORT>    -> Pi 127.0.0.1:22
```

hub 上的三个端口必须唯一且只监听 `127.0.0.1`。Wi-Fi SSID、DHCP 地址和 NAT 出口变化后，设备重新连上互联网即可重建隧道，不需要修改 hub 的 SSH 别名。

Tailscale 是独立的备用和诊断路径，不是反向隧道的前置依赖。网络切换时已有 SSH 会话仍可能中断；这里保证的是自动恢复，不是会话无缝迁移。

## 文件不会自动识别设备

GitHub 中的示例文件不执行任何发现逻辑。设备身份来自以下显式配置：

1. 安装了哪个设备专用 service 文件；
2. service 中使用的专用私钥和唯一反向端口；
3. hub `~/.ssh/config` 中别名到端口的对应关系；
4. 登录后返回的 `hostname` 和 `whoami` 验证结果。

不能只看到 hub 端口正在监听就判定设备正确。半死隧道也可能接受 TCP，但在 SSH banner 阶段超时。

## Keepalive 的能力边界

`ClientAliveInterval`/`ClientAliveCountMax` 是故障发现手段，不是端口回收承诺。它们只对仍由对应 sshd 会话管理、且能进入 keepalive 判定的连接有效；内核或进程已经积压的 `CLOSE_WAIT`、旧 sshd 子进程仍占用端口、以及客户端抢占失败，都需要另外定位。

因此不能用“配置值是 30/3”推导“90 秒后端口一定释放”。必须同时查看：

```bash
sudo sshd -T | grep -E '^(clientaliveinterval|clientalivecountmax|tcpkeepalive)'
sudo ss -lntp
sudo ss -tan state close-wait
ssh -vvv -o ConnectTimeout=10 DEVICE_ALIAS true
```

仓库提供的 `ops/ssh/check-reverse-tunnels.sh` 可把这几类只读证据放在同一份带时间戳的输出中。它发现异常后仍应先定位准确监听进程和服务日志，不能按名称批量杀 sshd。

## 部署前准备

在设备和 hub 上分别确认：

```bash
hostname
id
systemctl is-active ssh
ss -lntp
```

如使用 Tailscale，再检查：

```bash
systemctl is-active tailscaled
tailscale status
```

为每台设备分配独立端口，使用 `ss` 或 `lsof` 确认未被占用。为每台设备生成独立的、仅用于无人值守隧道的密钥；仓库中只保存路径占位符，不保存任何密钥内容。

## 客户端安装

在对应设备上选择且只选择自己的示例文件。下面以 Spark 为例：

```bash
sudo install -m 0644 ops/systemd/spark-korea-reverse-ssh.service.example \
  /etc/systemd/system/spark-korea-reverse-ssh.service
sudoedit /etc/systemd/system/spark-korea-reverse-ssh.service
```

替换文件中的所有 `REPLACE_...` 占位符，然后检查：

```bash
grep -n 'REPLACE_' /etc/systemd/system/spark-korea-reverse-ssh.service
sudo systemd-analyze verify /etc/systemd/system/spark-korea-reverse-ssh.service
sudo systemctl daemon-reload
sudo systemctl enable --now spark-korea-reverse-ssh.service
sudo systemctl status spark-korea-reverse-ssh.service
journalctl -u spark-korea-reverse-ssh.service -n 100 --no-pager
```

`grep` 应无输出。首次启用前通过独立渠道核验 hub 主机密钥，并将其写入 service 指定的专用 `known_hosts`；不要用 `StrictHostKeyChecking=no` 绕过验证。

## Hub 安装

在 Korea/hub 上安装 keepalive 前，先保留当前可用会话：

```bash
sudo install -m 0644 ops/ssh/korea-sshd-tunnel-keepalive.conf.example \
  /etc/ssh/sshd_config.d/99-tunnel-keepalive.conf
sudoedit /etc/ssh/sshd_config.d/99-tunnel-keepalive.conf
sudo /usr/sbin/sshd -t
sudo systemctl reload ssh
```

示例可全局生效，也可以取消注释 `Match User` 并替换专用隧道账户。修改 sshd 时使用 `reload`，不要在唯一会话中直接重启服务。

将 `korea-device-aliases.config.example` 的三个 `Host` 块复制到 hub 用户的 `~/.ssh/config`，替换端口、设备用户和登录密钥。它只提供登录别名，不启动隧道。

## 端到端验收

在 hub 上分别执行：

```bash
ssh wsl-device 'hostname; whoami; date -Is'
ssh spark-device 'hostname; whoami; date -Is'
ssh pi-device 'hostname; whoami; date -Is'
```

同时检查每个端口的真实所有者：

```bash
sudo ss -lntp
sudo lsof -nP -iTCP -sTCP:LISTEN
```

验收必须记录别名、端口、返回的 hostname/user、安装的 unit 名、备份位置和恢复耗时。配置 Tailscale 备用入口时，还要单独验证备用别名，确保它没有经过反向隧道。

监听端口缺失与 SSH 登录失败是两个不同结论：前者说明隧道没有落地，后者还可能是认证、主机密钥或目标 sshd 问题。任意一个失败都不能把该设备标记为“已覆盖”。

## 回滚

客户端 unit 失败时：

```bash
sudo systemctl disable --now <UNIT_NAME>
sudo mv /etc/systemd/system/<UNIT_NAME>.bak /etc/systemd/system/<UNIT_NAME>
sudo systemctl daemon-reload
```

hub sshd 配置失败时，保留当前会话并从第二会话验证；语法检查不通过则恢复备份，再执行 `sshd -t`。不要按模糊进程名批量杀死 sshd，只处理经 `ss`/`lsof` 确认占用目标端口的旧子进程。
