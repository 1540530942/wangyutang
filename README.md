# Wangyutang Platform Workspace

This directory gathers the projects deployed behind `110.40.154.41` and the `wangyutang` module domains.

## Projects

| Path | Role | Public route |
| --- | --- | --- |
| `robot_gateway/` | Lightweight public homepage and Caddy gateway for robot modules | `/` |
| `camera_snapshot/` | TurboPi / Raspberry Pi camera snapshot service | direct service port |
| `action_move/` | Robot motion API and TurboPi chassis/servo control wrapper | direct service port |
| `audio_recognition/` | Voice recognition and robot interaction service | direct service port |
| `pi5_robot/` | Raspberry Pi 5 patrol robot MVP console, simulation robotd/visiond/harnessd, and hardware integration scaffold | direct service port |
| `slam_mapping/` | Optional pose and occupancy-grid module for SLAM-style motion feedback | direct service port |
| `smile_face/` | Synchronized Wall-E-like robot expression screen for web and Raspberry Pi LCD kiosk display | direct service port |

The repository is now focused on robot-platform services. Retired portal, web-manager, remote-control, remote-sensing, paper-learning, and SLAM experiment modules have been removed from the active runtime. `robot_gateway/` keeps `https://www.wangyutang.cn/` as a lightweight robot-only entrypoint.

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
http://127.0.0.1/api/health         robot gateway
http://127.0.0.1/camera/            camera through gateway
http://127.0.0.1/action/            action through gateway
http://127.0.0.1:8099/              camera snapshot service
http://127.0.0.1:8094/              action move service
http://127.0.0.1:8095/              audio recognition service
http://127.0.0.1:8093/              Pi5 robot console
http://127.0.0.1:8301/              SLAM mapping service
http://127.0.0.1:8096/              Smile Face robot expression screen
```

## Validate

```powershell
.\scripts\check-local.ps1
```

The check script probes the active robot-platform service health endpoints when they are running.
