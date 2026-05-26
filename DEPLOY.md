# Tencent Cloud Deployment

This repository uses one standard deployment path for Tencent Cloud. The goal is to avoid ad-hoc server edits, stale containers, and unclear Compose entrypoints.

## Rule

```text
Any production change must land in source first.
Any production deployment must go through scripts/deploy_tencent.ps1.
Emergency docker cp fixes are temporary only and must be folded back into source and a normal release.
```

## Target

```text
Host: 110.40.154.41
SSH user: root
Default SSH alias: tencent
Server release root: /root/control_platform/releases
Server current link: /root/control_platform/current
Primary URLs:
  https://www.wangyutang.cn/
  https://www.wangyutang.cn/camera/
  http://110.40.154.41/
  http://110.40.154.41/camera/
```

## Standard Flow

From the repository root on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy_tencent.ps1
```

For camera-only changes, use the narrower camera release path:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy_camera_snapshot.ps1
```

This updates both sides of the camera chain: the Tencent Cloud `camera-snapshot` container and the Raspberry Pi `camera-snapshot-sender.service`.

The deployment script performs these steps:

```text
1. Check git status and record the current commit.
2. Run local syntax/config checks.
3. Build Docker images with docker compose build.
4. Export the production images with docker save.
5. Create a timestamped release directory under dist\tencent_releases.
6. Copy docker-compose.yml, .env example files, Caddyfile, registry, scripts, and image archives into the release.
7. Upload the release to /root/control_platform/releases/<timestamp>.
8. Run scripts/tencent_apply_release.sh on the server.
9. Verify public URLs.
10. Write docs/logs/<date>-tencent-deploy-<timestamp>.md.
```

The camera-only script performs these extra checks:

```text
1. Compile camera_snapshot/server.py and camera_snapshot/pi_camera_sender.py.
2. Upload the exact source files to Tencent Cloud and rebuild camera-snapshot:local.
3. Restart only the camera-snapshot container.
4. Install the Raspberry Pi sender and systemd unit from this repository.
5. Verify the public page contains 摄像机截图, 屏幕截图, 表情截图.
6. Trigger all three modes and require these sources:
   single -> opencv
   screenshot -> screenshot
   face -> face-screenshot
```

## Emergency Fix Policy

Emergency fixes may use `scp`, `docker cp`, or `docker commit` only when the site is already broken and the fix must be immediate.

After an emergency fix:

```text
1. Copy the same change into the repository.
2. Run scripts/deploy_tencent.ps1.
3. Confirm the server image labels and release directory match the source change.
4. Record the emergency in docs/logs.
```

## Useful Options

Package and upload without rebuilding:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy_tencent.ps1 -SkipBuild
```

Create a local release package only:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy_tencent.ps1 -SkipUpload -SkipRemoteApply -SkipVerify
```

Upload an already-created release:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy_tencent.ps1 -ReleaseId 20260507-153000 -SkipBuild
```

## Rollback

On the server:

```bash
cd /root/control_platform
ln -sfn /root/control_platform/releases/<previous-release> current
cd current
docker compose --env-file .env -f docker-compose.yml up -d --no-build
```

Then verify:

```bash
curl -fsS http://127.0.0.1/api/health
curl -fsS http://127.0.0.1/camera/api/health
```

## Notes

- Tencent Cloud SSH may close rapid repeated connections. Wait 45 to 120 seconds before retrying.
- Docker Hub access from the server has been unreliable, so releases include local image archives and use `docker load`.
- The server may keep historical image tags such as `control-platform:deployed-<timestamp>` for emergency recovery, but the normal runtime tags are the `:local` names used by Compose.
- The Raspberry Pi camera sender must be managed by systemd only. Do not start manual `nohup python3 pi_camera_sender.py ...` copies; the sender has a lock file to reject duplicate instances.
