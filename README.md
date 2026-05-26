# Wangyutang Platform Workspace

This directory gathers the projects deployed behind `110.40.154.41` and the `wangyutang` module domains.

## Projects

| Path | Role | Public route |
| --- | --- | --- |
| `control_platform/` | Unified module portal and Caddy config | `/` |
| `paper_learning_system/` | Paper learning and Hermes gateway | `/papers/` |
| `remote_control_cloud/` | Remote-control Vite UI, FastAPI command API, and MQTT config | `/remote/` |
| `remote_control_edge/` | PC/Arduino bridge, MQTT serial bridge, and PlatformIO firmware | part of `/remote/`, local hardware side |
| `remote_sensing/` | Executable scaffold for the remote-sensing module | `/sensing/` |
| `camera_snapshot/` | TurboPi / Raspberry Pi camera snapshot service | `/camera/` |
| `llm_manager/` | Web Manager dashboard for module webpage status, development progress, and large-model API management | `/web/`, `/llm/` |
| `pi5_robot/` | Raspberry Pi 5 patrol robot MVP console, simulation robotd/visiond/harnessd, and hardware integration scaffold | `/robot/` |
| `smile_face/` | Synchronized Wall-E-like robot expression screen for web and Raspberry Pi LCD kiosk display | `/face/` |
| `pi_slam/` | Raspberry Pi / robot SLAM research placeholder; no runtime service yet | `/modules/` extension entry |

The path routes above are served from both gateway hosts:

```text
https://www.wangyutang.cn/<path>
http://110.40.154.41/<path>
```

The original source directories were left in place. This workspace is the consolidated copy to use for deployment and day-to-day management.

## Module Merge Guide

Before adding or changing any module, follow [MODULE_MERGE_GUIDE.md](MODULE_MERGE_GUIDE.md). Every module merge must record the change and explicitly state which merge criteria have been satisfied.

Current module compliance is recorded in [docs/module-compliance-audit.md](docs/module-compliance-audit.md).

## Run

Create the local env file once:

```powershell
Copy-Item .env.example .env
```

Start everything:

```powershell
docker compose up --build
```

Docker Desktop or another Docker daemon must be running before this command can start the containers.

Useful local URLs:

```text
http://127.0.0.1:8098/              control platform
http://127.0.0.1:8088/              paper learning system
http://127.0.0.1:5173/              remote-control web UI
http://127.0.0.1:8000/api/health    remote-control API
http://127.0.0.1:8090/              remote-sensing scaffold
http://127.0.0.1:8099/              camera snapshot service
http://127.0.0.1:8092/              Web Manager
http://127.0.0.1:8093/              Pi5 robot console
http://127.0.0.1:8096/              Smile Face robot expression screen
http://127.0.0.1/api/health         Caddy gateway to platform, when port 80 is available
```

## Validate

```powershell
.\scripts\check-local.ps1
```

The check script probes the platform, paper, Hermes, and camera health endpoints when they are running.
