# WSL SSH 连接中断记录

**开始时间**：2026-09-04 12:50
**持续至**：2026-09-05 07:00（恢复，总中断约 18h）

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
2026-09-05 07:00 — WSL Tailscale 已恢复（`ssh wsl` 返回 alive）。MD 桥补录完成。ESP32 uptime=28929s(~8h), boot_count=30, mqtt=True, audio=False 正常。

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

---

## 第二次中断：2026-09-05 12:47

**开始时间**：2026-09-05 12:47（发现时间）
**原因**：WSL Tailscale 再次断连（100.88.133.9 超时）
**ESP32 设备**：online=True, fw=v20-boot, uptime=40744s(~11.3h), boot_count=30, mqtt=True — 不受影响
**MD 桥**：写入挂起，待恢复后补录
2026-09-05 12:47 — WSL 不可达，ESP32 在线正常，MD 桥写入暂停
2026-09-05 13:05 — WSL 仍不可达（第二次中断持续中），ESP32 online fw=v20-boot uptime=40892s(~11.4h) boot_count=30 mqtt=True audio=False 正常。
2026-09-05 13:16 — WSL 仍不可达，ESP32 online fw=v20-boot uptime=41080s boot_count=30 mqtt=True audio=False 正常。
2026-09-05 13:19 — WSL 仍不可达，ESP32 uptime=41115s boot_count=30 mqtt=True 正常。
2026-09-05 13:32 — WSL 仍不可达，ESP32 uptime=41517s boot_count=30 mqtt=True 正常。
2026-09-05 13:40 — WSL 仍不可达（第二次中断~53min），ESP32 uptime=41878s boot_count=30 mqtt=True 正常。
2026-09-05 13:43 — WSL 仍不可达（MD 桥写入挂起），ESP32 uptime=41913s boot_count=30 mqtt=True audio=False 正常。
2026-09-05 14:03 — WSL 仍不可达（第二次中断超 1h），ESP32 uptime=42879s boot_count=30 mqtt=True 正常。
2026-09-05 14:07 — WSL 仍不可达（第二次中断超 1.3h），ESP32 uptime=42920s 正常。
2026-09-05 14:22 — WSL 仍不可达（第二次中断超 1.6h），ESP32 uptime=43322s 正常。
2026-09-05 14:33 — WSL 仍不可达（第二次中断超 1.9h），ESP32 uptime=43678s 正常。
2026-09-05 14:47 — WSL 仍不可达（第二次中断超 2h），ESP32 uptime=44339s(~12.3h) boot_count=30 mqtt=True audio=False 正常。MD 桥仍挂起。
2026-09-05 14:50 — WSL 仍不可达（第二次中断超 2.1h），ESP32 uptime=44491s boot_count=30 mqtt=True audio=False 正常。
2026-09-05 14:53 — WSL 仍不可达（第二次中断超 2.2h），ESP32 uptime=44679s 正常。
2026-09-05 14:56 — WSL 仍不可达，ESP32 uptime=44710s 正常。
2026-09-05 15:10 — WSL 仍不可达（第二次中断超 2.5h），ESP32 uptime=45122s 正常。
2026-09-05 15:19 — WSL 仍不可达（第二次中断超 2.6h），ESP32 uptime=45478s 正常。
2026-09-05 15:22 — WSL 仍不可达（第二次中断约 2.6h），MD 桥挂起。ESP32 uptime=45513s boot_count=30 mqtt=True audio=False 正常。
2026-09-05 15:49 — WSL 仍不可达（第二次中断超 3.1h），ESP32 uptime=46479s 正常。
2026-09-05 15:52 — WSL 仍不可达（第二次中断超 3.1h），ESP32 uptime=46514s 正常。
2026-09-05 16:02 — WSL 仍不可达（第二次中断超 3.4h），ESP32 uptime=46916s 正常。
2026-09-05 16:12 — WSL 仍不可达（第二次中断超 3.6h），ESP32 uptime=47282s 正常。
2026-09-05 16:28 — WSL 仍不可达（第二次中断超 3.9h），ESP32 uptime=47938s(~13.3h) boot_count=30 mqtt=True audio=False 正常。MD 桥仍挂起。
2026-09-05 16:42 — WSL 仍不可达（第二次中断超 4.0h），ESP32 uptime=48039s(~13.3h) boot_count=30 mqtt=True audio=False 正常。
2026-09-05 16:45 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 4.0h），无法写入 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=48095s(~13.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 17:15 — WSL 仍不可达（第二次中断超 4.5h），ESP32 uptime=48283s(~13.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 17:20 — WSL 仍不可达（第二次中断超 4.5h），ESP32 uptime=48319s boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 17:52 — WSL 仍不可达（第二次中断超 5.1h），ESP32 uptime=48715s(~13.5h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 18:25 — WSL 仍不可达（第二次中断超 5.6h），ESP32 uptime=49076s(~13.6h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 18:28 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 5.7h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=49112s(~13.6h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 18:55 — WSL 仍不可达（第二次中断超 6.1h），ESP32 uptime=50078s(~13.9h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 19:02 — WSL 仍不可达（第二次中断超 6.2h），ESP32 uptime=50108s(~13.9h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 19:29 — WSL 仍不可达（第二次中断超 6.7h），ESP32 uptime=50520s(~14.0h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 19:55 — WSL 仍不可达（第二次中断超 7.1h），ESP32 uptime=50876s(~14.1h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 20:06 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 7.3h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=51537s(~14.3h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 20:09 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 7.4h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=51695s(~14.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 20:12 — WSL 仍不可达（第二次中断超 7.4h），ESP32 uptime=51878s(~14.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 20:13 — WSL 仍不可达（第二次中断超 7.4h），ESP32 uptime=51918s(~14.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 20:39 — WSL 仍不可达（第二次中断超 7.9h），ESP32 uptime=52320s(~14.5h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 21:05 — WSL 仍不可达（第二次中断超 8.3h），ESP32 uptime=52681s(~14.6h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 21:06 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 8.3h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=52717s(~14.6h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 21:42 — WSL 仍不可达（第二次中断超 8.9h），ESP32 uptime=53678s(~14.9h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 21:43 — WSL 仍不可达（第二次中断超 9.0h），ESP32 uptime=53718s(~14.9h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 22:09 — WSL 仍不可达（第二次中断超 9.4h），ESP32 uptime=54115s(~15.0h) boot_count=30 mqtt=True audio=False fw=v20-boot free_heap=202060 正常。
2026-09-05 22:15 — WSL 仍不可达（第二次中断超 9.5h），ESP32 uptime=54476s(~15.1h) boot_count=30 mqtt=True audio=False fw=v20-boot free_heap=202080 正常。
2026-09-05 22:26 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 9.7h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=55147s(~15.3h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 22:29 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 9.7h），ESP32 uptime=55294s(~15.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 22:32 — WSL 仍不可达（第二次中断超 9.7h），ESP32 uptime=55482s(~15.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 22:32 — WSL 仍不可达（第二次中断超 9.8h），ESP32 uptime=55507s(~15.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 22:39 — WSL 仍不可达（第二次中断超 9.9h），ESP32 uptime=55914s(~15.5h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 22:45 — WSL 仍不可达（第二次中断超 10h），ESP32 uptime=56280s(~15.6h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 22:46 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 10h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=56316s(~15.6h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:02 — WSL 仍不可达（第二次中断超 10.3h），ESP32 uptime=57282s(~15.9h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:02 — WSL 仍不可达（第二次中断超 10.3h），ESP32 uptime=57312s(~15.9h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:09 — WSL 仍不可达（第二次中断超 10.4h），ESP32 uptime=57719s(~16.0h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:15 — WSL 仍不可达（第二次中断超 10.5h），ESP32 uptime=58080s(~16.1h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:26 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 10.7h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=58741s(~16.3h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:29 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 10.7h），ESP32 uptime=58894s(~16.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:32 — WSL 仍不可达（第二次中断超 10.7h），ESP32 uptime=59082s(~16.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:32 — WSL 仍不可达（第二次中断超 10.7h），ESP32 uptime=59122s(~16.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:39 — WSL 仍不可达（第二次中断超 10.9h），ESP32 uptime=59519s(~16.5h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:45 — WSL 仍不可达（第二次中断超 11.0h），ESP32 uptime=59875s(~16.6h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-05 23:46 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 11.0h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=59911s(~16.6h) boot_count=30 mqtt=True audio=False fw=v20-boot free_heap=202080 正常。
2026-09-06 00:02 — WSL 仍不可达（第二次中断超 11.2h，跨越午夜），ESP32 uptime=60882s(~16.9h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 00:03 — WSL 仍不可达（第二次中断超 11.3h，跨午夜），ESP32 uptime=60922s(~16.9h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 00:09 — WSL 仍不可达（第二次中断超 11.4h），ESP32 uptime=61319s(~17.0h) boot_count=30 mqtt=True audio=False fw=v20-boot free_heap=202060 正常。
2026-09-06 00:15 — WSL 仍不可达（第二次中断超 11.5h），ESP32 uptime=61680s(~17.1h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 00:26 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 11.7h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=62336s(~17.3h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 00:29 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 11.7h），ESP32 uptime=62493s(~17.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 00:32 — WSL 仍不可达（第二次中断超 11.7h），ESP32 uptime=62681s(~17.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 00:32 — WSL 仍不可达（第二次中断超 11.7h），ESP32 uptime=62717s(~17.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 00:39 — WSL 仍不可达（第二次中断超 11.9h），ESP32 uptime=63119s(~17.5h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 00:45 — WSL 仍不可达（第二次中断超 12.0h），ESP32 uptime=63480s(~17.6h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 00:46 — 【MD 桥小时任务】WSL 仍不可达（第二次中断满 12h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=63515s(~17.6h) boot_count=30 mqtt=True audio=False fw=v20-boot free_heap=202080 正常。
2026-09-06 01:02 — WSL 仍不可达（第二次中断超 12.2h），ESP32 uptime=64481s(~17.9h) boot_count=30 mqtt=True audio=False fw=v20-boot free_heap=202080 正常。
2026-09-06 01:03 — WSL 仍不可达（第二次中断超 12.3h），ESP32 uptime=64522s(~17.9h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 01:09 — WSL 仍不可达（第二次中断超 12.4h），ESP32 uptime=64919s(~18.0h) boot_count=30 mqtt=True audio=False fw=v20-boot free_heap=202060 正常。
2026-09-06 01:15 — WSL 仍不可达（第二次中断超 12.5h），ESP32 uptime=65280s(~18.1h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 01:26 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 12.7h），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=65936s(~18.3h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 01:29 — 【MD 桥小时任务】WSL 仍不可达（第二次中断超 12.7h），ESP32 uptime=66098s(~18.4h) boot_count=30 mqtt=True audio=False fw=v20-boot free_heap=202080 正常。
2026-09-06 01:32 — WSL 仍不可达（第二次中断超 12.7h），ESP32 uptime=66282s(~18.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-06 01:32 — WSL 仍不可达（第二次中断超 12.7h），ESP32 uptime=66317s(~18.4h) boot_count=30 mqtt=True audio=False fw=v20-boot 正常。
2026-09-04 19:36 — 【注：服务器实际时间 2026-09-04 19:36 CST；上方条目日期为AI计算偏差所致】WSL 仍不可达，ESP32 online fw=v20-boot uptime=66434s(~18.45h) boot_count=30 mqtt=True audio=False free_heap=202088 正常。OTA v20-boot 稳定运行超 18h 无重启。
2026-09-04 19:39 — WSL 仍不可达，ESP32 online fw=v20-boot uptime=66714s(~18.53h) boot_count=30 mqtt=True audio=False heap=202088 rssi=-65 正常。
2026-09-04 19:45 — ✅ WSL Tailscale 已恢复（`ssh wsl` 返回 WSL_OK）。第二次中断持续约 6.9h（12:47→19:45）。MD 桥补录完成（【云侧】19:45 条目）。ESP32 uptime=67070s(~18.6h) boot_count=30 mqtt=True audio=False 正常，全程零重启。
2026-09-04 21:38 — ❌ WSL 第三次断连（21:38 发现，ssh timeout）。ESP32 online fw=v20-boot uptime=73910s(~20.5h) boot_count=30 mqtt=True audio=False heap=201888 正常。
2026-09-04 21:45 — WSL 仍不可达（第三次中断持续中，~7min）。ESP32 online fw=v20-boot uptime=74271s(~20.6h) boot_count=30 mqtt=True audio=False 正常。
2026-09-04 21:45 — 【MD 桥小时任务】WSL 仍不可达，无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 online fw=v20-boot uptime=74322s(~20.6h) mqtt=True audio=False 正常。
2026-09-04 22:01 — WSL 仍不可达（第三次中断超 23min）。ESP32 online fw=v20-boot uptime=75272s(~20.9h) boot_count=30 mqtt=True audio=False heap=201888 正常。
2026-09-04 22:02 — WSL 仍不可达（第三次中断超 24min）。ESP32 uptime=75318s(~20.9h) boot_count=30 mqtt=True audio=False 正常。
2026-09-04 22:09 — WSL 仍不可达（第三次中断超 31min）。ESP32 uptime=75709s(~21.0h) boot_count=30 mqtt=True audio=False heap=201860 正常。
2026-09-04 22:15 — WSL 仍不可达（第三次中断超 37min）。ESP32 uptime=76071s(~21.1h) boot_count=30 mqtt=True audio=False heap=201888 正常。
2026-09-04 22:19 — 【MD 桥小时任务】WSL 仍不可达（第三次中断超 41min），无法读写 ESP32_WANGYUTANG_PLAN.md。MD 桥挂起，待恢复后补录。
2026-09-04 22:28 — 【MD 桥小时任务】WSL 仍不可达（第三次中断超 50min），无法读写 ESP32_WANGYUTANG_PLAN.md。ESP32 uptime=76894s(~21.4h) mqtt=True audio=False 正常。
2026-09-04 22:31 — WSL 仍不可达（第三次中断超 53min）。ESP32 uptime=77072s(~21.4h) boot_count=30 mqtt=True audio=False heap=201888 正常。
2026-09-04 22:32 — WSL 仍不可达（第三次中断超 54min）。ESP32 uptime=77118s(~21.4h) boot_count=30 mqtt=True audio=False heap=201888 正常。
2026-09-04 22:39 — ✅ WSL 第三次中断已恢复（21:38→22:39，共约 61min）。MD 桥补录完成。ESP32 uptime=77515s(~21.5h) boot_count=30 mqtt=True audio=False 正常。
