# pose_tracker

轻量级机器人位姿估计服务。接收 IMU 四元数和 `/cmd_vel` 速度指令，融合推算当前位置（x/y/yaw），通过 WebSocket 实时推送到浏览器。

## 架构

```
turbopi 容器
  pi_imu_sender.py
    ├─ subscribe /ros_robot_controller/imu_raw  →  POST /api/imu
    └─ subscribe /cmd_vel                       →  POST /api/cmd_vel
                                                        │
                                                pose_tracker server (port 8300)
                                                  PoseEstimator
                                                    ├─ yaw  ← IMU 四元数（优先）
                                                    └─ x/y  ← cmd_vel 积分（dead-reckoning）
                                                        │
                                                    WebSocket /ws  →  浏览器
```

## 组件

| 文件 | 说明 |
|---|---|
| `server.py` | FastAPI 服务，端口 8300；接收 IMU/cmd_vel，广播位姿到所有 WebSocket 客户端 |
| `pose_estimator.py` | 位姿估计核心：IMU 四元数 → yaw，cmd_vel 积分 → x/y，保留最近 2000 个轨迹点 |
| `pi_imu_sender.py` | 容器内 ROS2 节点，订阅 IMU 和 /cmd_vel，异步 POST 到 pose_tracker server |
| `static/index.html` | 浏览器实时可视化页面，WebSocket 接收位姿并绘制轨迹 |

## 启动

**服务端**（云端或本地机器）：

```bash
pip install fastapi uvicorn
python pose_tracker/server.py         # 监听 0.0.0.0:8300
```

**Pi 端发送器**（turbopi 容器内）：

```bash
docker exec -it -u ubuntu turbopi bash
source /opt/ros/humble/setup.bash && source /home/ubuntu/ros2_ws/install/setup.bash
python3 /path/to/pi_imu_sender.py --server http://<server-ip>:8300
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/imu` | 接收四元数 `{orientation: {x,y,z,w}}` |
| `POST` | `/api/cmd_vel` | 接收速度 `{linear_x, linear_y, angular_z}` |
| `POST` | `/api/reset` | 重置位姿到原点 |
| `GET` | `/api/pose` | 返回当前位姿快照 |
| `WS` | `/ws` | 实时推送位姿（每次 IMU 或 cmd_vel 更新触发） |

位姿快照格式：

```json
{
  "x": 0.123,
  "y": -0.045,
  "yaw_deg": 47.2,
  "dist_cm": 13.1,
  "imu_active": true,
  "trail": [{"x": 0.0, "y": 0.0, "yaw": 0.0}, ...],
  "description": "距起点 13 cm，偏左前方（47°），数据源：IMU融合"
}
```

## 位姿估计逻辑

- **yaw**：IMU 有数据时从四元数直接提取（`atan2` 方法），比 cmd_vel 积分精确
- **x/y**：对 cmd_vel 做时间积分，方向由当前 yaw 旋转（世界坐标系）
- **dead-reckoning 误差**：无 IMU 时 yaw 也用 cmd_vel angular_z 积分，累积误差较大；长距离运动后建议 `POST /api/reset` 重置
