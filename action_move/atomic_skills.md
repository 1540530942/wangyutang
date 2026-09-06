# action_move 原子技能说明

本文档汇总 `wangyutang_platform/action_move` 中的原子技能。原子技能的定义主要来自 `skill_catalog.json`，执行入口主要是 `edge_ros_controller.py` 的 `POST /execute`，云端任务入口是 `server.py` 的 `POST /api/tasks`。少数技能（如 `speak`）没有 ROS 动作，由 `edge_action_poller.py` 取到任务后直接在边缘处理，不下发 `/execute`。

## 通用分层

| 层级 | 位置 / 形式 | 说明 |
|---|---|---|
| 自然语言 / 按钮 | 前进、左转、开灯、测距等 | 用户或 UI 发起动作 |
| 任务指令 | `{"action":"move_forward"}` | 云端或本机提交动作 ID |
| 原子能力定义 | `action_move/skill_catalog.json` | 定义技能 ID、类型、Twist、舵机、电机或 RGB 参数 |
| 执行器 | `action_move/edge_ros_controller.py` | 读取技能定义并发布 ROS2 消息或调用硬件 SDK |
| 底层指令 | ROS2 topic / SDK / HTTP | `/cmd_vel`、`/pwm_servo/set_state`、`/set_rgb`、Sonar SDK、Camera API |
| 硬件动作 | TurboPi 底盘、舵机、灯光、传感器 | 机器人真实动作或读取结果 |

## 通用触发指令

云端任务指令：

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_forward","source":"manual"}'
```

Pi 本机直接执行：

```bash
curl -X POST http://127.0.0.1:8765/execute \
  -H "Content-Type: application/json" \
  -d '{"action":"move_forward","settings":{"unit_distance_cm":10,"sensitivity":1.0}}'
```

Python SDK 调用：

```python
import json
import urllib.request

payload = json.dumps({
    "action": "move_forward",
    "settings": {"unit_distance_cm": 10, "sensitivity": 1.0},
}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8765/execute",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
print(urllib.request.urlopen(req, timeout=20).read().decode())
```


## 每个技能的云端任务指令
每个原子技能都可以用下面这种云端任务指令提交到 action_move 队列：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_forward","source":"manual"}'
```

逐个技能的云端任务指令如下：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"emergency_stop","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"reset_pose","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"rgb_on","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"rgb_off","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"front_distance","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"look_left","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"look_right","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"look_up","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"look_down","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_forward","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_backward","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_left","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_right","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"turn_left","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"turn_right","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"rotate_in_place","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_diagonal_forward_left","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_diagonal_forward_right","source":"manual"}'
```

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"camera_snapshot","source":"manual"}'
```

`remote_shutdown` is destructive and requires a verification code:

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"remote_shutdown","verification_code":"123","source":"manual"}'
```

`speak` 与其它原子技能同级，但携带自由文本参数，通过 `params` 传入（`settings` 的固定字段放不下文本）：

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"speak","params":{"text":"你好，我到了","voice":"vivian"},"source":"manual"}'
```

- `params.text`（必填，≤200 字）：要播报的文本
- `params.voice`（可选）：TTS 音色，默认 `vivian`
- `params.instructions`（可选）：播报风格 prompt，默认清新自然甜美语气
- `settings.voice_volume_percent`（可选，沿用现有设置）：本机音量，0 视为静音

执行链路：云端 `server.py` 校验并入队 → Pi `edge_action_poller.py` 取到 `type=speak` 的任务 → 调 `https://www.wangyutang.cn/common/api/tts/speech` 合成 WAV → `aplay` 到本机 USB 音箱 → 上报任务结果。**不经** `edge_ros_controller.py` 的 `/execute`。

已知边界：

- 只在装了 `edge_action_poller.py` 且接了音箱的机器人（turbopi-01）上生效；WonderEcho Pro 是独立设备，仍走 `audio_interact` 的 `/api/device/{id}/broadcast`（它要的是全双工 / barge-in 流式）
- `emergency_stop` 目前只清运动类待执行任务、不打断正在播放的 `aplay`；已排进队列的 `speak` 不会被急停取消
- `speak` 不受 `--no-voice` 影响（该开关只静音自动的动作完成提示音）


## 技能总览

