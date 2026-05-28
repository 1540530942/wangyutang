# Camera and screen snapshot diagnosis, 2026-05-28

## Conclusion

The `camera_snapshot` deployment path is automated for ordinary `camera_snapshot/**`
pushes. The latest verified run was:

- Run: https://github.com/1540530942/wangyutang/actions/runs/26548007946
- Result: success
- Verified steps: syntax check, smoke tests, Tencent Cloud deployment, Raspberry Pi
  sender update, public capture flow for `single`, `screenshot`, and `face`.

The remaining camera issue is not a cloud deployment issue. The Raspberry Pi OS
currently does not expose a real camera to either libcamera/rpicam or OpenCV, so
`single` cannot produce a real camera frame.

## Evidence

Raspberry Pi service after deployment:

```text
camera-snapshot-sender.service: active
ExecStart=/usr/bin/python3 /home/pi/pi_camera_sender.py --server https://www.wangyutang.cn/camera --device-id turbopi-01 --backend auto --camera-index 1
```

Camera discovery:

```text
rpicam-still --list-cameras
No cameras available!
```

`/dev/video*` exists, but the available nodes are not usable as a normal camera
for the current sender path. OpenCV still cannot open a camera around the
configured index, and `rpicam-still` exits with status 255.

Public capture results from 2026-05-28:

```text
single     -> capture_source=screenshot-fallback
screenshot -> capture_source=screenshot
face       -> capture_source=face-screenshot
```

The downloaded `screenshot` image is a real Raspberry Pi desktop screenshot and
is stamped with `Screen ...`. The downloaded `single` image is stamped with
`Camera fallback ...`, which means it is explicitly not a real camera frame.

## Root Cause

There are two separate concepts:

- `single`: intended to capture the physical camera.
- `screenshot`: intended to capture the Raspberry Pi desktop.

`screenshot` works. It captures the Raspberry Pi Wayland desktop using `grim`
with:

```text
XDG_RUNTIME_DIR=/run/user/1000
WAYLAND_DISPLAY=wayland-1
```

`single` does not show a real camera frame because the Pi currently reports no
camera. The sender now runs with `--backend auto`, so it tries the supported
camera backends before falling back:

1. `web-video-server`
2. `rpicam-still`
3. `picamera2`
4. `opencv`
5. `screenshot-fallback`

The fallback prevents the UI from staying empty, but the metadata and watermark
must be used to tell whether the image is real camera output.

## Guardrails

Smoke tests now cover:

- server metadata for `single`, `screenshot`, and `face`;
- camera success source reporting;
- camera failure source reporting as `screenshot-fallback`;
- static page labels for `摄像机截图`, `屏幕截图`, and `表情截图`;
- static UI status text such as `相机异常`, `屏幕截图`, and `图片已更新`;
- absence of known mojibake markers in the static page assets.

## Next Hardware Check

Before expecting `single` to show the real camera again, the Pi must pass:

```bash
rpicam-still --list-cameras
```

and at least one of the auto backends must be able to produce a JPEG. Until that
is true, the correct software behavior is to mark `single` as
`screenshot-fallback` with the camera error instead of pretending that a real
camera frame was captured.
