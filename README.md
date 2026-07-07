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
| `common_api_manager/` | Public workbench (Model Studio, Robot Skills) + platform ASR/TTS common API | `/common/` | host:8101 |

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