| 能力 ID | 中文名称 | 类型 | 主要定义参数 | 主要执行方式 | 底层 Topic / 接口 |
|---|---|---|---|---|---|
| `emergency_stop` | 急停 | `base_stop` | 零速度 | 发布停止 Twist | `/cmd_vel`, `/controller/cmd_vel` |
| `reset_pose` | 复位 | `reset_pose` | 舵机回中 `1500` | 停车 + 舵机回中 | `/cmd_vel`, `/pwm_servo/set_state` |
| `remote_shutdown` | 远程关机 | `system_shutdown` | 关机验证码 | 边缘端执行关机 | `sudo shutdown -h now` |
| `rgb_on` | RGB 开灯 | `rgb_light` | `rgb_red/green/blue` | 发布 RGB 状态 | `/ros_robot_controller/set_rgb` + Sonar SDK |
| `rgb_off` | RGB 关灯 | `rgb_light` | RGB 全 0 | 发布 RGB 状态 | `/ros_robot_controller/set_rgb` + Sonar SDK |
| `front_distance` | 前方测距 | `front_distance` | 采样次数、保守取值 | 读取超声波传感器 | Sonar SDK `getDistance()` |
| `look_left` | 向左看 | `camera_servo` | servo 2, `delta=+100` | 发布 PWM 舵机状态 | `/ros_robot_controller/pwm_servo/set_state` |
| `look_right` | 向右看 | `camera_servo` | servo 2, `delta=-100` | 发布 PWM 舵机状态 | `/ros_robot_controller/pwm_servo/set_state` |
| `look_up` | 向上看 | `camera_servo` | servo 1, `delta=-100` | 发布 PWM 舵机状态 | `/ros_robot_controller/pwm_servo/set_state` |
| `look_down` | 向下看 | `camera_servo` | servo 1, `delta=+100` | 发布 PWM 舵机状态 | `/ros_robot_controller/pwm_servo/set_state` |
| `move_forward` | 前进 | `base_move` | `linear_x=0.35` | 发布 Twist burst 后停止 | `/cmd_vel`, `/controller/cmd_vel` |
| `move_backward` | 后退 | `base_move` | `linear_x=-0.35` | 发布 Twist burst 后停止 | `/cmd_vel`, `/controller/cmd_vel` |
| `move_left` | 左平移 | `base_move` | `linear_y=0.35` | 发布 Twist burst 后停止 | `/cmd_vel`, `/controller/cmd_vel` |
| `move_right` | 右平移 | `base_move` | `linear_y=-0.45` | 发布 Twist burst 后停止 | `/cmd_vel`, `/controller/cmd_vel` |
| `turn_left` | 左转 | `base_turn` | `angular_z=5.0` | 发布 Twist burst 后停止 | `/cmd_vel`, `/controller/cmd_vel` |
| `turn_right` | 右转 | `base_turn` | `angular_z=-5.0` | 发布 Twist burst 后停止 | `/cmd_vel`, `/controller/cmd_vel` |
| `rotate_in_place` | 原地旋转 | `base_turn` | `angular_z=5.0`, `motor_override` | Twist 路径或直接电机路径 | `/cmd_vel` 或 `/set_motor_speeds` |
| `move_diagonal_forward_left` | 左前斜移 | `base_move` | `linear_x=0.25`, `linear_y=0.25`, `motor_override` | Twist 路径或直接电机路径 | `/cmd_vel` 或 `/set_motor_speeds` |
| `move_diagonal_forward_right` | 右前斜移 | `base_move` | `linear_x=0.25`, `linear_y=-0.25`, `motor_override` | Twist 路径或直接电机路径 | `/cmd_vel` 或 `/set_motor_speeds` |
| `camera_snapshot` | 拍照 | `camera_snapshot` | camera server | 创建相机抓拍任务，Pi 端 sender 轮询后取帧上传 | `/camera/api/capture` -> `/camera/api/control` -> `/camera/api/frame` |
| `speak` | 说话 | `speak` | `params.text` / `params.voice` / `params.instructions` | Pi 端合成文本为 WAV 后本机播放 | `/common/api/tts/speech` -> `aplay` |

## 执行规则

