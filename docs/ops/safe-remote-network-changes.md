# 远程网络变更的防失联流程

对 Wi-Fi、SSH、防火墙、路由和远程隧道的修改都可能切断当前管理路径。原则是：变更后的路径必须独立验证，旧路径在验证完成前必须保留。

## 变更前

1. 确认当前可用主路径和至少一条独立备用路径。
2. 实际测试备用路径，不以“USB 已插入”或“Tailscale 应该在线”代替验证。
3. 备份将修改的文件并记录权限、所有者和恢复命令。
4. 设置限时自动回滚，例如临时 systemd timer 或 `at` 任务。
5. 保持第二条 SSH 会话，不在唯一会话里直接重启 sshd、防火墙或网络服务。

## 多 SSID

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
