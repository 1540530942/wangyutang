# 2026-09-13 · ESP32 云端下发 WiFi 凭据（v33-remotewifi）+ 一次误判排查

## 背景

在 v32-dualwifi（双 SSID 回退）基础上，用户要求进一步支持**云端远程下发新 SSID/密码，写入 NVS 立即生效，不用重新编译烧录**；下发无效时退回主备编译凭据；每次重启始终优先尝试云端下发的凭据。

## 实现

`esp32_wifi` 仓库 `feature/aec-fullduplex` 分支，commit `8330c23`：
- 凭据表从 2 级（编译主/备）扩成 3 级：**NVS 远程 > 编译主 `1-1-1204` > 编译备 `pangdahai`**（原备用 `ChinaNet-dsge` 一并换掉）
- 新增 MQTT 命令 `wifi_set_remote`，`{ssid, password}` 写入 NVS 并立即断线重连；空 `ssid` 触发 `nvs_erase_key` 清除
- 复用原有"连续 3 次失败才轮换"逻辑，NVS 凭据失败会自动滚到编译主/备，且这个优先级在开机时也生效（`init_wifi()` 每次都先 `wifi_load_creds()`）

## 一次误判：以为固件在崩溃重启循环，其实是自己的测试方式有问题

验证"错误凭据自动回退"时，为了快速触发重启，直接调用了 device_hub 内部的
`server._enqueue_mqtt_command()` 而不是走 `cmd_run.py`。观察到设备 uptime 反复
卡在 10-20s 就归零，**一度怀疑是新代码在坏凭据下崩溃重启**。

排查后发现：`_enqueue_mqtt_command` 发布的是 **retained** MQTT 消息，而
`cmd_run.py` 在等到结果后会调用 `_mqtt_clear_retained_command` 清掉；绕过
`cmd_run.py` 直接调用底层接口就漏了这一步，导致那条 `reboot` 命令被 retain
在 broker 上，设备每次重新连上 MQTT 就重放一次——是**测试方式的问题，不是
固件问题**。用 `server._mqtt_clear_retained_command` 手动清掉后，uptime 立即
恢复正常增长，问题消失。

## 验证结果

- 推送一个不存在的 SSID：设备在正常开机周期内完成 3 次失败重试并回退到编译主
  `1-1-1204`，不崩溃、不影响其他重启（干净重启后第一次心跳 ~18s，明显比有效
  凭据的 ~4s 慢，符合"多试了几次坏凭据"的预期）
- 推送有效凭据（`1-1-1204` 真实密码）：立即生效，第一次心跳仅 ~4s，重启后依旧
  优先使用
- 推送空 `ssid`：返回 `done|wifi_remote_cleared`，NVS 覆盖清除正常

## 教训

**不要绕过 `cmd_run.py` 直接调用 device_hub 内部的命令下发接口做测试**——
它省掉了 retained 清理这一步，会制造出"看起来像固件崩溃循环"的假象，浪费
排查时间。以后需要快速下发测试命令，要么用 `cmd_run.py`，要么手动记得在
之后调用清理函数。

## 影响范围

- 设备最终固件 `esp32-wangyutang-v33-remotewifi`，稳定运行，OTA 全程无遗留
  retained 命令
- 备用 SSID 变更（`ChinaNet-dsge`→`pangdahai`）不影响生产使用，主 SSID 未变

## 收尾

`git fetch` 确认 `esp32_wifi` 仓库 `feature/aec-fullduplex` 分支本地与远端完全
同步，双向 `log` 对比（`origin..HEAD`、`HEAD..origin`）均为空，都停在 `8330c23`。
本轮 WSL 长达约 7 小时的断连期间用 `/loop` 每 5-30 分钟递增间隔轮询等待恢复，
恢复后一次性完成全部实现、编译、OTA、验证、提交、推送，未留任何未完成事项。
