# 模块合入规范检查记录

日期：2026-06-13

检查范围：`wangyutang_platform` 当前保留的机器人平台模块。

依据：[MODULE_MERGE_GUIDE.md](../MODULE_MERGE_GUIDE.md)

## 检查结论

仓库已聚焦到机器人平台运行链路。`paper_learning_system/`、`pi_slam/`、`remote_sensing/`、`control_platform/`、`llm_manager/` 已从活跃运行模块中移除。

当前合规检查以模块 README、`docker-compose.yml`、健康检查和部署脚本为准，不再依赖 `control_platform/modules/registry.json` 或 Caddy 路由。

## 模块检查表

| 模块 | README | 健康检查或替代验证 | Compose | 结论 |
| --- | --- | --- | --- | --- |
| `remote_control_cloud/` | 已有 | `/api/health` | 已配置 | 符合 |
| `remote_control_edge/` | 已有 | 本地服务、MQTT 桥、固件编译 | 不独立部署 | 符合 |
| `camera_snapshot/` | 已有 | `/api/health` | 已配置 | 符合 |
| `action_move/` | 已有 | `/api/health` | 已配置 | 符合 |
| `audio_recognition/` | 已有 | `/api/health` | 已配置 | 符合 |
| `pi5_robot/` | 已有 | `/api/health` | 已配置 | 符合 |
| `smile_face/` | 已有 | `/api/health` | 已配置 | 符合 |
| `common_api_manager/` | 已有 | 本地/systemd 运行说明 | 未纳入根 Compose | 例外记录 |

## 本次调整

- 移除统一门户、Web Manager、遥感、论文学习和 SLAM 实验模块。
- 根 `docker-compose.yml` 只保留机器人平台相关运行服务。
- 部署脚本改为按剩余服务端口做健康检查。
- 模块合入规范改为不依赖注册表和 Caddy。

## 已知例外

- `remote_control_edge/` 是本地硬件边缘侧模块，不提供容器健康检查。
- `common_api_manager/` 当前作为独立 systemd 服务记录，不属于根 Compose 运行链路。
