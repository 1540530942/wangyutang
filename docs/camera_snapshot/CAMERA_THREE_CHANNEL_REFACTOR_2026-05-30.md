# Camera Snapshot 三通道重构记录，2026-05-30

## 目标

把 `camera_snapshot` 中的三类截图拆成独立通道：

- `camera`：真实摄像机帧。
- `screen`：树莓派当前桌面截图。
- `face`：`smile_face` 服务根据当前表情 state 离屏渲染出的 JPEG。

目标是避免三类截图继续共用一个任务槽、共用一张 `latest.jpg`、互相覆盖或互相伪装。

## 修改范围

### `camera_snapshot/server.py`

完成了服务端三通道拆分：

- 新增固定 kind：`camera` / `screen` / `face`。
- 将原来的单一 `state["task"]` 改成三通道任务：
  - `tasks.camera`
  - `tasks.screen`
  - `tasks.face`
- 将原来的单一 `latest_meta` 改成按 kind 存储。
- 最新图片改为三份：
  - `data/latest_camera.jpg`
  - `data/latest_screen.jpg`
  - `data/latest_face.jpg`
- `POST /api/capture` 支持新协议：
  - `{"kind":"camera","mode":"single"}`
  - `{"kind":"screen","mode":"single"}`
  - `{"kind":"face","mode":"single"}`
- 保留旧协议兼容：
  - `mode=single` -> `camera`
  - `mode=screenshot` -> `screen`
  - `mode=face` -> `face`
  - `mode=continuous` -> `camera`
  - `mode=inspect` -> `screen`
- `GET /api/control` 返回三通道 `tasks`，同时保留旧字段 `task` 作为 camera alias。
- `POST /api/frame` 新增读取 `X-Capture-Kind`，按 kind 写入对应 latest 图片。
- 老 sender 没有 `X-Capture-Kind` 时，按 `X-Capture-Source` 做兼容推断。
- `GET /api/latest?kind=...` 和 `GET /api/latest.jpg?kind=...` 支持按通道读取，不传 kind 默认 camera。
- `POST /api/stop?kind=...` 只停止指定通道。

### `camera_snapshot/pi_camera_sender.py`

完成了树莓派发送端三通道采集逻辑：

- sender 轮询 `/api/control` 后读取 `tasks.camera/screen/face`。
- 上传图片时新增：
  - `X-Capture-Kind: camera|screen|face`
- camera 通道：
  - 继续使用现有相机后端链。
  - 删除“相机失败后回退成屏幕截图”的行为。
  - 相机失败就是相机失败，不再伪装为截图成功。
- screen 通道：
  - 继续使用 `grim/scrot` 抓树莓派桌面。
  - `capture_source=screenshot`。
- face 通道：
  - 不再抓桌面。
  - 改成请求 `smile_face` 的离屏渲染接口：
    - `https://www.wangyutang.cn/face/api/face/render.jpg`
  - `capture_source=smile-face-render`。
- camera continuous 不再长期独占循环；sender 每轮都会检查 screen/face 任务。
- ROS `usb_cam.launch.py` 自启动默认关闭，避免一次截图扰动机器人主栈。

### `camera_snapshot/static/index.html`

前端从单图片区改成三张独立卡片：

- 摄像机截图卡片。
- 屏幕截图卡片。
- 表情截图卡片。

每张卡片独立显示：

- 图片。
- 来源。
- 更新时间。
- 图片大小。
- 任务状态。
- 健康状态。

### `camera_snapshot/static/app.js`

前端逻辑改成按 kind 独立轮询：

- `GET /api/latest?kind=camera`
- `GET /api/latest?kind=screen`
- `GET /api/latest?kind=face`

按钮映射：

- 摄像机截图 -> `POST /api/capture {kind:"camera", mode:"single"}`
- 屏幕截图 -> `POST /api/capture {kind:"screen", mode:"single"}`
- 表情截图 -> `POST /api/capture {kind:"face", mode:"single"}`
- 连续发送 -> `POST /api/capture {kind:"camera", mode:"continuous"}`

### `camera_snapshot/static/styles.css`

重做了三卡片布局和样式：

- 三通道卡片布局。
- 独立状态 pill。
- 各通道元数据网格。
- 保留全局 GPIO / 巡检信息。

### `smile_face/face_render.py`

新增离屏渲染模块。

