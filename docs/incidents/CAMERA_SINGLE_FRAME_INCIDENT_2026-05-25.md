# Camera single-frame incident, 2026-05-25

## Summary

2026-05-25 晚上排查 `https://www.wangyutang.cn/camera/` 点击“单帧发送”后没有真实摄像头画面的问题。

最终确认：云端页面、`/api/capture`、`/api/latest`、`/api/latest.jpg` 这条链路本身可用；真正导致“没有摄像头图形”的原因在树莓派端取帧后端不稳定。`picamera2` 和 `rpicam-still/libcamera-still` 在当前 Pi 环境中会卡住或失败，页面看到的“新图”有时其实是截图回退，不是真实摄像头画面。

当前可工作的临时修复是：树莓派 `camera-snapshot-sender.service` 固定使用 OpenCV 后端：

```bash
/usr/bin/python3 /home/pi/pi_camera_sender.py \
  --server https://www.wangyutang.cn/camera \
  --device-id turbopi-01 \
  --backend opencv
```

验证过的成功结果：

- 单帧任务：`1779725154119-5616b3`
- `status`: `complete`
- `uploaded_frames`: `1`
- `capture_source`: `opencv`
- 最新 JPEG 大小：`23799` bytes
- 下载查看后确认为真实摄像头画面，不是桌面截图

## User-visible symptoms

1. 打开 `https://www.wangyutang.cn/camera/` 可以看到页面。
2. 点击“单帧发送”后，页面没有出现期望的摄像头画面。
3. 某些时候接口显示任务完成，但图片内容是树莓派桌面上的表情页面，左下角水印是 `Screenshot ...`。
4. 某些时候任务直接 `expired`，`uploaded_frames=0`。

## Button meanings

页面上几个按钮的含义不要混用：

- `单帧发送`：下发 `mode=single`。目标是让树莓派采集一帧真实摄像头画面并上传。
- `截图上传`：下发 `mode=screenshot`。目标是上传树莓派当前桌面截图，不代表摄像头工作正常。
- `巡检快照` / `远程快照`：下发 `mode=inspect`。它会上传一张快照，同时调用 `upload_inspection()` 上报远程巡检信息，包括主机名、温度、欠压/降频状态、Wi-Fi、IP、网关、磁盘、sender 服务状态和 load average。
- `持续发送`：下发 `mode=continuous`。按页面选择的频率持续上传摄像头帧，直到停止。

因此，如果“远程巡检”面板为空，应点击 `巡检快照` / `远程快照`，而不是 `单帧发送`。`单帧发送` 只负责摄像头帧，不会刷新巡检面板。

## What was not the root cause

这些都曾被排查过，但不是核心根因：

- Caddy 路由不是根因：`/camera/`、`/camera/api/health`、`/camera/api/latest`、`/camera/api/latest.jpg` 均可返回。
- 云端任务创建不是根因：`POST /camera/api/capture` 能创建任务。
- JPEG 图片接口不是根因：`/camera/api/latest.jpg` 能返回 `image/jpeg`。
- 前端静态文件不是根因：`/camera/static/app.js` 能加载。
- 树莓派不是完全离线：日志中能看到它轮询任务和上报 GPIO。

## Actual root cause

树莓派端取真实摄像头帧的后端不稳定。

### 1. `web_video_server` path unavailable

默认 `web_video_server` snapshot URL：

```text
http://127.0.0.1:8080/snapshot?topic=/image_raw
```

实际表现：

- `/image_raw` 没有稳定 publisher。
- snapshot 请求会 read timeout。

因此 `web-video-server` 后端不可用。

### 2. `picamera2` can detect camera but hangs/fails

日志中可见相机能被 libcamera/Picamera2 识别：

```text
INFO Camera camera_manager.cpp:326 libcamera v0.5.0+59-d83ff0a4
INFO Camera camera.cpp:1205 configuring streams: (0) 640x480-YUYV
[INFO] camera backend: picamera2
```

但随后没有成功上传，进程会卡住或抛过类似错误：

```text
[WARN] camera capture unavailable, falling back to screenshot: 'YUYV'
```

说明问题不是“完全没有相机硬件”，而是当前 Picamera2/格式处理/驱动状态组合不可靠。即使把脚本改成 `RGB888` 和 `capture_array()` fallback，现场仍可能卡在底层采集阶段。

### 3. `rpicam-still/libcamera-still` also unstable in this environment

为绕过 Picamera2，发送脚本曾加入 `rpicam-still` 后端，并把 `--timeout` 从错误的 `1` 调整为 `1000` 毫秒。但在实车 Pi 上测试时，sender 仍然会在任务创建后停止心跳，任务过期：

```text
status: expired
uploaded_frames: 0
device online: false after task start
```

这说明 `rpicam-still/libcamera-still` 在当前状态下同样可能阻塞。

### 4. OpenCV backend works, but needs careful lifecycle

OpenCV 后端曾成功拿到真实摄像头画面：

```text
[INFO] opencv camera index: 0
[INFO] camera backend: opencv
[ OK ] uploaded task ... frame 1/1
```

最终成功验证：

```text
task=1779725154119-5616b3
status=complete
uploaded_frames=1
capture_source=opencv
content_length=23799
```

下载 `latest.jpg` 后确认画面是真实摄像头画面。

因此当前策略是固定 `--backend opencv`，并且单帧拍完释放 camera handle，降低后续任务读帧失败概率。

## Important side incident during deployment

排查过程中曾错误使用 `docker compose ... up --force-recreate camera-snapshot`，实际影响超出 `camera-snapshot`，导致以下独立容器被停掉/移除：

