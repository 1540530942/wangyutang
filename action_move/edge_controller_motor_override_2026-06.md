# edge_ros_controller 运动控制改进记录（2026-06）

## 背景：容器内的原子指令层级

TurboPi 的 ROS2 运动控制从高到低分三层：

```
/cmd_vel  (geometry_msgs/Twist)
    │
    ▼
mecanum_chassis_node          ← 做麦克纳姆运动学换算
    │  linear_x, linear_y, angular_z → 4轮速度
    ▼
/ros_robot_controller/set_motor_speeds  (MotorsSpeedControl)   ← 真正的原子层
    │
    ▼
ros_robot_controller          ← 硬件驱动，Board.set_motor_duty()
    │
    ▼
4个电机
```

其他原子 topic：

| topic | 消息类型 | 控制对象 |
|---|---|---|
| `/ros_robot_controller/set_motor_speeds` | `MotorsSpeedControl` | 4轮电机（最底层） |
| `/ros_robot_controller/pwm_servo/set_state` | `SetPWMServoState` | 摄像头舵机 PWM |
| `/ros_robot_controller/set_rgb` | `RGBStates` | LED |
| `/image_raw` | `sensor_msgs/Image` | 相机原始帧（只读） |

## 进程与网络拓扑

```
Pi 宿主机（Docker network=host）
├── edge_action_poller.py       轮询云端任务，发心跳，播报语音
│     └─ HTTP POST 127.0.0.1:8765/execute ──▶ edge_ros_controller
├── fc_server.py  (:8088)       底层手动控制 Web UI（/home/pi/fc_server.py）
│     ├─ 常规动作 ──▶ HTTP 8765 edge_ros_controller
│     └─ /api/motors ──▶ docker exec subprocess ros2 topic pub（慢）
│
turbopi 容器（host 网络，共享 127.0.0.1）
├── edge_ros_controller.py (:8765)   持久 rclpy Node，HTTP 服务
│     ├─ cmd_vel_pubs        持久 publisher → /cmd_vel
│     ├─ servo_pub           持久 publisher → /pwm_servo/set_state
│     ├─ rgb_pub             持久 publisher → /set_rgb
│     └─ motors_pub [新]    持久 publisher → /set_motor_speeds
├── mecanum_chassis_node    订阅 /cmd_vel → 发布 /set_motor_speeds
├── ros_robot_controller    订阅 /set_motor_speeds → 驱动电机
├── usb_cam                 发布 /image_raw
└── web_video_server        HTTP 视频流
```

## 发现的问题：motor_override 静默失效

`skill_catalog.json` 中部分技能定义了 `motor_override` 字段，用于精确控制各轮速度（绕过 mecanum_node 的运动学换算）：

- `rotate_in_place`：四轮同向同速，实现原地旋转
- `move_diagonal_forward_left`：M2/M3 驱动，左前 45° 合力
- `move_diagonal_forward_right`：M1/M4 驱动，右前 45° 合力

**改进前的路径（错误）：**

```
edge_action_poller
  → HTTP 8765 → edge_ros_controller.execute(base_move)
  → 没有 motor_override 分支，直接走 publish_twist_burst(/cmd_vel)
  → mecanum_node 做运动学换算（不是期望的轮速）
```

`motor_override` 在 controller 路径下被静默忽略。`action_move_executor.py` 里虽然有 `execute_motor_direct()`，但 poller 优先走 controller，executor 只在 controller 不可用时作为 fallback。

`fc_server.py` 的 `/api/motors` 走的是 `docker exec ros2 topic pub --once`（子进程，~200-400ms），且是从容器→宿主机→docker exec 回容器的迂回路径。

## 改进 A：在 controller 加持久化 motors publisher

### 变更内容（`edge_ros_controller.py`）

1. **新增 import**
   ```python
   from ros_robot_controller_msgs.msg import MotorSpeedControl, MotorsSpeedControl, ...
   ```