| 类型 | 执行规则 |
|---|---|
| `base_move` | 按 `unit_distance_cm / 5 * 800ms / sensitivity` 计算时长，限制在 `180..3000ms`，发布 `Twist` 后发送零速度停止 |
| `base_turn` | 按 `turn_angle_deg / 5 * 450ms / sensitivity` 计算时长，限制在 `180..2500ms`，发布 `Twist` 后发送零速度停止 |
| `base_stop` | 立即设置 stop event，并连续发布零速度 `Twist` |
| `reset_pose` | 先停止底盘，再将 PWM servo 1/2 设置到 `1500` |
| `camera_servo` | 在当前舵机位置上做 `delta` 增量，并限制在技能定义的 `min..max` 范围内 |
| `rgb_light` | 按 settings 或 off 模式发布 RGB；同时尝试设置超声波模块的 RGB 灯 |
| `front_distance` | 读取多次超声波距离，默认取有效样本的最小值作为保守估计 |
| `camera_snapshot` | 在相机服务创建抓拍任务；Pi 端 `pi_camera_sender.py` 轮询任务、从 ROS/web-video-server 或相机后端取 JPEG，再上传到 `/api/frame` |
| `system_shutdown` | 由边缘 poller / executor 执行宿主机关机，云端创建任务时需要验证码 |
| `speak` | 边缘 poller 直接处理：拉取任务后调云端 TTS 合成 WAV，`aplay` 到本机音箱，不下发 `/execute`；`params.text` 必填且 ≤200 字，创建任务时要求边缘在线 |

## 运动类技能

### `move_forward`：前进

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_forward","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 定义文件 | `action_move/skill_catalog.json` |
| 能力类型 | `base_move` |
| 执行器 | `action_move/edge_ros_controller.py` |
| Twist | `linear.x=0.35`, `linear.y=0.0`, `angular.z=0.0` |
| Topic | `/cmd_vel`, `/controller/cmd_vel` |

```json
{
  "id": "move_forward",
  "type": "base_move",
  "twist": {
    "linear_x": 0.35,
    "linear_y": 0.0,
    "angular_z": 0.0
  }
}
```

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.35, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

停止：

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

### `move_backward`：后退

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_backward","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_move` |
| Twist | `linear.x=-0.35`, `linear.y=0.0`, `angular.z=0.0` |
| Topic | `/cmd_vel`, `/controller/cmd_vel` |

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: -0.35, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

### `move_left`：左平移

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_left","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_move` |
| Twist | `linear.x=0.0`, `linear.y=0.35`, `angular.z=0.0` |
| Topic | `/cmd_vel`, `/controller/cmd_vel` |

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.35, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

### `move_right`：右平移

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_right","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_move` |
| Twist | `linear.x=0.0`, `linear.y=-0.45`, `angular.z=0.0` |
| Topic | `/cmd_vel`, `/controller/cmd_vel` |

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: -0.45, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

### `turn_left`：左转

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"turn_left","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_turn` |
| Twist | `linear.x=0.0`, `linear.y=0.0`, `angular.z=5.0` |
| Topic | `/cmd_vel`, `/controller/cmd_vel` |

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 5.0}}"
```

### `turn_right`：右转

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"turn_right","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_turn` |
| Twist | `linear.x=0.0`, `linear.y=0.0`, `angular.z=-5.0` |
| Topic | `/cmd_vel`, `/controller/cmd_vel` |

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: -5.0}}"
```

## 直接电机 / 特殊运动技能

这些技能在 `skill_catalog.json` 中同时保留了 `twist` 和 `motor_override`。`twist` 可走 `/cmd_vel`；`motor_override` 用于直接发布四电机速度，适合原地旋转、斜向位移等普通 Twist 难以稳定表达的动作。

> 备注：`action_move_executor.py` 的 fallback 路径会检测 `motor_override`，并直接发布 `/ros_robot_controller/set_motor_speeds`。`edge_ros_controller.py` 当前主路径也支持原生电机速度发布。

### `rotate_in_place`：原地旋转

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"rotate_in_place","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_turn` |
| Twist | `angular.z=5.0` |
| motor_override | M1=35, M2=35, M3=35, M4=35 |
| 直接电机时长 | `2000ms` |

```json
{
  "id": "rotate_in_place",
  "type": "base_turn",
  "twist": {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 5.0},
  "motor_override": {
    "speeds": [
      {"id": 1, "speed": 35},
      {"id": 2, "speed": 35},
      {"id": 3, "speed": 35},
      {"id": 4, "speed": 35}
    ],
    "duration_ms": 2000
  }
}
```

