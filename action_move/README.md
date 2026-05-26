# TurboPi Action Move

`action_move` provides the TurboPi movement, heading, emergency stop, reset, and camera-pan skill layer.

It has two runtime parts:

- Cloud module: `TurboPi Action Move`, a FastAPI service deployed at `/action/`.
- Edge executor: Raspberry Pi scripts that execute bounded ROS2 commands inside the local `turbopi` container.

## Cloud Module

Public route:

```text
https://www.wangyutang.cn/action/
```

Local container port:

```text
8094
```

Core endpoints:

```text
GET  /api/health
GET  /api/skills
GET  /api/settings
POST /api/settings
GET  /api/tasks
POST /api/tasks
GET  /api/tasks/next?wait_seconds=10
POST /api/tasks/result
POST /api/device/heartbeat
```

The cloud service stores a short in-memory task queue. The Raspberry Pi uses long polling so a newly created task is claimed quickly without hot-looping:

```bash
python3 edge_action_poller.py --server https://www.wangyutang.cn/action --token "$ACTION_MOVE_TOKEN" --long-poll-seconds 10
```

If `/app/data/.action_token` does not exist in the cloud container, token validation is disabled. For production, put the same token in that file and pass it to the edge poller.

## Skills

The skill catalog lives in `skill_catalog.json`.

| User phrase | Skill id | Hardware path |
| --- | --- | --- |
| 急停 | `emergency_stop` | `/cmd_vel` zero Twist |
| Reset / 复位 | `reset_pose` | zero Twist + PWM servo 1/2 -> 1500 |
| 远程关机 | `remote_shutdown` | host `sudo shutdown -h now`, requires code `123` |
| 向左看 | `look_left` | PWM servo 2 -> 1800 |
| 向右看 | `look_right` | PWM servo 2 -> 1200 |
| 向上看 | `look_up` | PWM servo 1 -> 1000 |
| 向下看 | `look_down` | PWM servo 1 -> 1700 |
| 向前走 | `move_forward` | `/cmd_vel linear.x = 0.35` |
| 向后走 | `move_backward` | `/cmd_vel linear.x = -0.35` |
| 向左走 | `move_left` | `/cmd_vel linear.y = 0.35` |
| 向右走 | `move_right` | `/cmd_vel linear.y = -0.35` |
| 向左转 | `turn_left` | `/cmd_vel angular.z = 5.0` |
| 向右转 | `turn_right` | `/cmd_vel angular.z = -5.0` |

The left/right camera mapping was corrected by image review: PWM servo 2 is the horizontal pan axis. PWM servo 1 changes the vertical/pitch view.

## Unit Controls

The cloud service persists runtime settings in `data/settings.json`:

- `unit_distance_cm`: movement unit distance, default `5`.
- `turn_angle_deg`: heading turn unit, default `5`.
- `sensitivity`: duration multiplier control, default `1.0`.

Each task stores a settings snapshot when it is created. The Raspberry Pi poller passes that snapshot into `action_move_executor.py`, so an action keeps the units that were visible on the page when the button was pressed.

The executor maps one unit to bounded timed `/cmd_vel` bursts:

- `move_forward`, `move_backward`, `move_left`, `move_right`: one distance unit.
- `turn_left`, `turn_right`: one heading-angle unit.
- `emergency_stop`: zero Twist only.
- `reset_pose`: zero Twist plus PWM servo 1/2 center reset.

## Local Execution

Dry-run a skill:

```bash
python3 action_move_executor.py turn_left --params-json '{"unit_distance_cm":5,"turn_angle_deg":5,"sensitivity":1}' --dry-run
```

Execute it on the Raspberry Pi host while the `turbopi` ROS container is running:

```bash
python3 action_move_executor.py turn_left
```

The executor runs ROS2 commands as user `ubuntu` inside the `turbopi` container. Every base movement publishes a timed `Twist` burst and then a zero stop command.

Camera look skills request `https://www.wangyutang.cn/camera/api/capture` after the servo command so the camera page refreshes to the new direction.

## Directory Layout

```text
server.py                         Cloud FastAPI module.
Dockerfile                        Cloud module image.
static/                           Cloud control page.
skill_catalog.json                Skill definitions and bounded command values.
action_move_executor.py           Run one local TurboPi skill.
edge_action_poller.py             Pi-side cloud task poller.
edge_ros_controller.py            Persistent ROS2 publisher service inside turbopi.
movement_image_verifier.py        Manual verification helper.
cloud_image_verifier.py           Cloud task plus before/after camera verification helper.
motion_image_verification_*.md    Current image-based calibration report.
source_map.md                     Tutorial source mapping.
ros2_command_examples.md          ROS2 command examples.
implementation_plan.md            Architecture notes.
latency_analysis_*.md             Latency diagnosis and deployment notes.
```

## Low-Latency Edge Controller

The preferred runtime path is:

```text
cloud task -> edge_action_poller.py -> http://127.0.0.1:8765/execute -> persistent ROS2 publishers
```

`edge_ros_controller.py` runs inside the `turbopi` container with ROS2 sourced once. It keeps `/cmd_vel` and PWM servo publishers alive, so each button no longer pays the cost of starting `docker exec` and `ros2 topic pub`.

`edge_action_poller.py` falls back to `action_move_executor.py` if the local controller is unavailable.

## Safety Rules

- Always send a stop command after timed base movement.
- Prioritize `emergency_stop` in the cloud queue.
- `remote_shutdown` requires verification code `123` at the cloud API and web prompt before it enters the queue.
- Use `/cmd_vel` for normal movement; reserve direct motor speed commands for diagnostics.
- Keep PWM servo values inside calibrated ranges.
- Avoid long-running monitoring sessions on unstable Wi-Fi/hotspot links.

## Regression Guard

Run this before and after substantial changes:

```bash
python action_move/regression_guard.py --local --cloud
```

The guard does not send movement commands and does not create a valid shutdown task. It checks:

- local FastAPI health and skill catalog
- remote shutdown verification-code rejection
- motion queue overlap rejection
- camera mini-window static assets
- cloud `/action/` and `/camera/` health
- cloud skill catalog and device network fields

For live hardware changes, add a separate image-verification run with `cloud_image_verifier.py`; keep that out of the default guard because it moves the robot.
