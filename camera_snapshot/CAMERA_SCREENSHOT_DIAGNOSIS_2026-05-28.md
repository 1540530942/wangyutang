# Camera and screen snapshot diagnosis, 2026-05-28

## Conclusion

The `camera_snapshot` deployment path is automated for ordinary `camera_snapshot/**`
pushes. The latest verified run was:

- Run: https://github.com/1540530942/wangyutang/actions/runs/26548007946
- Result: success
- Verified steps: syntax check, smoke tests, Tencent Cloud deployment, Raspberry Pi
  sender update, public capture flow for `single`, `screenshot`, and `face`.

The remaining camera issue is not a cloud deployment issue. TurboPi's camera is
expected to enter the system through the ROS2 `usb_cam` peripheral stack:
`/dev/video0` -> `usb_cam` -> `/image_raw` -> `web_video_server`.

At the time of this diagnosis, that chain is incomplete: `/image_raw` exists but
has no publisher, and `/dev/video0` is not present inside the running `turbopi`
container. Therefore `single` cannot produce a real camera frame.

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

TurboPi ROS2 discovery:

```text
ros2 topic list
/image_raw

ros2 topic info /image_raw -v
Publisher count: 0
```

Starting the vendor camera launch directly fails because the configured device is
missing:

```text
ros2 launch peripherals usb_cam.launch.py
Device specified is not available or is not a valid V4L2 device: `/dev/video0`
```

`/dev/video*` exists, but the available nodes are Pi 5 ISP/codec nodes such as
`pispbe` and `rpi-hevc-dec`, not the USB camera node expected by
`peripherals/config/usb_cam_param.yaml`.

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

`single` does not show a real camera frame because the ROS camera publisher is
not running and the configured USB camera device is absent. The sender now runs
with `--backend auto`, so it tries the supported camera backends before falling
back:

1. `web-video-server`
2. `rpicam-still`
3. `picamera2`
4. `opencv`
5. `screenshot-fallback`

For the TurboPi ROS2 path, the sender also attempts to start
`peripherals usb_cam.launch.py` inside the `turbopi` container when `/image_raw`
has no publisher. If `/dev/video0` is absent, it records that diagnostic in the
camera error instead of silently pretending the camera succeeded.

The fallback prevents the UI from staying empty, but the metadata and watermark
must be used to tell whether the image is real camera output.

## Guardrails

Smoke tests now cover:

- server metadata for `single`, `screenshot`, and `face`;
- camera success source reporting;
- camera failure source reporting as `screenshot-fallback`;
- missing TurboPi ROS2 USB camera publisher/device diagnostics;
- static page labels for `摄像机截图`, `屏幕截图`, and `表情截图`;
- static UI status text such as `相机异常`, `屏幕截图`, and `图片已更新`;
- absence of known mojibake markers in the static page assets.

## Next Hardware Check

Before expecting `single` to show the real camera again, the TurboPi ROS2 camera
chain must pass:

```bash
docker exec turbopi bash -lc \
  'source /opt/ros/humble/setup.bash && source /home/ubuntu/ros2_ws/install/setup.bash && ros2 topic info /image_raw -v'
```

with `Publisher count` greater than zero, and `web_video_server` must return a
JPEG from:

```text
http://127.0.0.1:8080/snapshot?topic=/image_raw
```

Until that is true, the correct software behavior is to mark `single` as
`screenshot-fallback` with the camera error instead of pretending that a real
camera frame was captured.