### `move_diagonal_forward_left`：左前斜移

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_diagonal_forward_left","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_move` |
| Twist | `linear.x=0.25`, `linear.y=0.25`, `angular.z=0.0` |
| motor_override | M1=0, M2=50, M3=-50, M4=0 |
| 直接电机时长 | `300ms` |

```json
{
  "id": "move_diagonal_forward_left",
  "type": "base_move",
  "twist": {"linear_x": 0.25, "linear_y": 0.25, "angular_z": 0.0},
  "motor_override": {
    "speeds": [
      {"id": 1, "speed": 0},
      {"id": 2, "speed": 50},
      {"id": 3, "speed": -50},
      {"id": 4, "speed": 0}
    ],
    "duration_ms": 300
  }
}
```

### `move_diagonal_forward_right`：右前斜移

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_diagonal_forward_right","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_move` |
| Twist | `linear.x=0.25`, `linear.y=-0.25`, `angular.z=0.0` |
| motor_override | M1=-50, M2=0, M3=0, M4=50 |
| 直接电机时长 | `300ms` |

```json
{
  "id": "move_diagonal_forward_right",
  "type": "base_move",
  "twist": {"linear_x": 0.25, "linear_y": -0.25, "angular_z": 0.0},
  "motor_override": {
    "speeds": [
      {"id": 1, "speed": -50},
      {"id": 2, "speed": 0},
      {"id": 3, "speed": 0},
      {"id": 4, "speed": 50}
    ],
    "duration_ms": 300
  }
}
```

直接电机指令形式：

```bash
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: -50.0}, {id: 2, speed: 0.0}, {id: 3, speed: 0.0}, {id: 4, speed: 50.0}]}"
```

停止电机：

```bash
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: 0.0}, {id: 2, speed: 0.0}, {id: 3, speed: 0.0}, {id: 4, speed: 0.0}]}"
```

## 停止和复位技能

### `emergency_stop`：急停

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"emergency_stop","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_stop` |
| 执行器 | `edge_ros_controller.py` |
| 动作 | 设置 stop event，并发布零速度 Twist |
| Topic | `/cmd_vel`, `/controller/cmd_vel` |

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

### `reset_pose`：复位

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"reset_pose","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `reset_pose` |
| 执行动作 | 底盘停止，PWM servo 1/2 回到 `1500` |
| Topic | `/cmd_vel`, `/ros_robot_controller/pwm_servo/set_state` |

```bash
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state \
  ros_robot_controller_msgs/msg/SetPWMServoState \
  "{duration: 0.35, state: [{id: [1], position: [1500], offset: []}, {id: [2], position: [1500], offset: []}]}"
```

## 摄像头舵机技能

| 能力 ID | 中文名称 | 舵机 | 轴向 | 增量 | 限制范围 |
|---|---|---|---|---|---|
| `look_left` | 向左看 | servo 2 | pan | `+100` | `1200..1800` |
| `look_right` | 向右看 | servo 2 | pan | `-100` | `1200..1800` |
| `look_up` | 向上看 | servo 1 | tilt | `-100` | `1000..1700` |
| `look_down` | 向下看 | servo 1 | tilt | `+100` | `1000..1700` |

执行逻辑：

```text
读取当前 servo 位置
  -> 加上 delta
  -> clamp 到 min..max
  -> 发布 SetPWMServoState
  -> 可选触发 camera_snapshot
```

ROS2 指令形式：

```bash
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state \
  ros_robot_controller_msgs/msg/SetPWMServoState \
  "{duration: 0.35, state: [{id: [2], position: [1600], offset: []}]}"
```

### `look_left`?向左看

云端任务指令：

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks   -H "Content-Type: application/json"   -d '{"action":"look_left","source":"manual"}'
```

底层 ROS2 原子指令形式：
```bash
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state   ros_robot_controller_msgs/msg/SetPWMServoState   "{duration: 0.35, state: [{id: [2], position: [1600], offset: []}]}"
```

### `look_right`?向右看

云端任务指令：

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks   -H "Content-Type: application/json"   -d '{"action":"look_right","source":"manual"}'
```

底层 ROS2 原子指令形式：
```bash
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state   ros_robot_controller_msgs/msg/SetPWMServoState   "{duration: 0.35, state: [{id: [2], position: [1400], offset: []}]}"
```

### `look_up`?向上看

云端任务指令：

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks   -H "Content-Type: application/json"   -d '{"action":"look_up","source":"manual"}'
```

底层 ROS2 原子指令形式：
```bash
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state   ros_robot_controller_msgs/msg/SetPWMServoState   "{duration: 0.35, state: [{id: [1], position: [1400], offset: []}]}"
```

### `look_down`?向下看

云端任务指令：

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks   -H "Content-Type: application/json"   -d '{"action":"look_down","source":"manual"}'
```

