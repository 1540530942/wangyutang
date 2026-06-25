# function_center

Pi 宿主机上的底层直接控制服务，提供 Web UI 和 Python API 供手动调试机器人。

> **注意**：`function_center/server.py` 对应 Pi 上 `/home/pi/fc_server.py`（`python3 fc_server.py`，端口 8088）。`function_center/robot.py` 是可在 Pi 上 import 的 Python 封装库。

## 组件

| 文件 | 说明 |
|---|---|
| `server.py` | BaseHTTP Web 服务器，端口 8088，提供手动控制 UI 和 `/api/motors` 等接口 |
| `robot.py` | Python 封装库，`move_forward()`、`turn_left()`、`rgb_on()` 等函数，供脚本直接调用 |

## 执行路径

```
Web UI / robot.py 调用
  │
  ├─[常规动作]──▶ HTTP 8765 edge_ros_controller（持久 rclpy publisher，快）
  │
  └─[/api/motors]─▶ docker exec ros2 topic pub --once /set_motor_speeds（子进程，慢）
```

> `/api/motors` 的子进程路径已被 `edge_ros_controller` 的原生 `motor_override` 替代，
> 仅在调试或 controller 不可用时作为 fallback 使用。

## 典型用法（Pi 上）

```python
from function_center import robot

robot.move_forward(distance_cm=10)
robot.turn_left(angle_deg=45)
robot.rgb_on(r=255, g=128, b=0)
print(robot.front_distance())
```

## Web UI

浏览器打开 `http://<pi-ip>:8088`，可直接点按方向键控制底盘、调整舵机、读取传感器。Docker 容器与宿主机共享 host 网络，因此容器内也可访问 `127.0.0.1:8088`。

## ROS2 原子指令

容器内可直接用 `ros2 topic pub` 触发硬件动作，无需任何上层服务。详见 [ros2_command_examples.md](ros2_command_examples.md)，涵盖：

- 如何 SSH 进入 turbopi 容器并初始化 ROS2 环境
- 底盘运动（前/后/左平移/右平移/左右转）、电机直控、斜向平移
- 摄像头舵机、RGB 灯、超声波测距
- 紧急停止与常见问题
