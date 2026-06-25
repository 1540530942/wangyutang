# ROS2 原子指令参考

本文档说明如何进入 TurboPi 容器、执行 ROS2 原子指令，以及每条指令触发的实际效果。

---

## 如何进入容器

### 方式一：从宿主机 SSH 进入容器（推荐）

```bash
# 1. SSH 到树莓派宿主机
ssh pi@<pi-ip>           # 或通过 Tailscale: ssh pi-robot

# 2. 进入 turbopi 容器（交互式 bash）
docker exec -it -u ubuntu turbopi bash

# 3. 加载 ROS2 环境（每次进入都要执行）
source /opt/ros/humble/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
```

### 方式二：在宿主机上一次性执行容器内命令

```bash
ssh pi@<pi-ip> 'docker exec -u ubuntu turbopi bash -lc "
  source /opt/ros/humble/setup.bash && source /home/ubuntu/ros2_ws/install/setup.bash
  ros2 topic list
"'
```

### 确认环境正常

```bash
# 查看运行中的节点（应包含 mecanum_chassis_node、ros_robot_controller 等）
ros2 node list

# 查看可用 topic
ros2 topic list | grep -E 'cmd_vel|motor|servo|rgb|image'
```

预期输出：
```
/action_move_edge_controller
/mecanum_chassis_node
/ros_robot_controller
/startup_check_node
/usb_cam
/web_video_server
```

---

## 紧急停止（先记住这条）

**任何时候想让机器人停下来，执行这条：**

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

或直接写四轮速度为 0：

```bash
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: 0.0}, {id: 2, speed: 0.0}, {id: 3, speed: 0.0}, {id: 4, speed: 0.0}]}"
```

---

## 两条路径的区别

| 路径 | Topic | 经过节点 | 适合场景 |
|---|---|---|---|
| 高层速度 | `/cmd_vel` (Twist) | mecanum_chassis_node → ros_robot_controller | 标准前进/后退/转向/平移 |
| 电机直控 | `/ros_robot_controller/set_motor_speeds` (MotorsSpeedControl) | ros_robot_controller（直达） | 精确控制各轮速度比，斜向/旋转等特殊运动 |

`--once` 只发一帧，机器人短暂动一下后自然停止（约 200ms 内）。  
`--rate 10` 以 10Hz 持续发布，机器人持续运动直到手动停止。

---

## 底盘运动：/cmd_vel 路径

### 前进

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.35, y: 0.0, z: 0.0}, angular: {z: 0.0}}"
```

**效果**：四轮同向，机器人向正前方移动约 0.5~1cm（单帧）。  
**终端输出**：
```
publisher: beginning loop
publishing #1: geometry_msgs.msg.Twist(linear=..., angular=...)
```

持续前进 1 秒后停止：

```bash
ros2 topic pub --rate 10 /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.35, y: 0.0, z: 0.0}, angular: {z: 0.0}}" &
PID=$!; sleep 1; kill $PID
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {z: 0.0}}"
```

### 后退

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: -0.35, y: 0.0, z: 0.0}, angular: {z: 0.0}}"
```

**效果**：机器人向正后方移动。注意：后退在某些地面实测速度明显快于前进（重力/地面摩擦不对称），建议用较小速度值（-0.15）。

### 左平移 / 右平移（麦克纳姆轮特有）

```bash
# 左平移
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.35, z: 0.0}, angular: {z: 0.0}}"

# 右平移
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: -0.45, z: 0.0}, angular: {z: 0.0}}"
```

**效果**：机器人横向平移，车身朝向不变。左右速度值不对称（-0.45 vs 0.35）是因为右轮的物理阻力差异。

### 左转 / 右转

```bash
# 左转（逆时针）
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {z: 5.0}}"

# 右转（顺时针）
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {z: -5.0}}"
```

**效果**：机器人原地旋转，单帧约旋转 5~15°（取决于地面）。

---

## 电机直控：/set_motor_speeds 路径

直接控制 4 个轮子的速度，绕过 mecanum_node 的运动学换算。速度范围 `-100 ~ 100`（PWM duty %）。

**注意**：发一帧只持续瞬间，需要持续发或配合 sleep + 停止帧。

### 全速前进（四轮同向）

```bash
# 启动
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: 50.0}, {id: 2, speed: 50.0}, {id: 3, speed: 50.0}, {id: 4, speed: 50.0}]}"
sleep 0.5
# 停止
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: 0.0}, {id: 2, speed: 0.0}, {id: 3, speed: 0.0}, {id: 4, speed: 0.0}]}"
```

**终端输出**：
```
Waiting for at least 1 matching subscription(s)...
publisher: beginning loop
publishing #1: ros_robot_controller_msgs.msg.MotorsSpeedControl(data=[
  MotorSpeedControl(id=1, speed=50.0), MotorSpeedControl(id=2, speed=50.0), ...])
```

### 原地旋转（四轮同向同速 → 绕车中心自转）

```bash
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: 35.0}, {id: 2, speed: 35.0}, {id: 3, speed: 35.0}, {id: 4, speed: 35.0}]}"
sleep 2
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: 0.0}, {id: 2, speed: 0.0}, {id: 3, speed: 0.0}, {id: 4, speed: 0.0}]}"
```

**效果**：机器人原地逆时针旋转约 360°（2 秒 @ speed=35）。

### 斜向左前方（M2/M3 驱动，M1/M4=0）

