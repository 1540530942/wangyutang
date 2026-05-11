# TurboPi Action Move

`action_move` provides the TurboPi movement and camera-pan skill layer.

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
GET  /api/tasks
POST /api/tasks
GET  /api/tasks/next
POST /api/tasks/result
POST /api/device/heartbeat
```

The cloud service stores a short in-memory task queue. The Raspberry Pi can poll and execute tasks with:

```bash
python3 edge_action_poller.py --server https://www.wangyutang.cn/action --token "$ACTION_MOVE_TOKEN"
```

If `/app/data/.action_token` does not exist in the cloud container, token validation is disabled. For production, put the same token in that file and pass it to the edge poller.

## Eight Skills

The skill catalog lives in `skill_catalog.json`.

| User phrase | Skill id | Hardware path |
| --- | --- | --- |
| 向左看 | `look_left` | PWM servo 2 -> 1800 |
| 向右看 | `look_right` | PWM servo 2 -> 1200 |
| 向上看 | `look_up` | PWM servo 1 -> 1000 |
| 向下看 | `look_down` | PWM servo 1 -> 1700 |
| 向前走 | `move_forward` | `/cmd_vel linear.x = 0.35` |
| 向后走 | `move_backward` | `/cmd_vel linear.x = -0.35` |
| 向左走 | `move_left` | `/cmd_vel linear.y = 0.35` |
| 向右走 | `move_right` | `/cmd_vel linear.y = -0.35` |

The left/right camera mapping was corrected by image review: PWM servo 2 is the horizontal pan axis. PWM servo 1 changes the vertical/pitch view.

## Local Execution

Dry-run a skill:

```bash
python3 action_move_executor.py look_left --dry-run
```

Execute it on the Raspberry Pi host while the `turbopi` ROS container is running:

```bash
python3 action_move_executor.py look_left
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
edge_action_poller.py             Optional Pi-side cloud task poller.
movement_image_verifier.py        Manual verification helper.
motion_image_verification_*.md    Current image-based calibration report.
source_map.md                     Tutorial source mapping.
ros2_command_examples.md          ROS2 command examples.
implementation_plan.md            Architecture notes.
```

## ROS2 Control Paths

Base movement:

```text
/cmd_vel
  -> controller.mecanum
  -> /ros_robot_controller/set_motor_speeds
  -> ros_robot_controller_node
  -> STM32 / motor driver
```

PWM servo movement:

```text
/ros_robot_controller/pwm_servo/set_state
  -> ros_robot_controller_node
  -> Board.pwm_servo_set_position(...)
```

## Safety Rules

- Always send a stop command after timed base movement.
- Use `/cmd_vel` for normal movement; reserve direct motor speed commands for diagnostics.
- Keep PWM servo values inside calibrated ranges.
- Avoid long-running monitoring sessions on unstable Wi-Fi/hotspot links.
