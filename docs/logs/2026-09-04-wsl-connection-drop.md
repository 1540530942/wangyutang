# WSL SSH 连接中断记录

**开始时间**：2026-09-04 12:50
**持续至**：2026-09-04 13:22（仍未恢复）

## 影响
- MD 桥（ESP32_WANGYUTANG_PLAN.md）无法写入，本次 30min/小时检查记录暂停
- tang 和 ESP32 设备不受影响

## 设备状态（期间持续正常）
- online=True, fw=v20-boot, uptime~99min, boot_count=30, mqtt=True

## 恢复后待补
1. 写入缺失的 30min/小时 MD 桥条目
2. 确认 WSL 文件完整性
2026-09-04 13:51 — WSL 仍不可达，ESP32 online fw=v20-boot uptime=6876s 正常
2026-09-04 15:22 — WSL 仍不可达（中断超 2.5h），ESP32 online fw=v20-boot uptime=8629s boot_count=30 mqtt=True audio=False 正常
2026-09-04 16:22 — WSL 仍不可达（中断超 3.5h），ESP32 持续正常，MD 桥写入挂起
2026-09-04 17:08 — WSL 仍不可达（中断超 4.3h），ESP32 online fw=v20-boot uptime=9513s boot_count=30 mqtt=True audio=False 正常
2026-09-05 00:33 — WSL 仍不可达（中断超 11.7h，跨越午夜），ESP32 online fw=v20-boot uptime=22071s（~6.1h）boot_count=30 mqtt=True audio=False 正常。MD 桥今日全天无法写入，暂存内容待明早恢复后补录。

## 待写入 MD 桥的【云侧】内容（WSL 恢复后追加）

### 【云侧】2026-09-04 17:08
- 云侧 WSL Tailscale 断连（12:50 起，已超 4h），MD 桥写入暂停
- ESP32 设备全程在线稳定：fw=v20-boot，boot_count=30，MQTT ✅，audio=False
- 平台播报功能正常：play_audio / stop_audio 均可通过网页远程下发，端侧执行 ✅
- boot_announce 已验证：重启后自动播报日期+星期+版本号（40%音量）✅
- LCD 1602A 显示待人工肉眼确认（摄像头角度不足，需直视设备）
- 无需端侧任何操作，等 WSL 恢复后同步此记录即可

### 【云侧】2026-09-04 18:22
- WSL Tailscale 断连持续中（12:50 起，已超 5.5h），MD 桥仍无法写入
- ESP32 全程在线：fw=v20-boot，uptime=11267s（~3.1h），boot_count=30，MQTT ✅，audio=False
- 平台网页 play_audio / stop_audio 远程下发功能正常 ✅
- boot_announce 重启自动播报已验证 ✅
- LCD 1602A 显示待人工目视确认
- 请端侧检查 Windows Tailscale 客户端状态，恢复后云侧会补全所有缺失条目