最初实现时复用了 `fb_renderer.py` 的深色 framebuffer 风格，后来发现这不是用户原来的表情样式，于是改为复刻 `qt_lcd_client.py` / 网页 Canvas 的浅色可爱风格：

- 浅色渐变背景。
- 可爱圆角脸。
- 粉色腮红。
- 支持 `mochi` / `bunny` / `star` / `panda` style。
- 支持当前 emotion：`neutral` / `happy` / `joy` / `sad` / `angry`。

### `smile_face/server.py`

新增接口：

```text
GET /api/face/render.jpg
```

行为：

- 读取当前全局 `state`。
- 构造 `face_render.FaceState`。
- 调用 `draw_face(...)` 离屏渲染。
- 返回 JPEG。
- 响应头使用 `Cache-Control: no-store`。

后续修正：

- 一开始只传了 emotion，没有传 style。
- 已修复为同时传入 `state.style`，确保表情截图和当前样式一致。

### `smile_face/fb_renderer.py`

将渲染核心抽出到 `face_render.py` 后，`fb_renderer.py` 改为只负责：

- 轮询 `/api/state`。
- 计算 blink。
- 调用共享 `draw_face(...)`。
- 写 `/dev/fb0`。

### `smile_face/Dockerfile`

新增复制：

```dockerfile
COPY face_render.py ./face_render.py
```

否则容器中 `server.py` import `face_render` 会失败。

### `camera_snapshot/tests/test_smoke.py`

更新和新增测试：

- 三个 kind 的 latest 存储互不覆盖。
- `/api/latest.jpg?kind=...` 返回各自通道图片。
- `/api/control` 同时返回三通道任务。
- `/api/stop?kind=face` 不影响 camera task。
- sender 上传包含 `X-Capture-Kind`。
- camera 失败不再 fallback 到 screenshot。
- `GET /api/face/render.jpg` 返回 JPEG。
- 静态页面包含三卡片文案，且无 mojibake。

### 部署脚本

修改了：

- `.github/workflows/deploy-camera-snapshot.yml`
- `scripts/github_deploy_camera_snapshot.sh`
- `scripts/deploy_camera_snapshot.ps1`

主要变化：

- camera 专项部署包包含 `smile_face`，因为 face 通道依赖 `smile_face /api/face/render.jpg`。
- 部署时可同步更新 `smile-face` 容器。
- 公网验证逻辑改为按 kind 检查：
  - `camera` -> 真实相机后端。
  - `screen` -> `screenshot`。
  - `face` -> `smile-face-render`。

## 遇到的问题与解决

### 问题 1：三类截图互相覆盖

现象：

- 摄像机截图、屏幕截图、表情截图都显示在同一张图里。
- 后点的按钮覆盖前一个任务和图片。

原因：

- 服务端只有一个 `state["task"]`。
- 服务端只有一张 `data/latest.jpg`。
- 服务端只有一份 `latest_meta`。

解决：

- 拆成 `tasks.camera/screen/face`。
- 拆成 `latest_camera.jpg/latest_screen.jpg/latest_face.jpg`。
- API 增加 `kind`。

### 问题 2：摄像机失败会伪装成屏幕截图

现象：

- 用户点击摄像机截图，页面可能显示桌面截图。
- 只能通过水印和 `capture_source=screenshot-fallback` 判断真假。

原因：

- `pi_camera_sender.py` 中的 camera 失败 fallback 会调用桌面截图。

解决：

- 删除实际 fallback 行为。
- camera 失败不写 screen/face 图。
- 用户想看桌面必须点屏幕截图。

### 问题 3：表情截图最初样式不对

现象：

- 表情截图变成深色 framebuffer 机器人脸。
- 与原来的浅色可爱表情样式不一致。

原因：

- 初版离屏渲染复用了 `fb_renderer.py` 的深色渲染函数。
- 这个函数和网页/Qt LCD 的原始可爱风格不是同一套视觉实现。

解决：

- 重写 `smile_face/face_render.py`。
- 按 `qt_lcd_client.py` / `static/app.js` 的视觉风格实现浅色离屏渲染。
- `server.py` 渲染时传入当前 `style`。
- 已热更新腾讯云 `smile-face` 容器并端到端验证。

验证结果：

```text
kind=face
capture_source=smile-face-render
capture_error=""
```

下载 `/camera/api/latest.jpg?kind=face` 后，已确认是浅色可爱风格。

