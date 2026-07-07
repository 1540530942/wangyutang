# 迁移记录：audio_recognition → robot_sandbox（含网关路由改名）

状态：**已完成并生产验证**
时间线：2026-07-07 ~ 2026-07-08

本文件记录两次相关联的重命名迁移，供追溯与回滚参考。历史 `docs/logs/`、
`docs/incidents/` 中旧名的记录**不改写**（保留当时真实语境）。

## 1. 背景与动机

`audio_recognition` 模块在音频拆分（见
[audio-interact-robot-sandbox-implementation-plan.md](audio-interact-robot-sandbox-implementation-plan.md)）
后已**不再做音频识别**——音频 I/O 全部迁到 `audio_interact`。它现在的职责是：

```
文本 → tool_calls → 校验 → 安全 → 执行 → diagnostics → tts_text
```

名字名不副实，故重命名为 `robot_sandbox`。随后进一步把网关路由也对齐到模块名，
并退役历史 `/audio`、`/interact` 路由，清理债务。

## 2. 迭代过程（分两步交付）

### 阶段 A：模块重命名（2026-07-07）

`audio_recognition` → `robot_sandbox`（包目录 / 容器 / 镜像 / 卷 / 全部 import）。

| 层面 | 变更 |
|---|---|
| Python 包 | 目录 + 55 文件 import；`git mv` 保留历史 |
| compose | 服务/容器/镜像/构建路径 → `robot-sandbox`；卷 `audio_recognition_data` → `robot_sandbox_data` |
| 跨服务 env | `AUDIO_RECOGNITION_URL` → `ROBOT_SANDBOX_URL`（audio_interact 保留旧名回退） |
| Caddy | `/audio/*` 上游 → `robot-sandbox:8095`（**当时保留 `/audio/*` 路径兼容**） |
| CI | 全部引用改名；加一次性 `docker rm -f audio-recognition` 释放 8095 端口 |

阶段 A 保留 `/audio/*` 作兼容别名（设备/CI 依赖）。

### 阶段 B：路由退役（2026-07-08）

把网关路由也对齐模块名，**移除**历史路由（不再兼容）。

| 旧路由（已 404 退役） | 新路由 | 后端 |
|---|---|---|
| `/audio/*` | `/robot_sandbox/*` | robot-sandbox:8095 |
| `/interact/*` | `/audio_interact/*` | audio-interact:8097 |

同步迁移的所有硬编码引用：

- `robot_sandbox/web/server.py`：`audio_url` 由 `/audio/api/audio/` → `/robot_sandbox/api/audio/`
- 仪表盘 `app.js` / `index.html`：VAD WebSocket + 健康引用 → `/audio_interact/*`
- CI workflow：版本断言（`/robot_sandbox/api/recognize-text`、`/robot_sandbox/api/envelopes/latest`）+ 公网网关检查（`/audio_interact/api/health`）
- `audio_interact/edge_streamer.py`、`edge/config.example.json`、`config.example.json` → `/audio_interact/ws/audio`
- `robot_gateway/tests/test_caddyfile.py`、`README.md` 模块图
- **实车 Pi（turbopi-01）** `~/audio_interact_edge/config.json`：`server` 由 `/interact` → `/audio_interact`（备份 `config.json.bak`）
- `common_api_manager/doc/*`：旧 `/audio/api/*` 参考 → `/robot_sandbox/api/*`；旧 ASR 兼容入口 `/audio/api/asr/transcribe` 退役，规范入口为 `/common/api/asr/transcribe`

### 关键前置判断（为何 B 可安全一步到位）

- Pi 语音监听器 `wonderecho_listener` **当前未常驻运行**（无 systemd 服务）→ 退役 `/interact` 无活跃中断
- 可 `ssh pi@raspberrypi` 直连改 Pi 配置（Tailscale 浏览器认证仅 Tang 中转时需要）

## 3. 验证结果（真实，非构造）

| 验证项 | 结果 |
|---|---|
| 16 模块测试套件 | 245 passed / 1 skipped / 33 subtests（改名后重跑） |
| `/robot_sandbox/api/health`、`/audio_interact/api/health` | 200 |
| 旧 `/audio`、`/interact` | 404（已退役） |
| A1 dry_run 契约（`/robot_sandbox`） | PASS，skill=move_forward |
| C1 静音段（`/audio_interact`） | PASS，text="" command=None |
| 实车 move_forward（`/robot_sandbox`） | status=complete，sonar 实时（55/23/113cm 随场景变化），tts="好的，往前走" |
| 相机确认 | 实时帧时间戳同步，画面与声纳距离一致 |

## 4. 兼容与回滚

- **兼容保留**：`audio_interact` 仍识别旧 `AUDIO_RECOGNITION_URL` 环境变量（回退）。
- **公网路由无兼容**：`/audio`、`/interact` 已彻底移除，旧客户端必须改用新路由。
- **回滚**：`git revert` 对应提交即可恢复路由与包名；Pi 端 `cp config.json.bak config.json` 还原 `/interact`。数据卷 `audio_recognition_data`（旧）仍在 Tang 上（orphaned），未删除。

## 5. 相关提交

- 阶段 A：`Rename module audio_recognition -> robot_sandbox (global consistency)`
- 阶段 B：`Retire legacy /audio and /interact routes; use module-name routes`
