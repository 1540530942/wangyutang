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
  ├─ /audio/*   → robot-sandbox:8095
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

## Spark GPU 服务器：同一个模型的两条独立调用路径

`spark-c9a7`（100.97.66.46，Tailscale）上跑的 vLLM 模型服务，有**两条完全独立、互不知情**的调用路径。这一点不在任何单一服务的代码里体现，只能靠拓扑文档记录，否则换模型/改端口时很容易只顾一头：

```
spark-c9a7 (100.97.66.46, Tailscale)
└─ vllm-qwen36-nvfp4 :8000   本地 vLLM 推理服务 (served-model-name=qwen3.6-35b-a3b)
     │
     ├─ 路径 A：经腾讯云公网网关，可审计
     │    common-api-spark-c9a7-tunnel.service (腾讯云 127.0.0.1:18000)
     │    → common_api_manager: spark_qwen_chat / spark_qwen_vision
     │    → 公网 https://www.wangyutang.cn/common/api/llm/qwen3.6-35b/chat 等
     │    （Caddy 网关日志 + common-api systemd journal 都能看到调用记录）
     │
     └─ 路径 B：spark 本机直连，公网日志完全看不到
          hermes-gateway.service (~/.hermes/, 不在本仓库)
          → OpenAI 兼容客户端直连 http://127.0.0.1:8000/v1
          → 微信 (weixin) / 钉钉 (dingtalk) 机器人网关
          （只能看 ~/.hermes/logs/agent.log，Caddy/common-api 日志里查不到）
```

**已知代价**：2026-08-18 排查模型切换影响面时，起初按路径 A 的日志判断"7 天零外部流量"，结论是错的——路径 B 当时正有真实微信对话在跑，只是完全不走公网网关。改 `served-model-name` 或换端口前，必须同时检查两条路径的消费方，尤其是 `~/.hermes/config.yaml` 里的 `model.default` 和 `custom_providers[].model`（两处都硬编码了模型名，不同步改会导致微信/钉钉机器人直接报错）。详见 [docs/logs/2026-08-18-spark-hermes-dependency-discovery.md](../docs/logs/2026-08-18-spark-hermes-dependency-discovery.md)。