底层 ROS2 原子指令形式：
```bash
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state   ros_robot_controller_msgs/msg/SetPWMServoState   "{duration: 0.35, state: [{id: [1], position: [1600], offset: []}]}"
```

## RGB 和传感器技能

### `rgb_on`：RGB 开灯

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"rgb_on","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `rgb_light` |
| RGB 来源 | settings 中的 `rgb_red`, `rgb_green`, `rgb_blue` |
| 默认颜色 | `200, 200, 200` |
| Topic | `/ros_robot_controller/set_rgb` |
| 额外动作 | 使用 Sonar SDK 设置超声波模块 RGB |

```bash
ros2 topic pub --once /ros_robot_controller/set_rgb \
  ros_robot_controller_msgs/msg/RGBStates \
  "{states: [{index: 1, red: 200, green: 200, blue: 200}, {index: 2, red: 200, green: 200, blue: 200}]}"
```

### `rgb_off`：RGB 关灯

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"rgb_off","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `rgb_light` |
| RGB 值 | `0, 0, 0` |
| Topic | `/ros_robot_controller/set_rgb` |

```bash
ros2 topic pub --once /ros_robot_controller/set_rgb \
  ros_robot_controller_msgs/msg/RGBStates \
  "{states: [{index: 1, red: 0, green: 0, blue: 0}, {index: 2, red: 0, green: 0, blue: 0}]}"
```

### `front_distance`: front sonar distance

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"front_distance","source":"manual"}'
```

#### 1. Atomic Skill Definition

| Item | Value |
|---|---|
| Skill ID | `front_distance` |
| Type | `front_distance` |
| Definition file | `action_move/skill_catalog.json` |
| Executor | `action_move/edge_ros_controller.py` |
| Fallback executor | `action_move/action_move_executor.py` |
| Hardware API | Hiwonder Sonar SDK |
| Core call | `sdk.sonar.Sonar().getDistance()` |
| Default samples | `7` |
| Default interval | `40ms` |
| Default strategy | Filter valid samples, take the minimum mm value, return cm |

```json
{
  "id": "front_distance",
  "type": "front_distance"
}
```

#### 2. Atomic Skill Execution

| Stage | Location / API | Purpose |
|---|---|---|
| Create cloud action task | `POST /action/api/tasks` | Create a `front_distance` task |
| Poll cloud action task | `edge_action_poller.py` | Pi side claims the task |
| Execute locally | `POST http://127.0.0.1:8765/execute` | `edge_ros_controller.py` handles the skill |
| Read sensor | `Sonar().getDistance()` | Read distance from the ultrasonic module in mm |
| Filter samples | `0 < value <= 5000` | Drop invalid readings |
| Convert result | `raw_mm / 10.0` | Return `front_distance_estimate_cm` |

Execution flow:

```text
front_distance
  -> edge_action_poller.py claims the task
  -> edge_ros_controller.py calls read_front_distance()
  -> Sonar().getDistance() samples 7 times
  -> filter invalid values outside 0..5000mm
  -> take min(valid_samples) by default
  -> convert mm to cm
  -> return front_distance_estimate_cm / raw_mm_samples / confidence in task output
```

#### 3. Command Forms

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"front_distance","source":"manual"}'
```

Pi local action-controller command:

```bash
curl -X POST http://127.0.0.1:8765/execute \
  -H "Content-Type: application/json" \
  -d '{"action":"front_distance","settings":{"sonar_distance_samples":7,"sonar_distance_sample_interval_ms":40}}'
```

Lowest-level SDK command form:

```bash
python3 - <<'PY'
import time
from sdk.sonar import Sonar

sonar = Sonar()
values = []
for _ in range(7):
    value = int(sonar.getDistance())  # millimeters
    if 0 < value <= 5000:
        values.append(value)
    time.sleep(0.04)

if not values:
    raise SystemExit('no valid sonar samples')