### 问题 4：屏幕截图显示的是表情图

现象：

- 点击屏幕截图后，screen 卡片显示的是表情窗口。
- 图片水印是 `Screen ...`，但内容是表情。

原因：

- 这不是通道写错。
- 树莓派桌面上运行着全屏服务：

```text
smile-face-lcd.service
/usr/bin/python3 /home/pi/smile_face/qt_lcd_client.py
```

- `grim/scrot` 抓的是“当前桌面”，而当前桌面被全屏表情 LCD 客户端占住。

解决：

- 在 `pi_camera_sender.py` 中新增 screen 截图保护逻辑：
  - 抓 screen 前检测 `smile-face-lcd.service`。
  - 如果 active，则执行 `sudo -n systemctl stop smile-face-lcd.service`。
  - 等服务停止后抓桌面。
  - 抓完执行 `sudo -n systemctl start smile-face-lcd.service`。
- 新增参数：

```text
--screen-hide-service smile-face-lcd.service
--no-screen-hide-face-service
```

- 默认启用临时隐藏表情 LCD 服务。

验证结果：

```text
running task ... kind=screen mode=single
stopping smile-face-lcd.service before screen capture
restarting smile-face-lcd.service after screen capture
uploaded task ... kind=screen
smile-face-lcd.service: active
```

### 问题 5：face 通道依赖 smile_face，但 camera 专项部署原本只更新 camera

现象：

- 如果只部署 `camera_snapshot`，新的 face 通道会调用 `/face/api/face/render.jpg`。
- 但旧 `smile-face` 容器中没有该接口，face 通道会失败。

解决：

- 修改 camera 专项部署流程。
- release 包包含 `smile_face`。
- 部署脚本可同步更新并重启 `smile-face`。
- 健康检查增加对 `/api/face/render.jpg` 的 JPEG 验证。

## 已执行的线上/设备操作

### 树莓派

已执行：

- 同步新版 `pi_camera_sender.py` 到：

```text
/home/pi/pi_camera_sender.py
```

- 重启：

```text
camera-snapshot-sender.service
```

验证：

```text
camera-snapshot-sender.service active
smile-face-lcd.service active
```

### 腾讯云

已执行定向热更新：

- 同步：

```text
smile_face/server.py
smile_face/face_render.py
```

- 更新容器内文件：

```text
smile-face:/app/server.py
smile-face:/app/face_render.py
```

- 重启：

```text
smile-face
```

验证：

```text
GET /api/face/render.jpg -> JPEG
```

## 验证命令与结果

本地测试：

```text
python camera_snapshot/tests/test_smoke.py
```

结果：

```text
Ran 11 tests
OK
```

Python 编译检查：

```text
python -m py_compile camera_snapshot/server.py camera_snapshot/pi_camera_sender.py smile_face/server.py smile_face/fb_renderer.py smile_face/face_render.py
```

结果：通过。

部署脚本语法检查：

```text
bash -n scripts/github_deploy_camera_snapshot.sh
```

结果：通过。

## 当前状态

- `camera` / `screen` / `face` 三通道已经在代码层独立。
- 树莓派 sender 已热更新并重启。
- 腾讯云 `smile-face` 已热更新并重启。
- 表情截图已经恢复为原浅色可爱样式。
- 屏幕截图会临时隐藏全屏表情 LCD 客户端，避免抓到表情窗口。

## 注意事项

- 当前有部分改动是热更新到线上/树莓派的，应尽快提交入库，避免下次部署覆盖。
- 工作区里还有非本次核心修改项，提交时不要误带：

```text
M audio_recognition
?? .github/workflows/deploy-modules.yml
```

- 本次应提交的核心文件包括：

```text
.github/workflows/deploy-camera-snapshot.yml
camera_snapshot/README.md
camera_snapshot/pi_camera_sender.py
camera_snapshot/server.py
camera_snapshot/static/app.js
camera_snapshot/static/index.html
camera_snapshot/static/styles.css
camera_snapshot/tests/test_smoke.py
scripts/deploy_camera_snapshot.ps1
scripts/github_deploy_camera_snapshot.sh
smile_face/Dockerfile
smile_face/README.md
smile_face/fb_renderer.py
smile_face/face_render.py
smile_face/server.py
docs/camera_snapshot/CAMERA_THREE_CHANNEL_REFACTOR_2026-05-30.md
```