- `audio-recognition`
- `action-move`
- `smile-face`

症状：

- `https://www.wangyutang.cn/audio/` 返回 502。
- `/action/`、`/face/` 也一度返回 502。

恢复方式：

1. 从 `/root/audio-recognition-face-duration-update.tar` 解出 `/root/audio_recognition`，重新构建并启动：

```bash
docker build -t audio-recognition:local /root/audio_recognition
docker run -d --name audio-recognition --network infra_default --restart unless-stopped audio-recognition:local
```

2. 从 `/root/control_platform/action_move` 重新构建并启动：

```bash
docker build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  -t action-move:local /root/control_platform/action_move
docker run -d --name action-move --network infra_default --restart unless-stopped \
  -v /root/control_platform/action_move/data:/app/data action-move:local
```

3. 从 `/root/smile_face` 重新构建并启动：

```bash
docker build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  -t smile-face:local /root/smile_face
docker run -d --name smile-face --network infra_default --restart unless-stopped smile-face:local
```

恢复后公网验证均为 200：

- `https://www.wangyutang.cn/audio/`
- `https://www.wangyutang.cn/action/`
- `https://www.wangyutang.cn/face/`
- `https://www.wangyutang.cn/camera/`

## Code changes made in this module

### `pi_camera_sender.py`

- 增加 `RpicamStillBackend`。
- 上传帧时增加：
  - `X-Capture-Source`
  - `X-Capture-Error`
- 单帧 `single` 模式支持真实相机失败时截图回退。
- `Picamera2Backend` 尝试使用 `RGB888`，并在 JPEG encoder 不可用时使用 `capture_array()` + PIL 编码。
- 单帧拍完后关闭 camera handle，避免持续占用。

### `server.py`

- `latest_meta` 增加：
  - `capture_source`
  - `capture_error`
- `/api/frame` 保存上传端声明的取帧来源和错误信息。
- 服务启动时如果 `/app/data/latest.jpg` 已存在，会从磁盘恢复 `has_image`、`updated_at`、`content_length`，避免容器重启后页面显示无图。

### `static/index.html` and `static/app.js`

- 页面元数据增加“来源”字段。
- 如果 `capture_source == screenshot-fallback`，页面显示警告“相机不可用，已回退截图”。
- 创建任务前检查树莓派是否在线；离线时立即提示“树莓派离线，无法发送”，不再假装等待上传。

## Current operational state

树莓派端当前应使用 systemd drop-in 固定 OpenCV 后端：

```bash
sudo systemctl cat camera-snapshot-sender.service
```

应能看到类似：

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /home/pi/pi_camera_sender.py --server https://www.wangyutang.cn/camera --device-id turbopi-01 --backend opencv
```

检查服务：

```bash
systemctl status camera-snapshot-sender.service --no-pager -l
journalctl -u camera-snapshot-sender.service -n 120 --no-pager
```

验证单帧：

```powershell
$task = Invoke-RestMethod -Uri https://www.wangyutang.cn/camera/api/capture `
  -Method Post -ContentType 'application/json' `
  -Body '{"mode":"single","query_gpio":26}'

Start-Sleep -Seconds 8
Invoke-RestMethod https://www.wangyutang.cn/camera/api/latest | ConvertTo-Json -Depth 5
Invoke-RestMethod https://www.wangyutang.cn/camera/api/control | ConvertTo-Json -Depth 5
```

成功时应看到：

```text
status: complete
uploaded_frames: 1
capture_source: opencv
```

刷新远程巡检：

```powershell
$task = Invoke-RestMethod -Uri https://www.wangyutang.cn/camera/api/capture `
  -Method Post -ContentType 'application/json' `
  -Body '{"mode":"inspect","query_gpio":26}'

Start-Sleep -Seconds 8
Invoke-RestMethod https://www.wangyutang.cn/camera/api/inspection | ConvertTo-Json -Depth 5
```

成功时应看到：

```text
available: true
hostname: raspberrypi
temperature_c: <actual temperature>
sender_service: active
```

## Lessons / guardrails

1. 不要仅用“接口 200”判断功能成功。必须下载 `latest.jpg` 看内容，确认是 `Camera ...` 水印而不是 `Screenshot ...` 水印。
2. `single` 任务成功不等于真实摄像头成功；如果启用了截图回退，要检查 `capture_source`。
3. `docker compose up --force-recreate camera-snapshot` 在该环境中可能影响其他同网络/同镜像历史容器。以后只更新 camera 容器时，优先使用：

```bash
docker cp ...
docker restart camera-snapshot
```

或确认 compose 文件中只包含目标服务后再操作。

4. Pi 端相机后端优先级不要盲目设为 `picamera2` 或 `rpicam-still`。当前现场已验证最稳定的是：

```text
--backend opencv
```

5. 如果之后要重新启用 Picamera2，需要先在 Pi 上独立验证不会卡死：

```bash
python3 - <<'PY'
from picamera2 import Picamera2
import time
cam = Picamera2()
config = cam.create_still_configuration(main={"size": (640, 480), "format": "RGB888"})
cam.configure(config)
cam.start()
time.sleep(1)
arr = cam.capture_array()
print(arr.shape, arr.dtype)
cam.stop()
PY
```

6. 如果之后要重新启用 `rpicam-still`，也必须先独立验证：

```bash
timeout 15s rpicam-still --nopreview --timeout 1000 --width 640 --height 480 --quality 85 -o /tmp/test.jpg
file /tmp/test.jpg
```

只有这些独立验证稳定后，才应把默认后端从 OpenCV 切走。