raw_mm = min(values)
print(f'front_distance_estimate_cm={raw_mm / 10.0:.2f}')
print('raw_mm_samples=' + ','.join(str(v) for v in values))
print(f'confidence={len(values) / 7:.3f}')
PY
```

edge controller debug command:

```bash
curl http://raspberrypi:8088/api/sonar
```

#### 4. Layered Understanding

| Layer | Example |
|---|---|
| Natural language / button | front distance / sonar distance |
| Task command | `{"action":"front_distance"}` |
| Atomic skill definition | `front_distance` in `skill_catalog.json` |
| Action executor | `edge_action_poller.py` + `edge_ros_controller.py` |
| Hardware SDK | `sdk.sonar.Sonar` |
| Atomic read call | `Sonar().getDistance()` |
| Data result | `front_distance_estimate_cm`, `raw_mm_samples`, `confidence` |

#### 5. Notes

| Item | Value |
|---|---|
| Essential nature | This is not a ROS2 `topic pub` control command; it is a sensor read through the Sonar SDK |
| Raw unit | `getDistance()` returns millimeters; the platform reports centimeters |
| Accuracy boundary | Camera frames cannot prove absolute distance accuracy; use a known-distance target or ruler for calibration |
| Relation to RGB | The same Sonar module also supports `setRGBMode()` / `setPixelColor()`, but this skill only reads distance |

### `camera_snapshot`: camera capture

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"camera_snapshot","source":"manual"}'
```

#### 1. Atomic Skill Definition

| Item | Value |
|---|---|
| Skill ID | `camera_snapshot` |
| Type | `camera_snapshot` |
| Definition file | `action_move/skill_catalog.json` |
| Action entrypoint | `edge_action_poller.py` |
| Fallback executor | `action_move/action_move_executor.py` |
| Camera task service | `camera_snapshot/server.py` |
| Pi-side frame executor | `camera_snapshot/pi_camera_sender.py` |

```json
{
  "id": "camera_snapshot",
  "type": "camera_snapshot"
}
```

#### 2. Atomic Skill Execution

| Stage | Location / API | Purpose |
|---|---|---|
| Create cloud action task | `POST /action/api/tasks` | Create a `camera_snapshot` task |
| Poll cloud action task | `edge_action_poller.py` | Pi side claims the task |
| Try local controller | `POST http://127.0.0.1:8765/execute` | Current `edge_ros_controller.py` has no dedicated `camera_snapshot` branch and may return 500 |
| Fallback execution | `action_move_executor.py` | Create a camera capture task |
| Create capture task | `POST /camera/api/capture` | Create a `kind=camera, mode=single` capture task |
| Pi-side polling | `GET /camera/api/control` | `pi_camera_sender.py` checks for pending capture tasks |
| Real frame capture | Pi local process | Read JPEG from ROS/web-video-server or camera backend |
| Upload frame | `POST /camera/api/frame` | Upload JPEG back to the camera service |
| Read result | `GET /camera/api/latest.jpg?kind=camera` | Download latest captured JPEG |

Real frame backends:

| Backend | Call form | Notes |
|---|---|---|
| `web-video-server` | `http://127.0.0.1:8080/snapshot?topic=/image_raw` | Current main path, fed by ROS `usb_cam` publishing `/image_raw` |
| `rpicam-still` | local command | Raspberry Pi camera fallback |
| `picamera2` | Python Picamera2 API | Raspberry Pi camera fallback |
| `opencv` | `cv2.VideoCapture` | USB camera fallback |

Execution flow:

```text
camera_snapshot
  -> create action_move task
  -> edge_action_poller.py claims task
  -> edge_ros_controller.py currently does not directly support this skill
  -> fallback to action_move_executor.py
  -> POST /camera/api/capture creates capture task
  -> pi_camera_sender.py polls /camera/api/control
  -> Pi reads JPEG from web-video-server or camera backend
  -> POST /camera/api/frame uploads JPEG
  -> /camera/api/latest.jpg returns latest image
```

#### 3. Command Forms

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"camera_snapshot","source":"manual"}'
```

Camera capture task command:

```bash
curl -X POST https://www.wangyutang.cn/camera/api/capture \
  -H "Content-Type: application/json" \
  -d '{"kind":"camera","mode":"single","query_gpio":26}'
```

Pi-side camera task poll command:

```bash
curl https://www.wangyutang.cn/camera/api/control
```

Pi local JPEG snapshot command:

```bash
curl -o frame.jpg \
  "http://127.0.0.1:8080/snapshot?topic=/image_raw"
```

Lowest-level ROS2 image-stream command form:

```bash
# Run inside the turbopi container
source /opt/ros/humble/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash

# Confirm image topics exist
ros2 topic list | grep -E '/image_raw|/camera_info'

# Inspect publisher/subscriber counts and message type
ros2 topic info /image_raw -v
ros2 topic type /image_raw

