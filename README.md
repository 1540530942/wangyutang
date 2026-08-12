# Wangyutang Platform Workspace

This directory gathers the robot-platform services deployed behind `110.40.154.41`
and the `www.wangyutang.cn` gateway. Every module owns its own README; this file is
the top-level map.

## Deployed services (`docker-compose.yml`)

These build and run as containers and are reachable through the Caddy gateway.

| Path | Role | Public route | Port |
| --- | --- | --- | --- |
| `robot_gateway/` | Caddy gateway + lightweight public homepage | `/` | 80/443 |
| `camera_snapshot/` | TurboPi / Raspberry Pi camera + screenshot snapshot service | `/camera/` | 8099 |
| `action_move/` | Robot motion API and TurboPi chassis/servo control wrapper | `/action/` | 8094 |
| `robot_sandbox/` | robot_sandbox: text → tool_calls → validation → safety → execution → tts_text | `/robot_sandbox/` | 8095 |
| `audio_interact/` | Audio I/O adapter: VAD/ASR/wake-state/TTS, settings, `/api/audio/segment` | `/audio_interact/` | 8097 |
| `pi5_robot/` | Raspberry Pi 5 patrol robot console + simulation robotd/visiond/harnessd | `/robot/` | 8093 |
| `slam_mapping/` | Pose and occupancy-grid module for SLAM-style motion feedback | `/slam/` | 8301 |
| `smile_face/` | Wall-E-like synchronized robot expression screen (web + Pi LCD kiosk) | `/face/` | 8096 |

`audio_interact` and `robot_sandbox` are the two halves of the voice pipeline:
audio I/O lives in `audio_interact`, robot command planning/execution in
`robot_sandbox`. See
[docs/audio-interact-robot-sandbox-design.md](docs/audio-interact-robot-sandbox-design.md).

## Host service (on the gateway, not in compose)

| Path | Role | Public route | Port |
| --- | --- | --- | --- |
| `common_api_manager/` | Public workbench (Model Studio, Robot Skills, 音频转换) + platform ASR/TTS common API | `/common/` | host:8101 |

## Web pages (可打开使用的网页)

All under `https://www.wangyutang.cn`. Each page and what it does:

| 页面 | 地址 | 功能说明 |
| --- | --- | --- |
| **首页 / 网关** | `/` | 机器人平台入口主页（robot_gateway 静态站）。 |
| **相机快照** | `/camera/` | 查看 TurboPi 实时相机帧与截图、触发抓拍、控制相机。 |
| **动作控制** | `/action/` | 底盘/舵机动作控制界面，直接下发 move/turn/look 等动作，含播报音量设置。 |
| **robot_sandbox 控制台** | `/robot_sandbox/` | **纯文本指令**入口：输入“前进/左转”等文本 → 指令规划 → 校验 → 安全（front_distance 前置）→ 执行 → TTS 播报；右侧看识别历史、流水线阶段事件、envelope、相机预览、清零任务。**不含音频**（语音请用下面的 audio_interact）。 |
| **audio_interact 语音输入台** | `/audio_interact/` | **语音输入**入口，三种模式：① **WonderEchoPro**——把输入模式设为 WonderEchoPro，树莓派硬件麦克风定长录音上传、本地扬声器播报；② **浏览器麦克风/扬声器**——用当前浏览器录音（4s）或上传 WAV → ASR → 唤醒词判断 → robot_sandbox 指令 → 浏览器扬声器播 TTS；③ **VAD_ASR 测试**——浏览器持续发 16k PCM，云端 Silero VAD 流式切分 + ASR + 唤醒，带实时 VAD 电平条。识别文本/唤醒状态/命中技能/TTS 实时显示。 |
| **公共工作台** | `/common/` | common_api_manager 公共能力入口页。 |
| **Model Studio** | `/common/model-studio` | 模型目录 + 每模型真实推理校验（点“校验”跑真实推理，非硬编码徽章）。 |
| **机器人技能** | `/common/robot-skills` | 技能面板（原 function_center 继任页），调用 `/action/*`、`/camera/*`。 |
| **音频转换** | `/common/audio-convert` | 上传 m4a/mp3/wav 等录音，转成 16kHz 单声道 ASR 输入音频（wav/opus/mp3），可按静音自动切片；结果可网页下载或经 `/common/api/audio/convert/*` 接口取回。 |
| **Pi5 控制台** | `/robot/` | 树莓派5 巡逻机器人 MVP 控制台。 |
| **SLAM 地图** | `/slam/` | 位姿 / 占据栅格地图反馈。 |
| **表情屏** | `/face/` | Wall-E 风格机器人表情屏（网页 + 树莓派 LCD kiosk）。 |

