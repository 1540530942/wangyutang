# 2026-09-13 · AEC 内存泄漏定位到具体调用点

## 背景

`docs/aec/SUMMARY.md`（esp32_wifi 仓库）记录了 AEC 全双工验收已通过核心指标，
但被一个约 45B/命令的内部堆内存泄漏卡住（挡着 24 小时长跑 G6.1）。原文档把
范围缩小到"MQTT 通用命令路径"里的三个候选：payload→HubCommand 字符串分配 /
MqttCommandJob 队列项 / publish_status，建议在这三段各打一个 heap 点分辨。

本次会话利用 spark 上刚打通的物理USB build+flash+monitor 全链路（同日另一篇
`2026-09-13-spark-esp32-usb-toolchain-fix.md`），实际执行了这个下一步。

## 方法

在 `mqtt_control_client.cpp` 打了 9~11 个 heap checkpoint（`heap_caps_get_free_size
(MALLOC_CAP_INTERNAL)`），分三轮迭代：

1. 第一轮：`on_command`/`command_task` 内 H0~H6 共7个点，包住 job/root/args_json
   的分配释放，以及两次 `publish_status` 调用。
2. 第二轮：加 HR0/HR1 包住 `cJSON_Parse` 本身，加 HQ0/HQ1 包住整个
   `MQTT_EVENT_DATA` 事件处理体（含 `incoming_payload_` 的 append/clear）。
3. 第三轮：加 `pubdbg` 日志打印 `message_id`/`pending_status_.size()`/
   `message.size()`/`json_len`，直接验证"离线重试 map"这个候选。

每轮都是：改代码 → 同步到 spark 和 wsl → spark 上 `idf.py build` →
（**每次 flash 前先核对 sdkconfig 里 WiFi SSID 长度跟 wsl 一致**，见下方教训）→
`idf.py -p /dev/ttyACM0 flash` → 后台 `idf.py monitor` 录日志 →
`docker exec device-hub python3 /app/cmd_run.py aec_probe '{}' <超时>` 连发
20 次 → 拉日志用 Python 脚本按 command_id 分组、逐段算 delta。

## 结果

**泄漏精确定位到 `command_task` 里第二次 `publish_status()` 调用**——也就是
发送真实探测结果的 "done" 状态那次（第一次 "accepted"、消息为空的调用是干净的）。
21 个独立样本，`H4_after_handler → H5_after_final_publish` 这一段的 delta
**全部精确是 -44 字节，零方差**；其余所有区间（job 分配、root 释放、队列交接、
`delete job`）都有正常的背景噪声波动，唯独这一段是完全确定性的常数。

同时用 `pubdbg` 日志直接推翻了"离线重试 map (`pending_status_`)"这个候选：
10 次调用里 `message_id` 全部是正数（发布成功）、`pending_status_.size()`
全程为 0——这个 offline-retry 分支在测试里从未被触发过。

## 未解决的部分

`aec_probe` 的探测结果格式基本固定长度（`json_len` 每次都是169字节），导致
现有数据无法区分：
- 这 44B 是 `esp_mqtt_client_publish()`（QoS1）内部某个固定大小的记账结构
  没释放（esp-mqtt 是 vendored 第三方组件，需要读它的 outbox 释放路径源码），
- 还是跟消息内容长度成正比的泄漏，只是这次测试里内容长度从未变化过，
  掩盖了真实的比例关系。

需要用一个消息长度会变化的命令重复实验才能区分。已写入
`docs/aec/SUMMARY.md` 第 4.1.1 节，插桩代码本身也提交在
`feature/aec-fullduplex` 分支（commit `848d7b4`），标注为 TEMP 诊断代码，
方便随时抽掉或继续深挖。

## 一个真实事故（同一会话内）

排查过程中第一次在 spark 上 flash 验证工具链时，重建 sdkconfig 把 WiFi 凭据
清空了，导致设备真的掉线过一小段时间。已恢复，详见
`2026-09-13-spark-flash-wiped-wifi-creds-incident.md`。此后每次 flash 前都先
`awk` 核对 SSID 长度再烧录，本篇记录的三轮实验都做了这个检查。