# Expected type: sensor_msgs/msg/Image
```

Start the USB camera ROS node if `/image_raw` has no publisher:

```bash
source /opt/ros/humble/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
export need_compile=False
ros2 launch peripherals usb_cam.launch.py
```

Start the ROS image-to-HTTP bridge if `web_video_server` is not running:

```bash
source /opt/ros/humble/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
ros2 run web_video_server web_video_server
```

Lowest-level data path:

```text
/dev/videoX
  -> usb_cam ROS2 node
  -> /image_raw sensor_msgs/msg/Image
  -> web_video_server
  -> http://127.0.0.1:8080/snapshot?topic=/image_raw
  -> JPEG frame.jpg
```

Read latest uploaded image:

```bash
curl -o latest.jpg \
  "https://www.wangyutang.cn/camera/api/latest.jpg?kind=camera"
```

#### 4. Layered Understanding

| Layer | Example |
|---|---|
| Natural language / button | take photo |
| Task command | `{"action":"camera_snapshot"}` |
| Atomic skill definition | `camera_snapshot` in `skill_catalog.json` |
| Action executor | `edge_action_poller.py` + `action_move_executor.py` |
| Camera task service | `camera_snapshot/server.py` |
| Pi-side frame executor | `pi_camera_sender.py` |
| ROS2 image topic | `/image_raw` (`sensor_msgs/msg/Image`) |
| Real frame backend | `web-video-server` / `rpicam-still` / `picamera2` / `opencv` |
| Upload result | `POST /camera/api/frame` |
| Hardware / data result | Current camera JPEG frame |

#### 5. Notes

| Item | Value |
|---|---|
| Essential nature | This is not a ROS2 `topic pub` control command; it reads an image stream and exports a JPEG |
| Real executor | `pi_camera_sender.py` on the Raspberry Pi |
| Main image source | ROS `web_video_server` exporting `/image_raw` |
| Current warning source | `edge_ros_controller.py` lacks a dedicated `camera_snapshot` branch, so the poller may see 500 before fallback succeeds |
| Documentation conclusion | Camera pipeline works; add a controller branch for `camera_snapshot` to remove the warning |

### `remote_shutdown`：远程关机

云端任务指令：
```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"remote_shutdown","verification_code":"123","source":"manual"}'
```

| 项目 | 内容 |
|---|---|
| 能力类型 | `system_shutdown` |
| 云端保护 | 创建任务时需要验证码 `123` |
| 执行位置 | Pi 宿主机 |
| 底层指令 | `sudo shutdown -h now` |

云端任务指令：

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"remote_shutdown","verification_code":"123","source":"manual"}'
```

## 后续新增技能的文档模板

````md
### `skill_id`：中文名称

| 项目 | 内容 |
|---|---|
| 能力类型 | `base_move` |
| 定义文件 | `action_move/skill_catalog.json` |
| 执行器 | `action_move/edge_ros_controller.py` |
| Topic / 接口 | `/cmd_vel` |

```json
{
  "id": "skill_id",
  "type": "base_move"
}
```

```bash
curl -X POST http://127.0.0.1:8765/execute \
  -H "Content-Type: application/json" \
  -d '{"action":"skill_id"}'
```
````

## 实机相机验证记录

验证时间：`2026-07-05 22:29` 到 `22:52`。

验证入口：

| 项目 | 值 |
|---|---|
| 动作服务 | `https://www.wangyutang.cn/action` |
| 相机服务 | `https://www.wangyutang.cn/camera` |
| 设备 | `turbopi-01` / `raspberrypi` |
| 相机来源 | `web-video-server` |
| 验证目录 | `action_move/skill_camera_verify/20260705_222912/` |
| 完整结果 | `action_move/skill_camera_verify/20260705_222912/summary.json` |

验证前将动作设置临时降到低风险档，验证后已恢复原设置：

| 设置项 | 验证时 | 验证后恢复 |
|---|---:|---:|
| `unit_distance_cm` | `1.0` | `10.0` |
| `turn_angle_deg` | `1.0` | `5.0` |
| `sensitivity` | `2.0` | `0.5` |
| `voice_volume_percent` | `0.0` | `0.0` |
| `rgb_red/green/blue` | `255/255/255` | `0/0/0` |

判定方式：

| 判定项 | 说明 |
|---|---|
| 任务状态 | `action_move` 云端任务返回 `complete` |
| 相机帧 | 每个技能保存 `before.jpg` 和 `after.jpg`，且后帧为有效 JPEG |
| 图像差异 | 对裁剪后的灰度图计算 `changed_percent_gt8` |
| 传感器类 | `front_distance` 以任务输出的超声波读数为主，相机只证明验证期间相机链路可用 |
| 危险类 | `remote_shutdown` 不执行，只记录为安全跳过 |

