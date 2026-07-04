# TurboPi Action Move

`action_move` provides the TurboPi movement, heading, emergency stop, reset, and camera-pan skill layer.

It has two runtime parts:

- Cloud module: `TurboPi Action Move`, a FastAPI service deployed at `/action/`.
- Edge executor: Raspberry Pi scripts that execute ROS2 commands inside the local `turbopi` container.

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

## Skills

The skill catalog lives in `skill_catalog.json`.

| 技能 id | 名称 | 类型 | 执行方式 |
|---|---|---|---|
| `emergency_stop` | 急停 | `base_stop` | `/cmd_vel` 零速度 |
| `reset_pose` | Reset / 复位 | `reset_pose` | 零速度 + PWM servo 1/2 → 1500 |
| `remote_shutdown` | 远程关机 | `system_shutdown` | 宿主机 `sudo shutdown -h now`，需验证码 `123` |
| `rgb_on` | RGB 开灯 | `rgb_light` | `/set_rgb` 按 settings 颜色点亮 |
| `rgb_off` | RGB 关灯 | `rgb_light` | `/set_rgb` 全部清零 |
| `front_distance` | 超声波测距 | `front_distance` | 读取超声波模块，返回 cm 估算值 |
| `look_left` | 向左看 | `camera_servo` | PWM servo 2 +100，范围 1200..1800 |
| `look_right` | 向右看 | `camera_servo` | PWM servo 2 -100，范围 1200..1800 |
| `look_up` | 向上看 | `camera_servo` | PWM servo 1 -100，范围 1000..1700 |
| `look_down` | 向下看 | `camera_servo` | PWM servo 1 +100，范围 1000..1700 |
| `move_forward` | 向前走 | `base_move` | `/cmd_vel linear.x=0.35`，持续 `unit_distance_cm/5*800ms` |
| `move_backward` | 向后走 | `base_move` | `/cmd_vel linear.x=-0.35` |
| `move_left` | 向左走 | `base_move` | `/cmd_vel linear.y=0.35` |
| `move_right` | 向右走 | `base_move` | `/cmd_vel linear.y=-0.45`（右轮阻力补偿） |
| `turn_left` | 向左转 | `base_turn` | `/cmd_vel angular.z=5.0`，持续 `turn_angle_deg/5*450ms` |
| `turn_right` | 向右转 | `base_turn` | `/cmd_vel angular.z=-5.0` |
| `rotate_in_place` | 原地旋转 | `base_turn` | **motor_override** 四轮同向 speed=35，2000ms |
| `move_diagonal_forward_left` | 斜向左前方移动 | `base_move` | **motor_override** M2=+50,M3=-50，300ms |
| `move_diagonal_forward_right` | 斜向右前方移动 | `base_move` | **motor_override** M1=-50,M4=+50，300ms |
| `camera_snapshot` | 拍照 | `camera_snapshot` | 请求 camera 服务捕获当前帧 |

`motor_override` 技能绕过 mecanum_node 运动学换算，直接发布到 `/ros_robot_controller/set_motor_speeds`，
用于四轮差速比无法用 Twist 表达的运动（原地旋转、纯斜向位移）。

## Unit Controls

The cloud service persists runtime settings in `data/settings.json`:

- `unit_distance_cm`: movement unit distance, default `10.0`.
- `turn_angle_deg`: heading turn unit, default `5.0`.
- `sensitivity`: duration multiplier, default `1.0`.
- `voice_volume_percent`: completion voice volume, `0` means muted.

Duration calculation:
- 移动：`unit_distance_cm / 5.0 * 800ms * sensitivity`，范围限制在 `[180, 3000]ms`
- 转向：`turn_angle_deg / 5.0 * 450ms * sensitivity`，范围限制在 `[180, 2500]ms`

## Execution Path

```text
cloud task
  → edge_action_poller.py (Pi host, port 8765 poll)
    → POST http://127.0.0.1:8765/execute (edge_ros_controller HTTP)
      → edge_ros_controller.py (inside turbopi container)
        ├─ base_move / base_turn（无 motor_override）→ persistent /cmd_vel publisher
        ├─ base_move / base_turn（有 motor_override）→ persistent /set_motor_speeds publisher
        ├─ camera_servo   → persistent /pwm_servo/set_state publisher
        ├─ rgb_light      → persistent /set_rgb publisher
        ├─ front_distance → Sonar SDK 读取
        ├─ camera_snapshot→ POST camera 服务 /api/capture
        ├─ base_stop      → /cmd_vel 零速度
        └─ reset_pose     → 零速度 + servo 1/2 → 1500
```

