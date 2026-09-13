# 2026-09-02 WSL SSH 中断记录

## 现象
- 约 19:30（本地时间）ssh wsl (Tailscale IP 100.88.133.9) 开始连接超时
- 原因推测：Windows 休眠或 Tailscale 断连
- 持续时间：至少 11 小时（19:30 → 次日 06:30+），仍未恢复

## 影响
- 无法读写 `~/workspace/codex_project/codex_terminator/ESP32_WANGYUTANG_PLAN.md`
- ESP32 设备本身不受影响：v15-lcd 持续在线，uptime=457min+（7.6小时），mqtt=True，boot_count=18，heap 稳定
- MD 云侧巡检记录中断，端侧若有新回复亦无法确认
- Monitor 在断连前检测到 MD 新段落（section count 48→51），具体内容未能确认是否有新端侧回复

## 恢复操作
在 Windows 端运行任意一条：
```powershell
wsl
tailscale up
wsl --shutdown && wsl
```

## 后续
WSL 恢复后：
1. 读取 MD 确认是否有新端侧内容
2. 补写云侧巡检记录（缺失 19:30～恢复时段）
3. 检查 Monitor 检测到的新段落是云侧巡检还是真正的端侧新回复