### 严格准确性结论

不能把“任务完成 + 相机前后图有变化”等同于“每个技能都准确”。相机前后变化适合验证运动、转向、舵机这类会改变画面的技能；对急停、测距、RGB 灯和关机这类技能，相机只能作为辅助证据。

| 技能类别 | 是否能仅靠相机前后变化证明准确 | 严格结论 |
|---|---|---|
| 底盘移动 / 转向 | 基本可以 | 前后图变化明显，可证明动作有效；若要证明厘米/角度绝对准确，还需要尺子、地面标尺、AprilTag、外部定位或里程计标定 |
| 摄像头舵机 | 可以 | 前后画面方向变化明显，可证明舵机动作有效 |
| `camera_snapshot` | 可以验证相机链路 | 能证明相机获得新帧；不能证明动作控制器链路无告警 |
| `front_distance` | 不可以 | 相机不能证明超声波距离绝对准确；需要实物标尺或已知距离目标 |
| `rgb_on` / `rgb_off` | 不可以 | 当前相机没有专门对准 RGB 灯，不能用画面严格证明灯光开关准确 |
| `emergency_stop` | 不可以 | 急停本质是停止/无动作，需要运动中触发或读取 `/cmd_vel`/电机状态来证明 |
| `remote_shutdown` | 不可以 | 该技能会关机，未执行；需要维护窗口或 mock 验证 |

### 相机变化验证总表

| 能力 ID | 任务状态 | 相机差异 | 相机验证结论 | 严格准确性 |
|---|---|---:|---|---|
| `emergency_stop` | `complete` | `4.80%` | 帧有效，任务完成 | 不能仅靠相机证明急停准确 |
| `reset_pose` | `complete` | `4.77%` | 帧有效，任务完成 | 可辅助证明复位执行；需舵机角度标定证明绝对位置 |
| `rgb_on` | `complete` | `5.06%` | 帧有效，任务完成 | 不能仅靠当前相机画面证明灯光准确开启 |
| `rgb_off` | `complete` | `6.03%` | 帧有效，任务完成 | 不能仅靠当前相机画面证明灯光准确关闭 |
| `front_distance` | `complete` | `4.71%` | 帧有效，任务完成 | 读数链路有效；距离绝对准确性未由相机证明 |
| `look_left` | `complete` | `92.28%` | 通过 | 有效，画面方向变化明显 |
| `look_right` | `complete` | `82.77%` | 通过 | 有效，画面方向变化明显 |
| `look_up` | `complete` | `47.29%` | 通过 | 有效，画面方向变化明显 |
| `look_down` | `complete` | `38.86%` | 通过 | 有效，画面方向变化明显 |
| `move_forward` | `complete` | `34.27%` | 通过 | 有效；厘米级准确性需标尺/定位补证 |
| `move_backward` | `complete` | `45.73%` | 通过 | 有效；厘米级准确性需标尺/定位补证 |
| `move_left` | `complete` | `50.48%` | 通过 | 有效；厘米级准确性需标尺/定位补证 |
| `move_right` | `complete` | `49.81%` | 通过 | 有效；厘米级准确性需标尺/定位补证 |
| `turn_left` | `complete` | `91.28%` | 通过 | 有效；角度绝对准确性需角度标定补证 |
| `turn_right` | `complete` | `91.35%` | 通过 | 有效；角度绝对准确性需角度标定补证 |
| `rotate_in_place` | `complete` | `91.72%` | 通过 | 有效；旋转角度准确性需角度标定补证 |
| `move_diagonal_forward_left` | `complete` | `61.14%` | 通过 | 有效；斜向距离/方向准确性需地面标尺补证 |
| `move_diagonal_forward_right` | `complete` | `50.26%` | 通过 | 有效；斜向距离/方向准确性需地面标尺补证 |
| `camera_snapshot` | `complete` | `3.45%` | 部分通过 | 相机链路获得新帧；任务记录含 `controller unavailable: HTTP Error 500` 告警 |
| `remote_shutdown` | 未执行 | - | 安全跳过 | 未验证执行，防止关闭树莓派 |

### 结果文件结构

每个已执行技能都有独立目录：

```text
action_move/skill_camera_verify/20260705_222912/<skill_id>/
  before.jpg
  after.jpg
  result.json
```

其中 `result.json` 包含任务 ID、任务状态、错误信息、前后帧 ID、图片大小和图像差异指标。
