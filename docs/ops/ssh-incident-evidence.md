# SSH 间歇故障取证

间歇问题在重新连接成功后经常失去现场。故障发生时先保存证据，再修改配置或清理连接。

## 同期对照

为别名和显式地址分别保存带时间戳的完整日志：

```bash
mkdir -p ~/ssh-diagnostics

log=~/ssh-diagnostics/alias-$(date +%Y%m%d-%H%M%S).log
ssh -vvv -o ConnectTimeout=10 -o ConnectionAttempts=1 DEVICE_ALIAS 2>&1 | tee "$log"
printf 'exit=%s log=%s\n' "${PIPESTATUS[0]}" "$log"

log=~/ssh-diagnostics/direct-$(date +%Y%m%d-%H%M%S).log
ssh -vvv -o ConnectTimeout=10 -o ConnectionAttempts=1 DEVICE_USER@DEVICE_HOST 2>&1 | tee "$log"
printf 'exit=%s log=%s\n' "${PIPESTATUS[0]}" "$log"
```

同时保存有效配置，确认别名实际使用的地址、用户和密钥：

```bash
ssh -G DEVICE_ALIAS | grep -E '^(hostname|port|user|identityfile|identitiesonly|proxyjump|proxycommand) '
```

如果故障是 hub 到反向端口，还要保存：

```bash
date -Is
sudo ss -lntp
sudo lsof -nP -iTCP -sTCP:LISTEN
ssh -vvv DEVICE_ALIAS
```

若同时维护多台设备，优先一次性执行只读检查，避免在不同时间点观察到不同现场：

```bash
bash ops/ssh/check-reverse-tunnels.sh \
  wsl-device:22022 spark-device:22023 pi-device:22024 \
  | tee "reverse-tunnels-$(date +%Y%m%d-%H%M%S).log"
```

在设备端保存：

```bash
systemctl status <REVERSE_SSH_UNIT> --no-pager
journalctl -u <REVERSE_SSH_UNIT> --since '-30 minutes' --no-pager
ip -brief address
```

## 错误分类

- `Could not resolve hostname`：名称或 DNS/MagicDNS 问题。
- 停在 `Connecting to ...` 或 `Connection timed out`：TCP、路由、ACL、防火墙或监听问题。
- `Permission denied (publickey)`：链路已通，检查准确用户、密钥和文件权限。
- `Host key verification failed`：核验预期密钥和 `HostKeyAlias`，不要清空全部 `known_hosts`。
- `remote port forwarding failed`：hub 端口被占用；先定位准确所有者。
- TCP 已建立但 banner 超时：优先怀疑半死反向隧道或卡住的 sshd 子进程。
- `Connection refused` 且目标回环端口没有监听：客户端隧道未建立或已经退出，不是登录密钥问题。
- 端口正在监听但 `hostname` 与别名预期不符：端口映射或隧道身份串线，不能继续按该别名运维。

一次普通重试成功不能证明别名有问题，也不能证明问题已经修复。只有同一时段的两份调试日志才适合比较。

## 隐私

日志可能包含用户名、主机地址、公钥指纹、代理配置和文件路径。上传 issue 或提交仓库前必须脱敏；私钥、密码和令牌不得进入日志附件。
