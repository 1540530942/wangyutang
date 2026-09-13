# 2026-09-13 · 树莓派反复重启丢配置 + 发现 Korea 反向隧道架构

## 背景

用户要求给 ESP32-S3 实现双 SSID 回退（详见 v32-dualwifi 提交），并沿用同样思路评估能否把树莓派
也切到新 WiFi `1-1-1204`。过程中意外发现树莓派处于不稳定状态，且之前一次会话已经在
`wsl:.../arduino_lcd_1602/ops/` 下搭了一套之前完全不知道的远程访问基础设施。

## 发现一：树莓派 WiFi 配置丢失——真实原因是自带脚本主动删除，不是硬复位

**这一节的结论经过一次自我更正**：最初怀疑"供电不稳导致反复硬复位、配置来不及持久化"，后来翻查
`journalctl -u NetworkManager` 拿到确凿证据，推翻了这个猜测，记录下更正过程本身。

### 最初的（错误）线索

- 排查连接性时发现 Tailscale 上 `raspberrypi` 显示 `offline, last seen 23h ago`——从 spark、wsl
  两个独立节点分别验证一致，不是我这一侧网络问题。
- Pi 恢复后 `uptime` 显示内核 monotonic 只有几分钟，但 `who -b`/`last -x reboot` 的时间戳是一天前
  ——**Pi 没有带电池的硬件 RTC**，每次断电重启系统时钟先回到错误默认值再被 NTP 校正，所以
  `last`/`who -b` 的时间戳本身不可信，只能用 `uptime` 的 monotonic 计数判断真实重启间隔。
- `last -x` 里大量会话以 `crash` 结尾，且当晚密集出现多次 `reboot system boot` 记录
  （00:17→01:09→02:17→02:19→02:27→02:37），一度据此判断是"不干净的硬复位反复发生"。
- `/etc/NetworkManager/system-connections/` 磁盘上只剩 `ChinaNet-dsge.nmconnection`，据此**误判**为
  "配置加进 NetworkManager 后没来得及持久化写盘，被硬复位冲掉了"。

### 查日志后的真实结论

- `journalctl -u NetworkManager` 显示 `1-1-1204`/`pangdahai` 于 02:04:50 添加成功，**`1-1-1204`
  实际在 02:16:36 和 02:18:57 两次成功连接并被设为默认路由**——配置不仅落了盘，还真的用过，
  完全推翻了"从没验证过能连上"的说法（也跟 `RASPBERRY_PI_SSH.md` 里"新两组尚未验证"的记载不一致，
  说明文档写完后机器上又发生了变化，文档没跟上）。
- **真正删掉配置的是这台机器自带的 `wifi.py`**（`wifi.service`，Hiwonder/TurboPi 机器人自带的 WiFi
  配置管理脚本，路径 `/home/pi/hiwonder-toolbox/wifi.py`，大概率绑定物理配网按钮），代码里在
  AP/STA 模式切换时明确会执行 `nmcli connection down <x>` + `nmcli connection delete <x>`——这是
  **主动删除**，不是被动丢失。
- **另一个自带服务 `wifi-watchdog.timer`（每 90 秒跑一次 `wifi-watchdog.sh`）放大了问题**：健康检查
  用 `ping -c2 -W3 8.8.8.8` 判断网络是否正常，但**当前网络环境下 8.8.8.8 100% ping 不通**（实测丢包
  率 100%，国内网络下基本是永久性条件，不是偶发）——这导致脚本永远误判"网络不通"，按其逻辑升级到
  `systemctl restart NetworkManager`，**每 90 秒重启一次 NetworkManager，无限循环**。日志里
  `02:27:36→02:28:08→02:29:41→02:31:12...` 这串约 90 秒间隔的 NetworkManager 重启记录跟这个 timer
  的间隔完全吻合，坐实了这一机制。
- **整机层面反复出现的 `reboot system boot` 记录跟这次配置删除是否是同一件事，目前没有确凿证据**
  ——`wifi-watchdog.sh` 只会重启 NetworkManager 服务本身，不会重启整机，所以"供电不稳导致硬复位"
  这个最初的猜测虽被削弱，但我也没有拿到新证据去肯定或彻底否定它，这部分保留未定论,不重复此前不严谨
  的判断。
- 根目录确认是正常 `ext4 rw`，"写入失败"从一开始就不是真正原因。

### 影响与修复

