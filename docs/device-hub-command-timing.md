# device_hub 指令时间戳与播报延迟观测

> 用途：记录 `device_hub/server.py` 里指令生命周期的时间戳字段含义，用于排查"下发播报到端侧真实播放"之间的延迟问题。

## 背景

平台网页/API 下发 `play_audio`/`stop_audio` 等指令时，走的是：

```
云侧 HTTP 请求 → _enqueue_mqtt_command() → MQTT 主题 devices/<id>/command/<id> → ESP32 执行 → MQTT 主题 devices/<id>/ack → 云侧记录完成
```

"指令已下发"不等于"端侧已经开始播放"，中间隔着 MQTT 传输 + 设备处理排队。要判断真实播报时刻，需要看下面几个字段，而不是只看 `dispatched_at`。

## 指令时间戳字段（`rec["commands"][i]`）

| 字段 | 含义 | 写入方 | 代码位置 |
|------|------|--------|----------|
| `created_at` | 云侧创建指令的时刻 | 云侧 | server.py 各 `_enqueue_*` 函数 |
| `dispatched_at` | 指令通过 MQTT 推送出去（或设备心跳拉取到）的时刻——**只代表云侧发出，不代表端侧收到/执行** | 云侧 | server.py:401, 559, 614, 682 等 |
| `accepted_at` | 设备收到并确认接受指令的时刻 | 端侧 ACK（`status=="accepted"`） | server.py:141-144 |
| `playback_done_at` | 设备**实际播放完成**的时刻，仅当 ACK payload 里 `event=="playback_done"` 时写入 | 端侧 ACK | server.py:139-140 |
| `playback_elapsed_ms` | 端侧自报的播放耗时，从 ACK message 正则解析 `elapsed_ms=(\d+)` | 端侧 ACK | server.py:135 |
| `done_at` | 通用完成时间，`status` 变成 `done`/`failed`/`unsupported` 时写入——各类指令通用，不特指播放完成 | 端侧 ACK（`/api/ack` 或 MQTT ack topic） | server.py:148, 421 |

## 如何算出真实播放窗口

- 播放**结束**时刻 ≈ `playback_done_at`（服务器收到该条 MQTT ACK 消息的时刻，比设备真实结束时刻晚几十到几百毫秒——MQTT 网络延迟）
- 播放**开始**时刻 ≈ `playback_done_at - playback_elapsed_ms / 1000`（反推，非独立打点）
- 端到端总时长（下发到收到完成 ACK）= `done_at - created_at`，包含了 MQTT 传输 + 设备处理排队 + 实际播放时长，**不是纯播放时长**

## 常见误用

- 用 `dispatched_at` 当作"端侧已开始播放"的时刻——错误，那只是云侧发出的时刻。
- 用 `done_at - created_at` 当作播放时长——错误，那是含传输延迟的端到端时长；纯播放时长应看 `playback_elapsed_ms`。
- 认为服务器记录的时间戳和设备本地时钟严格同步——不是，两者之间隔着 MQTT 网络延迟，量级通常是几十到几百毫秒，非硬件级时钟同步。

## ACK 数据来源

- MQTT 订阅 `devices/+/ack`（`_mqtt_ack_worker`，server.py:153-169）
- 或 HTTP `POST /api/ack`（`AckReq`：`device_id` / `command_id` / `status` / `message`，server.py:329-333, 409-424）
