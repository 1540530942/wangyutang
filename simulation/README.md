# simulation

TurboPi 全功能仿真服务器，实现与真实 Pi 完全兼容的 HTTP 接口，用于在无硬件环境下测试整条控制链路。

## 兼容接口

| 接口 | 说明 |
|---|---|
| `POST /execute` | 等同 edge_ros_controller，接受技能指令并更新仿真状态 |
| `GET /health` | action server 健康检查 |
| `POST /api/capture` | 等同 camera_snapshot server，返回当前仿真帧 |
| `GET /api/latest` | 最新帧元数据 |
| `GET /api/latest.jpg` | 最新帧 JPEG（由 renderer 渲染） |
| `GET /api/sonar` | 前方距离传感器（仿真值，基于障碍物几何计算） |
| `POST /vision/analyze` | inspect_scene LLM 视觉（透传或 mock） |
| `GET /api/state` | 完整仿真状态 JSON |
| `WS /ws` | 实时状态推送（WebSocket） |
| `GET /` | Web UI（arena 可视化） |

## 组件

| 文件 | 职责 |
|---|---|
| `state.py` | `RobotState` 状态机：坐标(x,y)、heading、速度；arena 800×600，Y轴向下 |
| `renderer.py` | 将 RobotState 渲染为 JPEG 帧（含障碍物、机器人、朝向箭头） |
| `server.py` | asyncio HTTP + WebSocket 服务，端口默认 8766（`SIM_PORT` 覆盖） |
| `static/index.html` | Web UI，实时显示仿真 arena |

## 仿真场景

默认障碍物 5 个（圆形），位于 arena 四角和中央。机器人从 (400, 300) 出发，heading=0（正右）。碰撞检测：机器人圆（r=20）与障碍物圆重叠则标记 collision。

## 启动

```bash
# 默认端口 8766
python -m simulation.server

# 自定义端口
SIM_PORT=9000 python -m simulation.server
```

## 与真实 Pi 切换

在 `loop_engineering` 或测试代码中，将 `CONTROLLER_URL` 从 `http://127.0.0.1:8765` 改为 `http://127.0.0.1:8766` 即可切换到仿真后端，无需改动任何业务逻辑。