```bash
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: 0.0}, {id: 2, speed: 50.0}, {id: 3, speed: -50.0}, {id: 4, speed: 0.0}]}"
sleep 0.3
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: 0.0}, {id: 2, speed: 0.0}, {id: 3, speed: 0.0}, {id: 4, speed: 0.0}]}"
```

**效果**：机器人向左前方 45° 移动约 5~8cm。

### 斜向右前方（M1/M4 驱动，M2/M3=0）

```bash
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: -50.0}, {id: 2, speed: 0.0}, {id: 3, speed: 0.0}, {id: 4, speed: 50.0}]}"
sleep 0.3
ros2 topic pub --once /ros_robot_controller/set_motor_speeds \
  ros_robot_controller_msgs/msg/MotorsSpeedControl \
  "{data: [{id: 1, speed: 0.0}, {id: 2, speed: 0.0}, {id: 3, speed: 0.0}, {id: 4, speed: 0.0}]}"
```

### 麦克纳姆轮各轮位置与符号

```
      前
  M1(左前)  M2(右前)
  M3(左后)  M4(右后)
      后

物理符号（mecanum.py 取反规则）：−M1 · M3 · −M2 · M4
实际发布 speed 为正 → 对应轮子正转方向
```

| 运动方向 | M1 | M2 | M3 | M4 |
|---|---|---|---|---|
| 前进 | + | + | + | + |
| 后退 | - | - | - | - |
| 左平移 | - | + | - | + |
| 右平移 | + | - | + | - |
| 左转(CCW) | + | + | + | + |
| 右转(CW) | - | - | - | - |
| 左前斜 | 0 | + | - | 0 |
| 右前斜 | - | 0 | 0 | + |

---

## 摄像头舵机：/pwm_servo/set_state

PWM 值范围 `500~2500`，中心 `1500`。servo 1 = 俯仰(Tilt)，servo 2 = 水平(Pan)。

```bash
# 向左看（Pan 增大）
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state \
  ros_robot_controller_msgs/msg/SetPWMServoState \
  "{duration: 0.5, state: [{id: [2], position: [1800], offset: []}]}"

# 向右看
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state \
  ros_robot_controller_msgs/msg/SetPWMServoState \
  "{duration: 0.5, state: [{id: [2], position: [1200], offset: []}]}"

# 向上看（Tilt 减小）
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state \
  ros_robot_controller_msgs/msg/SetPWMServoState \
  "{duration: 0.5, state: [{id: [1], position: [1000], offset: []}]}"

# 向下看（Tilt 增大）
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state \
  ros_robot_controller_msgs/msg/SetPWMServoState \
  "{duration: 0.5, state: [{id: [1], position: [1700], offset: []}]}"

# 两轴同时回中
ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state \
  ros_robot_controller_msgs/msg/SetPWMServoState \
  "{duration: 0.35, state: [{id: [1], position: [1500], offset: []}, {id: [2], position: [1500], offset: []}]}"
```

**效果**：`duration` 秒内舵机平滑移动到目标位置，无需等待即可发下一条。

---

## RGB 灯：/set_rgb

```bash
# 绿色（LED 1 和 2）
ros2 topic pub --once /ros_robot_controller/set_rgb \
  ros_robot_controller_msgs/msg/RGBStates \
  "{states: [{index: 1, red: 0, green: 255, blue: 0}, {index: 2, red: 0, green: 255, blue: 0}]}"

# 关灯
ros2 topic pub --once /ros_robot_controller/set_rgb \
  ros_robot_controller_msgs/msg/RGBStates \
  "{states: [{index: 1, red: 0, green: 0, blue: 0}, {index: 2, red: 0, green: 0, blue: 0}]}"
```

**效果**：机身 LED 立即变色，无延迟。index 1/2 对应机身两颗 RGB 灯；声纳灯（index 0/1）需通过 `sdk.Sonar` 单独控制。

---

## 传感器读取

### 查看超声波距离（持续输出）

```bash
ros2 topic echo /ros_robot_controller/sonar 2>/dev/null || \
python3 -c "
from sdk.sonar import Sonar; s=Sonar()
import time
while True:
    print(f'{s.getDistance()/10:.1f} cm'); time.sleep(0.2)
"
```

### 查看相机帧（确认 usb_cam 节点运行）

```bash
ros2 topic hz /image_raw          # 查看帧率，正常约 30Hz
ros2 topic info /image_raw -v     # 查看发布者
```

### 查看 IMU 数据

```bash
ros2 topic echo /ros_robot_controller/imu_raw --once
```

---

## 常见问题

**Q：执行 `ros2 topic pub` 后机器人没动？**  
先检查：`ros2 topic info /ros_robot_controller/set_motor_speeds -v`，确认 `ros_robot_controller` 节点是订阅者。如果 Subscription count=0，说明驱动节点没有启动。

**Q：`Waiting for at least 1 matching subscription(s)...` 一直等待？**  
bringup 还没完成，等待约 10 秒后 `ros_robot_controller` 节点会上线。

**Q：发 --once 后机器人动了很短就停了？**  
正常，`--once` 只发一帧（约 50ms），机器人动约 0.5~2cm 后自然停。想持续运动用 `--rate 10` + 后台运行。

**Q：在容器外如何用 curl 触发？**  
通过 edge_ros_controller HTTP 接口（127.0.0.1:8765）：
```bash
curl -s http://127.0.0.1:8765/execute \
  -X POST -H "Content-Type: application/json" \
  -d '{"action": "move_forward", "settings": {"unit_distance_cm": 5}}'
```
