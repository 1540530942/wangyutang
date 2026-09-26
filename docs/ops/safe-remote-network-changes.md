# 远程网络变更的防失联流程

对 Wi-Fi、SSH、防火墙、路由和远程隧道的修改都可能切断当前管理路径。原则是：变更后的路径必须独立验证，旧路径在验证完成前必须保留。

## 变更前

1. 确认当前可用主路径和至少一条独立备用路径。
2. 实际测试备用路径，不以“USB 已插入”或“Tailscale 应该在线”代替验证。
3. 备份将修改的文件并记录权限、所有者和恢复命令。
4. 设置限时自动回滚，例如临时 systemd timer 或 `at` 任务。
5. 保持第二条 SSH 会话，不在唯一会话里直接重启 sshd、防火墙或网络服务。

## 多 SSID

先确定谁拥有网络配置。不要假设 `nmcli` 写入的 profile 就是唯一配置源：厂商镜像可能在开机时运行自己的 `wifi.service`、脚本或 watchdog，删除 NetworkManager profile、重写连接文件或反复重启 NetworkManager。

变更前先查：

```bash
systemctl list-unit-files | grep -Ei 'wifi|network|watchdog'
systemctl cat NetworkManager 2>/dev/null || true
systemctl cat wifi.service 2>/dev/null || true
grep -R -nE 'system-connections|nmcli|restart NetworkManager|rm .*/NetworkManager' \
  /etc/systemd/system /usr/lib/systemd/system /home 2>/dev/null
```

如果存在厂商配置生成器，应把它纳入设计：要么安全修改其真实配置源并验证服务本身成功，要么经验证后停用它并由 NetworkManager 完整接管。只添加三个 profile、却保留一个会在下次启动清空它们的服务，不算完成三 SSID 配置。

- 使用 NetworkManager 新增 profile，不覆盖整份连接配置。
- 保留已验证的旧网络作为回退。
- 为首选、次选和兜底网络设置明确优先级，例如 300、200、100。
- 瞬时失败应有重试阈值，不能一次探测失败就删除或切换配置。
- 密码通过交互输入或本机受限配置保存，禁止写入仓库、命令日志和文档。

检查示例：

```bash
nmcli -t -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show
nmcli -t -f ACTIVE,SSID device wifi
ip -4 -brief address
```

切换后必须从独立路径验证：互联网、Tailscale、反向隧道 service 和 hub 端到端 SSH。只有全部通过后才取消回滚。

## 防止“结果正确、改动失败”的假阳性

最终网络能通不证明刚修改的配置生效；旧 profile、缓存连接或备用服务可能碰巧维持了网络。每次重启验收都要同时证明：

1. `boot_id` 或 uptime 表明确实发生了本次重启；
2. 负责应用配置的 service 在本次启动中以成功状态结束或保持 active；
3. 它的日志时间晚于本次启动，并明确显示加载了预期配置；
4. 当前 SSID/profile 与优先级符合设计；
5. hub 反向别名和独立备用路径分别完成真实 SSH 登录。

示例：

```bash
cat /proc/sys/kernel/random/boot_id
uptime -s
systemctl status wifi.service --no-pager
journalctl -b -u wifi.service --no-pager
nmcli -t -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show
nmcli -t -f ACTIVE,SSID device wifi
```

配置文件属于 Python 等可执行语言时，静态语法通过仍不等于运行时值有效；必须验证真实 import/服务执行和退出码。

## SSH 与防火墙

- 优先增加 sshd drop-in，不整文件覆盖发行版配置。
- 始终先运行 `sshd -t`。
- 优先 `systemctl reload ssh`，确需 restart 时必须有第二路径。
- 新防火墙规则先以限时规则或自动回滚方式验证。
- 新公钥追加到 `authorized_keys`，不要覆盖旧授权；验证成功后再删除失效条目。

## 完成条件

变更只有在以下条件同时满足时才算完成：

- 新路径完成真实登录，而不只是 ping 或端口监听；
- 返回的 hostname/user 与目标设备一致；
- 旧路径仍可用或已明确完成受控退役；
- 自动回滚已在验证后取消；
- 当前 SSID/路径、服务状态和限制已经记录。
