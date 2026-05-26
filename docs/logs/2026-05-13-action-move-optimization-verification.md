# 2026-05-13 Action Move Optimization And Image Verification

## Goal

Optimize `action_move` latency and reliability, then verify camera orientation and movement buttons with before/after camera images. A specific concern was that `move_backward` might keep running.

## Changes

- Added `action_move/edge_ros_controller.py`.
  - Runs inside the `turbopi` container with ROS2 sourced once.
  - Keeps `/cmd_vel` and PWM servo publishers alive.
  - Serializes actions with a lock.
  - Publishes stop after every base movement or turn.
  - Emergency stop publishes repeated zero Twist.
- Updated `edge_action_poller.py`.
  - Preferred path is now `http://127.0.0.1:8765/execute`.
  - Falls back to `action_move_executor.py` only if the persistent controller is unavailable.
- Added `action_move/systemd/action-move-controller.service`.
  - Cleans stale `edge_ros_controller.py` processes before start/stop.
  - Prevents the old invalid ROS context and occupied-port problem.
- Added `action_move/cloud_image_verifier.py`.
  - Captures before image.
  - Creates a cloud task.
  - Waits for task completion.
  - Captures after image.
  - Computes image-difference metrics.
- Updated turn skills:
  - `turn_left`: `/cmd_vel angular.z = 5.0`
  - `turn_right`: `/cmd_vel angular.z = -5.0`
  - Reason: Hiwonder examples use angular values around `5.0` to `10.0`; the previous `0.45` was too weak and did not produce visible turn movement.

## Deployment

- Tencent Cloud `action-move` container was updated and restarted.
- Raspberry Pi `/home/pi/action_move` was updated.
- `edge_ros_controller.py` and `skill_catalog.json` were copied into:

```text
/home/ubuntu/action_move
```

inside the `turbopi` container.

- Raspberry Pi services:

```text
action-move-controller.service: active
action-move-poller.service: active
```

## Verification

Default settings restored before the final run:

```json
{"unit_distance_cm": 5.0, "turn_angle_deg": 5.0, "sensitivity": 1.0}
```

Final verification run:

```text
action_move/cloud_image_verify/20260513_002945
```

All actions below completed, used `persistent_ros_controller`, and passed image-difference verification with threshold `changed_percent_gt8 >= 2.0`.

| Action | Status | Completion latency | Changed pixels > 8 |
| --- | --- | ---: | ---: |
| `look_left` | complete | 1.291s | 84.67% |
| `look_right` | complete | 0.700s | 76.93% |
| `look_up` | complete | 1.259s | 91.78% |
| `look_down` | complete | 0.969s | 94.53% |
| `move_forward` | complete | 1.372s | 98.99% |
| `move_backward` | complete | 2.321s | 99.30% |
| `move_left` | complete | 1.490s | 41.07% |
| `move_right` | complete | 1.753s | 39.24% |
| `turn_left` | complete | 1.553s | 81.24% |
| `turn_right` | complete | 1.252s | 86.98% |

Final safety stop:

```text
emergency_stop complete
claim_latency_seconds = 0.308
completion_latency_seconds = 0.726
controller elapsed_seconds = 0.092
```

## Backward Movement Concern

`move_backward` was verified in the final run:

- Task completed.
- It used `persistent_ros_controller`.
- Controller movement duration was one unit: `800ms`.
- Stop was published after the burst by the persistent controller.
- Image difference was strong: `99.30%`.
- No continued backward movement was observed in task state or final safety stop.

## Remaining Notes

- Total completion latency still includes cloud polling, task result upload, and camera capture timing. The actual controller execution is much lower:
  - emergency stop: about `0.09s`
  - servo command: about `0.35s`
  - movement unit: about `0.90s`
  - turn unit: about `0.55s`
- If lower cloud-to-robot latency is required, the next step is replacing polling with a persistent websocket/MQTT connection.

## Follow-up: Remote Shutdown Button

Added a protected `remote_shutdown` action:

- Web button label: `远程关机`.
- Web prompt requires verification code `123`.
- Cloud API also requires `verification_code == "123"` before creating the task.
- Skill id: `remote_shutdown`.
- Skill type: `system_shutdown`.
- Pi-side poller handles it on the Raspberry Pi host with:

```bash
sudo shutdown -h now
```

Local validation:

- Wrong code returns HTTP 403.
- Correct code creates a `remote_shutdown` task.
- Executor dry-run prints `sudo shutdown -h now` without powering off.

## Follow-up: Camera Mini Window

Added a UI-only camera monitor in `action_move/static`.

Behavior:

- A feature switch named `相机小窗` controls the monitor.
- When enabled, the page polls `/camera/api/latest` every 2 seconds.
- The latest image is loaded from `/camera/api/latest.jpg`.
- A hidden 80x60 canvas compares the current frame with the previous frame.
- If changed pixels exceed about `3.5%`, the mini window refreshes and shows a CSS camera symbol.
- If no significant change occurs, the camera symbol still appears every 10 seconds as a heartbeat.

This does not send movement commands and does not require Raspberry Pi SSH access. It only consumes the existing cloud camera API.

## Follow-up: Regression Guard And Network Heartbeat

Added `action_move/regression_guard.py`.

Default guard:

```bash
python action_move/regression_guard.py --local --cloud
```

It intentionally avoids live movement and valid shutdown tasks. It checks:

- local FastAPI health
- required skill catalog entries
- remote shutdown wrong-code rejection
- local-only correct-code shutdown task creation
- motion queue overlap rejection
- camera mini-window static assets
- cloud `/action/` health
- cloud device network fields
- cloud skill catalog
- cloud wrong-code shutdown rejection
- cloud page/app.js camera monitor assets
- cloud `/camera/` health with image

Latest run result:

```text
All guard checks passed.
```

Updated Raspberry Pi heartbeat:

- `hostname`
- `ip_address`
- `wifi_ssid`
- `gateway`

Verified cloud heartbeat after restarting the Pi poller:

```json
{
  "hostname": "raspberrypi",
  "ip_address": "192.168.137.2",
  "wifi_ssid": "pangdahai",
  "gateway": "192.168.137.1",
  "online": true
}
```

## Follow-up: Response Speed Optimization

Problem observed:

- Cloud HTTP latency was already low: `/action/api/health`, `/action/api/tasks`, and camera APIs were around `0.1s` to `0.15s`.
- Recent movement task claim latency was commonly `0.2s` to `0.6s`.
- The Pi poller accepted `--interval 0.35`, but the code still slept with `max(args.interval, 0.5)`, so the effective task pickup interval was at least `0.5s`.
- Movement completion still includes the physical unit action duration, for example a 5cm move publishes for about `800ms`; this part should not be removed without recalibrating distance.

Changes made:

- Cloud `GET /api/tasks/next` now supports long polling with `wait_seconds`.
- Pi `edge_action_poller.py` now calls `/api/tasks/next?wait_seconds=10.00` with a matching request timeout.
- Poller idle sleep lower bound changed from `0.5s` to `0.05s`; the installed service uses `--interval 0.10`.
- Added `action_move/systemd/action-move-poller.service` so the deployed low-latency arguments are recorded in the repository.
- Web UI refreshes every `350ms` while a task is active, then returns to `1000ms` when idle.
- Web UI releases the clicked button busy state after `350ms`.
- Camera mini-window now checks `/camera/api/latest` first and only downloads `/camera/api/latest.jpg` when the frame id changes. Repeatedly downloading the same image was removed from the normal path.

Deployment:

- Updated Tencent Cloud files under `/root/control_platform/action_move`.
- Restarted Docker container `action-move`.
- Updated Raspberry Pi file `/home/pi/action_move/edge_action_poller.py`.
- Installed and restarted `/etc/systemd/system/action-move-poller.service`.

Verification:

```text
action-move-poller.service: active
cloud static app.js: contains REFRESH_ACTIVE_MS, 未更新, 已下发
Raspberry Pi heartbeat: online, hostname raspberrypi, IP 192.168.137.2, Wi-Fi pangdahai
```

Low-risk latency test using `emergency_stop`:

```json
{
  "status": "complete",
  "claim_latency_seconds": 0.019,
  "completion_latency_seconds": 0.568,
  "controller_elapsed_seconds": 0.092,
  "poller_controller_roundtrip_seconds": 0.093
}
```

Regression guard:

```text
python action_move/regression_guard.py --local --cloud
All guard checks passed.
```

Remaining speed limit:

- Base movement buttons still take about the configured physical action time plus stop publishing. With the current default 5cm unit, this is expected to be roughly `0.8s` of robot motion before completion is reported.
- To make movement feel even faster without changing distance calibration, the next best step is WebSocket/SSE status push to the browser.
- To make actual movement complete faster, reduce the movement unit distance or recalibrate velocity/duration for a shorter burst.

## Follow-up: Unit Settings Input Reset

Observed issue:

- Unit setting persistence on the backend worked, but the page still looked like it could not be edited.
- Root cause: the page auto-refreshes health every second, and `refresh()` called `renderSettings(health.settings)` every time. While the user was typing, the refresh loop wrote the old saved value back into the input fields.

Fix:

- Added a `settingsDirty` state in `static/app.js`.
- Added `getFormSettings()` and `isEditingSettings()`.
- While the user is editing, automatic refresh no longer overwrites the three unit-setting inputs.
- The summary labels still update from the unsaved form values.
- After save succeeds, the backend-confirmed values are written back to the inputs.
- The save button is now explicitly `type="button"`.

Verification:

```text
local static checks: balanced braces/parentheses
cloud static app.js: contains settingsDirty and updateInputs: false
cloud index.html: saveSettingsBtn type="button"
cloud /api/settings restored to 5cm / 5deg / 1x after test
```


## Follow-up: Touchscreen Interaction Tuning

Observed context:

- Raspberry Pi detects `WaveShare WaveShare Touchscreen` on `/dev/input/event6`.
- Desktop compositor is Wayfire.
- The action page used normal browser `click` handlers only, which can feel sluggish on a small touchscreen.

Changes made:

- Action buttons now listen for touch/pen `pointerup` and trigger immediately.
- The following synthetic `click` after a touch is ignored to prevent duplicate action creation.
- Buttons get an immediate pressed visual state on touch down.
- Added `touch-action: manipulation` to reduce double-tap/zoom waiting behavior.
- Increased button target height from `44px` to `52px` and small buttons from `34px` to `42px`.
- Increased numeric input height and checkbox size for finger use.

Verification:

```text
local static checks: balanced braces/parentheses
regression guard: All guard checks passed
```
