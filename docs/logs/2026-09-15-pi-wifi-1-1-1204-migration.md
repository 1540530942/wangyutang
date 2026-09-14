# 2026-09-15 · 树莓派WiFi迁移到1-1-1204 + 过程中两个真实问题

## 背景

用户注意到树莓派(TurboPi/`raspberrypi`)当前连接的`ChinaNet-dsge`拿到的IP是
`172.20.10.5/28`（苹果热点默认网段），跟历史记录`192.168.1.46`不符，怀疑"应该
跟spark一样"。排查后发现：

- spark 的 NetworkManager 保存了3个WiFi profile，按优先级 `1-1-1204`(300,当前
  已连) > `ChinaNet-dsge`(200) > `pangdahai`(100)
- ESP32(WALLE)固件编译内置的"主/备"WiFi凭据也是 `1-1-1204`/`pangdahai`——跟
  spark是同一对网络
- 树莓派现场WiFi扫描：`1-1-1204`信号79/72（强，双AP/mesh），`ChinaNet-dsge`
  只有52（弱，且是172.20.10.x热点网段）——树莓派连的是信号更弱的那个网络

## 发现1：Hiwonder官方工具箱每次开机会清空WiFi配置

树莓派上 `wifi.service`（开机自启，`After=NetworkManager.service`）跑的是
`/home/pi/hiwonder-toolbox/wifi.py`（Hiwonder是TurboPi机器人套件厂商）。这个
脚本每次执行连接逻辑时，`disconnect()`函数会先执行：

```python
os.system('rm /etc/NetworkManager/system-connections/*')
```

**把所有已保存的WiFi配置全部删除**，然后只按自己的配置文件重新写入一个。
这解释了为什么树莓派永远只有一个保存的网络（不像spark能攒着3个按优先级选）——
每次重启都会清空。

真正的配置源是 `/home/pi/hiwonder-toolbox/wifi_conf.py`（不是`/etc/wifi/
wifi_conf.py`，那个不存在），定义 `WIFI_MODE`/`WIFI_STA_SSID`/
`WIFI_STA_PASSWORD`。目录里还留着历史备份：
- `wifi_conf.py.backup`（4月16日，原厂默认）：AP模式，SSID `hiwonder_5G`
- `wifi_conf.py.bak_codex`（5月16日）：**Codex之前把这台Pi配置成连
  `pangdahai`**——跟这次改的`1-1-1204`一样，都是spark/ESP32共用的网络之一
- 但同一天又被改回了`ChinaNet-dsge`（改动者不明）

说明"改了又被改回去"这件事之前真实发生过，不是错觉。

## 发现2：Korea反向隧道的sshd有长期未清理的CLOSE_WAIT积压

切换WiFi时用`nmcli device wifi connect`强行切网（没走正常TCP四次挥手），
导致Pi原本走Korea隧道的SSH路径断开，且新的隧道连接反复报错：

```
Error: remote port forwarding failed for listen port 10024
```

排查发现Korea VM(`43.155.169.6`)上负责这个端口的sshd进程（PID 1170）**积压了
几百个CLOSE_WAIT状态的僵尸连接**。按[[reference_korea_reverse_ssh_hub]]的架构
文档，Korea端sshd应该在约90秒内自动回收死掉的隧道连接，但实际上这个回收机制
明显没有真正生效——积压是长期累积的，不是这次事故刚产生的。

这次最后**自愈了**（隧道自己重新建立成功，没有手动介入Korea VM），但这个
CLOSE_WAIT积压问题本身没有解决，以后大概率还会再复现（wsl/spark用同一套
Korea隧道机制，任何一次粗暴断连都可能加重积压）。

## 修复过程（含一次自己的错误）

1. 用 `nmcli device wifi connect 1-1-1204 password ...` 临时连接验证信号/
   连通性正常（不改持久化配置）。
2. 从spark的NetworkManager用`nmcli --show-secrets`读出`1-1-1204`的密码
   （spark和这台Pi现在共用同一个网络）。
3. 备份原`wifi_conf.py`为`wifi_conf.py.bak_20260915_chinanet`，写入新的
   `WIFI_STA_SSID='1-1-1204'`。
4. **第一次重启验证是假阳性**：新写的配置文件里密码那行漏加了引号
   （`WIFI_STA_PASSWORD=Shuailin314159`而不是加引号的字符串字面量），导致
   `wifi.py`在读取配置时直接因Python语法/运行时错误崩溃退出（`systemctl
   status`显示 `exit-code 1`，只跑了1.78秒，`wifi.log`里完全没有这次开机的
   记录）。那次重启后树莓派能连上`1-1-1204`纯粹是NetworkManager自己保留的
   连接缓存生效，跟这次配置改动毫无关系——**被"重启后确实连上了"这个表象
   误导，没有先检查`wifi.service`自身是否真的成功执行**。
5. 用`ast.parse`能验证语法本身没错，但语法正确不等于运行时正确（字符串值
   缺引号在AST层面看不出来，只有真正import执行才会报错）——发现问题的
   关键是去看`systemctl status wifi.service`的exit code和`wifi.log`有没有
   这次开机的新记录，而不是只看"网络通不通"这一个表面结果。
6. 补上引号，`systemctl restart wifi.service`手动验证脚本自己打出
   `Connected to 1-1-1204`日志后，**重新做了一次真实的重启验证**：
   `uptime`确认"up 0 min"（真的刚重启），`wifi.log`里有紧跟在服务启动之后
   的新记录，这次才是真正验证通过。

## 结果

树莓派现在开机会自动、稳定地连接`1-1-1204`（跟spark/ESP32对齐），IP
`192.168.1.17`，Korea隧道和Tailscale直连两条SSH路径都正常。

## 教训

1. **验证"配置改动是否生效"，不能只看最终业务结果（网络通没通），要看
   执行改动的那个进程/脚本本身是否真的成功跑完**——网络能通可能是别的
   机制（这里是NetworkManager的连接缓存）凑巧顶上了，掩盖了改动本身其实
   失败的事实。
2. **生成/写入配置文件时，字符串值必须显式加引号**，即使目标语言（这里是
   Python）对未加引号的裸标识符只是在运行时才报错而非写入时报错——语法
   检查（`ast.parse`）能过，不代表值是合法的。
3. 这次意外发现了 Korea 隧道 sshd 的 CLOSE_WAIT 积压问题，值得后续找机会
   专门清理/修复回收机制，但本次未处理。
