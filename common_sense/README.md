# common_sense

通用知识与拓扑文档目录，存放不属于任何单一服务但对整体系统理解至关重要的参考资料。

## 文件

| 文件 | 说明 |
|---|---|
| `network_topology_remote_access.html` | 网络连接与远程访问拓扑图（HTML 可视化），描述腾讯云、Tailscale、Pi、Docker 容器之间的连通路径和访问方式 |

## 网络拓扑概览

```
公网用户
  │ HTTPS
  ▼
腾讯云 (110.40.154.41)
  │ robot_gateway (Caddy)  反向代理
  ├─ /camera/*  → camera-snapshot:8099
  ├─ /action/*  → action-move:8094
  ├─ /audio/*   → audio-recognition:8095
  ├─ /common/*  → common_api_manager:8101
  ├─ /robot/*   → pi5-robot:8093
  └─ /face/*    → smile-face:8096
         │
         │ Tailscale VPN / SSH ProxyJump
         ▼
  Raspberry Pi 5 (100.118.92.117)
  ├─ edge_action_poller (host)       轮询云端，下发动作
  ├─ turbopi 容器
  │    ├─ edge_ros_controller :8765  ROS2 持久节点
  │    ├─ mecanum_chassis_node       /cmd_vel → 电机
  │    ├─ ros_robot_controller       硬件驱动
  │    └─ usb_cam / web_video_server
  └─ fc_server :8088 (host)          底层手动控制 Web UI
```
