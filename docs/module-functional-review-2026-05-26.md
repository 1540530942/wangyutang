# Wangyutang Platform 模块功能审计与整改建议

日期：2026-05-26  
更新：2026-06-13，按机器人平台聚焦后的模块边界修订。

## 总体结论

仓库已从“统一门户 + 多实验模块”收敛为机器人平台运行仓库。当前核心链路包括远程控制、摄像头快照、动作控制、语音识别、Pi5 机器人控制台和表情屏。

已下线模块：

- `paper_learning_system`
- `pi_slam`
- `remote_sensing`
- `control_platform`
- `llm_manager`

下线后不再维护 Caddy 网关、模块注册表和 Web Manager 状态页。运行状态以 `docker-compose.yml`、模块健康检查和部署脚本为准。

## 模块功能描述

| 模块 | 功能定位 | 当前状态 | 主要入口 |
| --- | --- | --- | --- |
| `remote_control_cloud` | 远程控制云端：Vite 控制台、FastAPI 指令 API、MQTT Broker 配置。 | 可运行链路，面向云端/公网控制。 | Web `:5173`, API `:8000` |
| `remote_control_edge` | 远程控制边缘侧：PC 串口服务、MQTT 串口桥、Arduino/ESP32 固件工程。 | 边缘运行，不作为 Compose 服务。 | 本地 PC/串口/PlatformIO |
| `camera_snapshot` | TurboPi/Raspberry Pi 摄像头快照服务，支持单帧、持续上传、最新 JPEG 预览和 GPIO 状态上报。 | 可运行，依赖边缘上传器。 | `:8099` |
| `action_move` | TurboPi 动作队列和执行层，支持底盘前后左右、转向、急停、复位、摄像头云台动作。 | 可运行，含云端任务队列和树莓派轮询执行器。 | `:8094` |
| `audio_recognition` | 语音识别与动作路由监控，承接 ASR、技能路由、任务执行结果和摄像头预览。 | 可运行，含测试与回归脚本。 | `:8095` |
| `common_api_manager` | 公共 AI/API 适配层，统一 ASR、TTS、LLM、健康检查等对外接口。 | 独立 systemd 记录，未纳入根 Compose。 | 预期 `:8101` |
| `pi5_robot` | Raspberry Pi 5 巡查机器人 MVP 控制台，含 robotd/visiond/harnessd 仿真、任务日志和硬件接入骨架。 | 脚手架/仿真优先。 | `:8093` |
| `smile_face` | 机器人表情屏，提供 Canvas 表情、眨眼、说话动画和树莓派 LCD 同步显示。 | 可运行。 | `:8096` |
| `common_sense` | 网络拓扑、远程访问等公共认知/说明材料。 | 文档型辅助目录。 | 静态文档 |
| `scripts` | 部署、检查、发布辅助脚本。 | 工具目录。 | PowerShell/Shell |

## 高优先级整改建议

1. 收紧硬件控制鉴权和审计。
2. 清理运行产物，避免日志、截图、音频、缓存进入版本库。
3. 增加端到端健康检查，覆盖服务健康、关键 API schema、边缘在线状态和任务队列闭环。
4. 统一机器人动作安全边界，包括最大速度、最大持续时间、急停优先级和操作来源记录。
5. 明确公网暴露策略。如果后续需要域名网关，应作为新的独立网关模块引入，而不是恢复旧 `control_platform`。

## 分模块建议

### `remote_control_cloud`

- 区分 Vite 开发服务和生产静态构建。
- API 指令需要统一签名/鉴权和 replay 防护。
- MQTT topic、device id、命令 schema 建议集中定义。

### `remote_control_edge`

- 将串口号、设备 ID、MQTT 地址、token 放入本地配置模板。
- 增加边缘侧自检脚本。
- 固件和 PC 桥接应记录协议版本。

### `camera_snapshot`

- 生产环境强制上传 token。
- 图片保存策略区分最新帧、历史帧、验证帧和临时帧。
- GPIO 查询改为可配置清单。

### `action_move`

- 补充最大速度、最大持续时间、最小间隔和急停抢占策略。
- 高风险动作需要二次确认、独立权限和审计。
- 云端队列建议持久化最近任务。

### `audio_recognition`

- 统一“识别文本 -> intent -> tool call -> action result”的事件 envelope。
- 隔离真实硬件路径和 mock/regression 路径。
- 加强语音到动作链路的回放测试。

### `common_api_manager`

- 如需纳入机器人平台主运行链路，应补充 Compose 服务。
- 对 ASR/TTS/LLM 返回结构做稳定 schema。
- 增加 provider 健康检查、超时、重试、熔断和用量统计。

### `pi5_robot`

- 继续保持仿真先行。
- 真实硬件接入前完成安全测试。
- harnessd 的任务日志建议形成统一 episode schema。

### `smile_face`

- 表情状态接口支持来源优先级。
- LCD 客户端补充 systemd 示例和断线重连策略。
- 补充音频模块调用表情屏的集成测试。