2. **`__init__` 加持久化 publisher**
   ```python
   self.motors_pub = self.create_publisher(
       MotorsSpeedControl, "/ros_robot_controller/set_motor_speeds", 10
   )
   ```

3. **新增 `publish_motor_override()` 方法**
   ```python
   def publish_motor_override(self, override):
       msg = MotorsSpeedControl()
       msg.data = [MotorSpeedControl(id=s["id"], speed=s["speed"]) for s in override["speeds"]]
       self.motors_pub.publish(msg)
       rclpy.spin_once(self, timeout_sec=0.0)
       time.sleep(override["duration_ms"] / 1000.0)
       # 发送停止帧
       stop_msg = MotorsSpeedControl()
       stop_msg.data = [MotorSpeedControl(id=i, speed=0.0) for i in [1, 2, 3, 4]]
       self.motors_pub.publish(stop_msg)
   ```

4. **`execute()` 加 motor_override 分支**（`base_move` 和 `base_turn` 均加）
   ```python
   elif skill["type"] == "base_move":
       self.stop_event.clear()
       if "motor_override" in skill:
           output.extend(self.publish_motor_override(skill["motor_override"]))
       else:
           # 原有 /cmd_vel burst 路径不变
   ```

5. **加 `camera_snapshot` dispatch**（同步之前仅在容器内的改动）

6. **加 `rclpy_spin` daemon 线程**（同步之前仅在容器内的改动）
   ```python
   spin_thread = threading.Thread(
       target=lambda: rclpy.spin(Handler.controller), daemon=True, name="rclpy_spin"
   )
   spin_thread.start()
   ```

### 改进后路径

```
edge_action_poller
  → HTTP 8765 → edge_ros_controller.execute(base_move/base_turn)
  → motor_override 存在：self.motors_pub.publish(MotorsSpeedControl)
  → /ros_robot_controller/set_motor_speeds（跳过 mecanum_node）
  → ros_robot_controller → 电机
```

延迟从 ~400ms（子进程）降到 <10ms（持久 publisher 直接发布）。

### 验证结果（2026-06-26）

```
rotate_in_place:
  ok=true, elapsed=2.004s
  output: motor_override duration_ms=2000 speeds=[35, 35, 35, 35]

move_diagonal_forward_left:
  ok=true, elapsed=0.302s
  output: motor_override duration_ms=300 speeds=[0, 50, -50, 0]

camera_snapshot:
  ok=true, elapsed=0.001s
```

## 未做的改进：合并 poller → controller（改进 B）

讨论过将 `edge_action_poller.py` 合并进 `edge_ros_controller.py`，使其同时承担云端轮询职责，消除一次本地 HTTP 跳转（~1ms）。

**结论：不做，原因：**

- poller 运行在宿主机，依赖 `systemctl`、`aplay`（语音播报）、`docker ps`（诊断收集）、`sudo shutdown`，这些在容器内均不可用
- 消除的延迟（loopback HTTP ~1ms）远小于改进 A 消除的延迟（子进程 ~400ms）
- 将网络 IO 和 ROS2 执行耦合在一个进程里，故障定位更难
- 当前分层清晰：poller 处理网络/系统，controller 处理 ROS2，职责边界合理

## 麦克纳姆轮运动学参考

| 方向 | linear_x | linear_y | M1 | M2 | M3 | M4 |
|---|---|---|---|---|---|---|
| 前进 | + | 0 | + | + | + | + |
| 后退 | - | 0 | - | - | - | - |
| 左平移 | 0 | + | - | + | - | + |
| 右平移 | 0 | - | + | - | + | - |
| 左前斜 | + | + | 0 | + | - | 0 |
| 右前斜 | + | - | - | 0 | 0 | + |
| 原地旋转 CCW | 0 | 0 | + | + | + | + |

`motor_override` 速度直接对应 `MotorSpeedControl.speed`，单位为设备 PWM duty（0~100），正负决定方向。
