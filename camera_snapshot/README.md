# TurboPi Camera Snapshot

TurboPi / Raspberry Pi 相机快照可视化模块。

目标域名：

```text
https://camera.wangyutang.cn/
```

## 功能

- `单帧发送`：网页点击后，树莓派只上传 1 张 JPEG。
- `截图上传`：网页点击后，树莓派立即上传当前截图（单帧任务）。
- `持续发送`：网页点击后，树莓派按选择频率持续上传；再次点击停止。
- 支持选择 GPIO 状态随帧返回；默认查询 `GPIO26 / LED2`，使用 `pinctrl get` 只读查询，不抢占 GPIO line。
- GPIO 状态包含电平、数值、原始 `pinctrl` 输出和采样时间。
- GPIO 状态通过独立 `/api/gpio` 通道持续上报；不依赖图片上传。
- 支持频率：5 fps、2 fps、1 fps、0.5 fps、0.2 fps。
- 云端只保存并展示最新一帧，适合轻量预览和远程确认相机状态。
- 树莓派主动访问云端，不需要公网 IP，也不需要云端反连树莓派。

## 数据链路

```text
浏览器
  -> https://camera.wangyutang.cn/
  -> POST /api/capture 或 /api/stop
  -> 云端创建/停止拍照任务

树莓派
  -> pi_camera_sender.py
  -> GET /api/control 轮询任务
  -> 采集相机 JPEG
  -> POST /api/frame 上传照片

网页
  -> GET /api/latest 和 /api/latest.jpg
  -> 显示最新照片和任务状态
```

## 本地开发启动

```powershell
cd C:\Users\Administrator\Desktop\Workspace\Project_Codex\Harness\pi_TurboPi\camera_snapshot
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 0.0.0.0 --port 8099
```

打开：

```text
http://127.0.0.1:8099/
```

## 云端部署

推荐作为平台新模块部署：

```text
camera.wangyutang.cn
  -> Caddy
  -> camera-snapshot:8099
```

DNS：

```text
camera.wangyutang.cn A -> 110.40.154.41
```

Caddy 路由已经按平台模块约定写入：

```text
Arduino_interact/git_workspace/control_platform/infra/caddy/Caddyfile
```

平台 registry 已加入：

```text
Arduino_interact/git_workspace/control_platform/modules/registry.json
```

## Docker 镜像

构建：

```powershell
cd C:\Users\Administrator\Desktop\Workspace\Project_Codex\Harness\pi_TurboPi\camera_snapshot
docker build -t camera-snapshot:local .
```

导出：

```powershell
New-Item -ItemType Directory -Force dist
docker save -o dist\camera-snapshot_local.tar camera-snapshot:local
```

腾讯云服务器加载：

```bash
docker load -i camera-snapshot_local.tar
```

## 树莓派端启动

推荐先用 `picamera2`：

```bash
python3 pi_camera_sender.py --server https://camera.wangyutang.cn --device-id turbopi-01
```

如果是 USB 摄像头并且装了 OpenCV：

```bash
python3 pi_camera_sender.py --server https://camera.wangyutang.cn --backend opencv --camera-index 0
```

常用参数：

```bash
--width 640 --height 480 --quality 78
```

## 上传令牌

可选。云端创建 `.camera_token`。容器部署时推荐放在数据卷：

```text
/app/data/.camera_token
```

本地开发时也可以放在模块目录：

```text
.camera_token
```

树莓派端启动时传同一个 token：

```bash
python3 pi_camera_sender.py --server https://camera.wangyutang.cn --token your-secret-token
```

## 接口

- `GET /api/health`：服务健康检查。
- `GET /api/control`：读取当前拍照任务。
- `GET /api/gpio`：读取最新 GPIO 状态。
- `POST /api/gpio`：树莓派独立上报 GPIO 状态，body 为 JSON。
- `POST /api/capture`：创建截图任务，`mode` 为 `single`（摄像机截图）、`screenshot`（屏幕截图）、`face`（表情截图）或 `continuous`，可选 `query_gpio`，默认 `26`。
- `POST /api/stop`：停止当前持续发送任务。
- `POST /api/frame`：树莓派上传 JPEG，body 为原始 JPEG，header 带 `X-Device-ID` / `X-Frame-ID` / `X-Task-ID`，可带 `X-Gpio-*` 状态 header。
- `GET /api/latest`：读取最新帧元数据。
- `GET /api/latest.jpg`：读取最新 JPEG。