## SLAM Pose Reporting

When `ACTION_SLAM_MAPPING_URL` is set, the cloud service reports completed base motion tasks to the optional `slam_mapping` service:

```text
action_move /api/tasks/result complete
  → POST $ACTION_SLAM_MAPPING_URL/api/odom
  → slam_mapping updates pose and travelled distance
```

Default in Docker Compose:

```text
ACTION_SLAM_MAPPING_URL=http://slam-mapping:8301
```

Current odometry mapping:

| Skill | SLAM update |
|---|---|
| `move_forward` | `dx_m = unit_distance_cm / 100` |
| `move_backward` | `dx_m = -unit_distance_cm / 100` |
| `move_left` | `dy_m = unit_distance_cm / 100` |
| `move_right` | `dy_m = -unit_distance_cm / 100` |
| `turn_left` | `dyaw_rad = +turn_angle_deg` |
| `turn_right` | `dyaw_rad = -turn_angle_deg` |

If SLAM is unavailable, motion completion still succeeds and the task stores `slam_update.ok=false` for diagnostics.

`edge_action_poller.py` falls back to `action_move_executor.py` if the controller is unavailable.

### motor_override 路径

`rotate_in_place`、`move_diagonal_forward_left`、`move_diagonal_forward_right` 三个技能携带 `motor_override` 字段，
`edge_ros_controller.execute()` 检测到该字段后调用 `publish_motor_override()`：

```python
# edge_ros_controller.py: publish_motor_override()
msg.data = [MotorSpeedControl(id=s["id"], speed=s["speed"]) for s in speeds]
self.motors_pub.publish(msg)   # 发布运动帧
time.sleep(duration_ms / 1000.0)
self.motors_pub.publish(stop_msg)  # 发布停止帧
```

持久 publisher 避免了旧路径（fc_server → `docker exec ros2 topic pub`）的子进程开销（约 600ms）。

## Local Execution

Dry-run a skill:

```bash
python3 action_move_executor.py turn_left --params-json '{"unit_distance_cm":5,"turn_angle_deg":5,"sensitivity":1.0}' --dry-run
```

Execute on the Raspberry Pi while the `turbopi` container is running:

```bash
python3 action_move_executor.py turn_left
```

## ROS2 原子指令

`edge_ros_controller.py` 内部使用的 ROS2 topic 也可在容器内直接用 `ros2 topic pub` 触发，无需启动任何服务。
详见 [`function_center/ros2_command_examples.md`](../function_center/ros2_command_examples.md)。

## Safety Rules

- Always send a stop command after timed base movement.
- Prioritize `emergency_stop` in the cloud queue.
- `remote_shutdown` requires verification code `123` at the cloud API and web prompt before it enters the queue.
- Use `/cmd_vel` for normal movement; use `motor_override` for diagonal / rotate-in-place; reserve direct motor speed commands outside skill_catalog for diagnostics only.
- Keep PWM servo values inside calibrated ranges (servo1: 1000..1700, servo2: 1200..1800).

## Regression Guard

```bash
python action_move/regression_guard.py --local --cloud
```

The guard checks health, skill catalog, shutdown verification, motion queue overlap, and cloud routes. It does not send movement commands. For live hardware verification use `cloud_image_verifier.py`.

## Directory Layout

```text
server.py                         Cloud FastAPI module.
Dockerfile                        Cloud module image.
static/                           Cloud control page.
skill_catalog.json                Skill definitions (twist values, motor_override specs, servo bounds).
action_move_executor.py           Fallback: run one skill via docker exec (subprocess path).
edge_action_poller.py             Pi-side cloud task poller; long-polls /api/tasks/next.
edge_ros_controller.py            Persistent ROS2 publisher service inside turbopi container.
movement_image_verifier.py        Manual verification helper (before/after camera diff).
cloud_image_verifier.py           Cloud task + image verification helper.
regression_guard.py               Fast smoke check for health and schema without moving robot.
edge_controller_motor_override_2026-06.md  Improvement A design notes (motor_override native path).
implementation_plan.md            Original architecture design document.
source_map.md                     Tutorial source mapping.
```