分工要点：**`/robot_sandbox/` = 文本指令**，**`/audio_interact/` = 音频（WonderEchoPro + 浏览器麦克风/扬声器 + VAD_ASR）**。两页共用同一套指令/唤醒/执行链路。路由改名历史见 [docs/migration-robot_sandbox-routes.md](docs/migration-robot_sandbox-routes.md)。

## Auxiliary modules and tooling (not deployed as services)

| Path | Role |
| --- | --- |
| `common_sense/` | Cross-cutting knowledge and topology reference docs (not owned by any single service) |
| `loop_engineering/` | Loop eval framework for the voice recognition → skill routing → execution chain |
| `pi5_monitor/` | Raspberry Pi shutdown / disconnect forensics (who/what triggered a power loss) |
| `pose_tracker/` | Real-time 2D pose tracking (IMU yaw + `/cmd_vel` dead-reckoning) streamed over WebSocket |
| `simulation/` | TurboPi full simulation server exposing Pi-compatible HTTP for hardware-free testing |
| `smoke/` | Must-pass smoke tests for skill routing that run without LLM calls |
| `scripts/` | Deployment and local-check scripts (see [scripts/README.md](scripts/README.md)) |
| `docs/` | Cross-cutting design docs, runbooks, logs, and incident records (see [docs/README.md](docs/README.md)) |

The repository is focused on robot-platform services. Retired portal, web-manager,
remote-control, remote-sensing, paper-learning, and standalone SLAM experiment
modules have been removed from the active runtime. The `function_center` module was
retired and replaced by `/common/robot-skills` (see
[common_api_manager/doc/function-center-migration.md](common_api_manager/doc/function-center-migration.md)).

## Module merge guide

Before adding or changing any module, follow [MODULE_MERGE_GUIDE.md](MODULE_MERGE_GUIDE.md).
Every module merge must record the change and state which merge criteria are satisfied.
Current compliance is tracked in [docs/module-compliance-audit.md](docs/module-compliance-audit.md).

## Deployment

Production deploys run through GitHub Actions (`.github/workflows/deploy-modules.yml`)
to Tencent Cloud on push to `main`/`master`/`feature/**`. See [DEPLOY.md](DEPLOY.md).

## Run locally

Create the local env file once:

```powershell
Copy-Item .env.example .env
```

Start everything:

```powershell
docker compose up --build
```

Docker Desktop or another Docker daemon must be running first.

Useful local URLs:

```text
http://127.0.0.1/api/health         robot gateway
http://127.0.0.1/camera/            camera through gateway
http://127.0.0.1/action/            action through gateway
http://127.0.0.1:8099/              camera snapshot service
http://127.0.0.1:8094/              action move service
http://127.0.0.1:8095/              robot_sandbox service (text→tool_calls→execute)
http://127.0.0.1:8097/              audio interact service
http://127.0.0.1:8093/              Pi5 robot console
http://127.0.0.1:8301/              SLAM mapping service
http://127.0.0.1:8096/              Smile Face robot expression screen
```

## Validate

```powershell
.\scripts\check-local.ps1
```

The check script probes the active robot-platform service health endpoints when they are running.
