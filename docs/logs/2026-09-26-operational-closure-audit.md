# 2026-09-26 · 运维闭环复核

本次复核专门检查历史任务中“最终现象正常，但证据不足”或后来被反例推翻的结论。检查以只读方式进行，没有修改 Korea、WSL、Spark 或 Pi 的网络配置。

## 同时段现场结果

- WSL 到 Korea 的别名解析与密钥选择符合现有配置，登录成功。
- Korea 到 WSL、Spark 的反向 SSH 别名完成真实 `hostname`/`whoami` 登录。
- Korea 的 WSL、Spark 回环端口正在监听；检查时没有 `CLOSE_WAIT` 条目。
- Korea 的 Pi 别名所配置回环端口没有监听，返回 `Connection refused`。
- Pi 的 Tailscale 备用地址同时超时。因此本次无法登录 Pi，也无法把 Pi 的隧道、三 SSID 或厂商 `wifi.service` 标记为当前已验证。
- Windows 当前未发现 Raspberry/CDC/RNDIS USB 网络设备，`raspberrypi` 名称也无法解析；插线本身没有形成可用的 USB 数据或网络兜底路径。

这说明 keepalive 配置当前生效且现场没有积压，但不能反推历史 `CLOSE_WAIT` 根因已经解决；它也不能解释 Pi 客户端为何没有重建隧道。

## 重新定级的任务

### 1. Korea 僵尸隧道：从“已解决”改为“缓解并可取证”

历史上曾出现数百个 `CLOSE_WAIT` 和反向端口抢占失败。`ClientAliveInterval` 与 `ClientAliveCountMax` 能缩短部分故障发现时间，但不能保证内核状态、旧 sshd 子进程或端口占用一定在固定时间内消失。

本次更新增加统一检查脚本与明确的能力边界。下次复现必须在清理前保存 `ss -lntp`、`ss -tan state close-wait`、准确端口所有者、服务日志和 SSH banner 阶段输出。

### 2. Pi 三 SSID：当前不能宣称已稳定

历史证据已经证明 Hiwonder 的 `wifi.service` 会清空 NetworkManager 的连接目录，并按自己的配置重新生成单一网络。通用的“添加三个 NetworkManager profile”会在下次启动被覆盖，除非先改变配置所有权。

本次因两条 Pi 管理路径都不可达，只能把现场验证标记为阻塞。恢复物理或网络入口后，应先只读确认厂商 service、真实配置源、本次 boot 日志、NetworkManager profiles 和反向隧道 unit，再设计变更与自动回滚。

### 3. 配置验证：增加执行者与结果双证据

历史 Pi Wi-Fi 变更曾因 Python 配置值错误导致 `wifi.service` 失败，但 NetworkManager 缓存仍连上目标网络，形成假阳性。今后重启验收必须同时验证 boot 身份、服务退出状态、本次启动日志、当前结果和独立端到端入口。

### 4. 发布闭环：仍是待决策风险

当前主平台 workflow 仍允许 feature/codex 分支进入 production，且集成测试位于生产更新之后；部分边缘部署仍允许 `continue-on-error`。这些是策略与架构问题，不能仅凭一次 Action 运行成功判定已解决。

本次只补充版本证据链和“部分成功”的报告规则，没有擅自改变生产触发策略。后续应先决定分支保护、离线边缘设备的发布语义和失败回滚目标，再单独修改 workflow 并演练回滚。

## 本次仓库更新

- 修正 keepalive 会自动回收所有半死连接的过度表述；
- 增加厂商网络配置所有权检查；
- 增加防假阳性的重启验收；
- 增加 Git、CI、云端和边缘设备的运行版本证据链；
- 增加只读反向隧道检查脚本。