`wifi-watchdog.sh` 会永远每 90 秒重启一次 NetworkManager 的问题**已修复**（2026-09-13 02:29）：
- 把 `PING_HOST` 从 `8.8.8.8` 改成 `223.5.5.5`（阿里 AliDNS，国内网络下稳定可达，实测 0% 丢包）
- 原脚本备份为 `/usr/local/bin/wifi-watchdog.sh.bak-2026-09-13`
- 验证：`ping 223.5.5.5` 从 Pi 上实测通；手动触发 `wifi-watchdog.service` 一次后 `journalctl` 无
  "unreachable" 记录；等过一个自然的 90 秒 timer 周期，同样没有新的误报日志（脚本只在判定断网时
  才打日志，静默过去即为正常）

丢失的 `1-1-1204`/`pangdahai` WiFi 配置**仍未重新添加**——按 `RASPBERRY_PI_SSH.md` 的规矩，先修完
这个会主动删连接的隐患源（`wifi.py` 的 AP/STA 切换逻辑本身还在，只是不会再被 watchdog 的误报重启
干扰），下次要重配时更安全一些，但仍建议按"只新增不激活、验证信号、留回滚"的流程来做。

## 发现二：一套完全独立于 Tailscale 的远程访问架构（此前不知情）

`wsl:C:\...\arduino_lcd_1602\ops\` 目录下 17 个文件，是 2026-09-12 另一次会话（用
`raspberry-pi-resilient-ssh` 技能）搭建的：wsl / spark / pi 三台设备各自跑一个 `systemd` 常驻服务
（`Restart=always`），**主动**反向连接一台公网 Korea VM（`43.155.169.6`，`ubuntu@VM-0-5-ubuntu`），
在 Korea 侧只监听 `127.0.0.1` 落地反向端口（wsl→10022，spark→10023，pi→10024），本地 `~/.ssh/config`
的 `wsl`/`spark`/`pi` 别名就是连这几个 loopback 端口。

这条路径与 Tailscale **完全独立**——Pi 在 Tailscale 上显示离线期间，`ssh pi`（走 Korea 隧道）依然
畅通，这解释了之前诊断时"一会儿通一会儿不通"的困惑，不是我操作有误，是两条链路本来就不共享故障域。
详见 `docs/logs/`（此文件）及 Claude 记忆 `reference_korea_reverse_ssh_hub`。

每台设备的反向隧道密钥在 Korea 端权限收得很紧：
`restrict,port-forwarding,permitlisten="127.0.0.1:10024",command="/bin/false"`，泄露也只能占用
那一个转发端口，登录不了 Korea 主机本身。

## 附：四台设备的 WiFi 优先级/自动重连机制对比

| 设备 | 机制 | 优先级设置 | 断线后的行为 | 是否需要"重启"生效 |
|---|---|---|---|---|
| **ESP32-S3** | 固件里手写的双凭据表（v32-dualwifi，见 `app_main.cpp`） | 数组顺序，无显式优先级数字；连续 3 次 `WIFI_EVENT_STA_DISCONNECTED` 才轮换到下一组，成功连接（`WIFI_EVENT_STA_CONNECTED`）清零失败计数 | 断线即触发 `esp_wifi_connect()` 重连当前凭据；攒够 3 次失败才换下一组 | 不需要重启，逻辑常驻固件运行时；只有**换新凭据本身**需要重新烧录/OTA |
| **Spark**（NetworkManager） | 系统原生多网络 profile | `autoconnect-priority`：`1-1-1204`=300 > `ChinaNet-dsge`=200 > `pangdahai`=100，`autoconnect-retries=2` | NM daemon 常驻监听，信号范围内断线/开机会自动挑优先级最高且能连上的已知网络，逐个重试 | 不需要重启，`nmcli`/NetworkManager 本身是持续运行的守护进程 |
| **树莓派**（NetworkManager，设计同 Spark） | 同上机制，但**磁盘上目前只剩 `ChinaNet-dsge` 一个 profile**（`autoconnect-priority=0`），另外两组已按上文"发现一"丢失 | 设计值应为 300/200/100（同 Spark），**实际现在没有优先级可言，因为候选只有一个** | 目前无法体现"自动回退"效果——只有单一网络，断了就是断了，没有备选 | 不需要重启才能生效，但**需要重新执行加配置的操作**才能恢复设计中的三网络回退能力 |
| **wsl 宿主机**（Windows, `netsh`） | Windows 原生已保存的 WiFi 配置文件列表（本次查到 13 个，含 `1-1-1204`/`ChinaNet-dsge`/个人热点/多个酒店网络等） | **未验证**——没有查过 Windows 是否对这些 profile 设置了显式连接顺序（`netsh wlan set profileorder`），目前只确认"已保存"，实际选择逻辑是 Windows 自身的"最近连接+信号强度"启发式 | Windows WiFi 服务常驻，断线会自动尝试已保存网络 | 不需要重启 |

**几个需要注意的点**：
- 只有 ESP32 是**没有操作系统级网络管理器**的，回退逻辑必须手写进固件、编译烧录才能生效——这也是当初做双 SSID 方案时特别谨慎的原因（换固件本身有变砖风险）。Spark/Pi/wsl 都是操作系统自带能力，改配置不需要编译或重启服务。
- Pi 目前是四台里**唯一实际上没有可用回退**的——设计和 Spark 一致，但配置在重启中丢了，处于"单点故障"状态，这也是本次发现一的核心问题。
- wsl 那条"优先级"完全没有验证过，写在这里是为了明确边界，不要在没查证的情况下假设它跟 Spark/Pi 一样有显式数字优先级。

## 处理

- 把 Tailscale 直连诊断别名 `pi-robot` 统一改名为 `pi-tailnet`（跟 `wsl-tailnet`/`spark-tailnet`
  命名一致），本地 `~/.ssh/config` 和 Korea 端源文件 `ops/korea-ssh-config` 都同步改了。
- **未**重新添加丢失的 `1-1-1204`/`pangdahai` WiFi 配置——按 `RASPBERRY_PI_SSH.md` 里记的规矩
  （"任何后续主动换网都应先准备并验证回滚任务，通过另一条会话验证再取消回滚"），建议先确认供电
  稳定后再重做，否则大概率又在下一次硬复位中白白丢失。

## 教训

1. **`last -x reboot` 的时间戳在无 RTC 设备上不可信**，必须用 `uptime` 的 monotonic 值或
   `who -b` 配合当前系统时间做交叉验证，否则会得出"重启间隔"完全错误的结论。
2. **Tailscale "offline" 不等于设备真的联系不上**——如果设备还有其他独立通路（这里是 Korea 反向
   隧道），诊断时必须两条路都查，不能看到一条不通就下"设备失联"的结论。
3. **"磁盘上文件不见了"不能直接等于"没持久化成功/被硬复位冲掉"**——这是本次最大的误判。第一次看到
   `system-connections/` 目录里只剩一个文件时，凭直觉套用了"写盘竞态"的解释，没有去查
   `journalctl -u NetworkManager` 里有没有 `connection-delete` 这类直接证据。**配置类问题优先查服务
   自己的日志，不要从"看起来像什么"直接跳到根因假设**——日志一查就发现配置其实成功持久化过、也真的
   连接使用过，是被同一台机器上另一个自带脚本（`wifi.py`）主动 `nmcli connection delete` 删掉的。
4. **健康检查脚本用错探测目标，后果比"检查不准"严重得多**——`wifi-watchdog.sh` 拿 `8.8.8.8` 判断
   网络死活，在这个网络环境下这个地址永久不通，导致脚本永远误判、每 90 秒重启一次 NetworkManager。
   这种"周期性自愈脚本"一旦探测目标选错，就会从"保障可用性"变成"持续制造不必要的服务重启"，而且
   不会主动报错——只会在无关的诊断（这次是查 WiFi 优先级）里意外暴露出来。以后接手任何设备前，应该
   主动看一遍它自带的看门狗/自愈类脚本在检查什么、用的什么判据。
5. 在没有查清楚全部现有基础设施之前就下诊断结论是有风险的——这次差点把"Pi 只有 Tailscale 一条路，
   没有真正带外通道"当成最终结论，直到翻出 `ops/` 目录才发现还有一整套独立的反向隧道方案。以后遇到
   "远程访问某设备"相关任务，先确认有没有类似 `ops/`、`RASPBERRY_PI_SSH.md` 这样的现成基础设施记录，
   不要假设自己已经掌握全部信息。

## 影响范围

- 无生产功能受影响（ESP32/audio_interact/action_move 均不涉及）
- 树莓派 WiFi 仍连 `ChinaNet-dsge`，未切换到 `1-1-1204`
- Korea 反向隧道全程未受影响，`pi-korea-reverse-ssh.service` 持续 active
