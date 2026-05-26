# Wangyutang Platform 模块功能审计与整改建议

日期：2026-05-26

本文基于仓库目录、根 `docker-compose.yml`、`control_platform/infra/caddy/Caddyfile`、`control_platform/modules/registry.json` 以及各模块 README/入口文件梳理。目标是给出每个模块的功能定位、当前状态、主要风险和整改建议。

## 总体结论

平台已经形成“统一入口 + 独立模块 + Caddy 路由 + Docker Compose 编排”的基本形态，核心可运行模块包括控制平台、论文学习、远程控制、遥感脚手架、摄像头快照、动作控制、语音识别、Web Manager、Pi5 机器人和表情屏。

当前最需要优先处理的是：中文文档和注册表存在编码乱码；模块注册表、Caddy 路由和 Compose 服务没有完全对齐；运行日志、图片、音频、验证产物等需要从代码仓库中隔离；硬件控制链路需要统一安全边界、鉴权和审计。

## 模块功能描述

| 模块 | 功能定位 | 当前状态 | 主要入口 |
| --- | --- | --- | --- |
| `control_platform` | 统一平台门户、模块发现、健康聚合、Caddy/路由配置承载。 | 可运行，作为平台入口。 | `/`, `:8098`, `/api/modules`, `/api/modules/health` |
| `paper_learning_system` | 论文检索、论文结构化解读、问答、主动回忆练习，并带 Hermes LLM 网关。 | 可运行，具备离线 fallback。 | `/papers/`, `:8088`, Hermes `:8091` |
| `remote_control_cloud` | 远程控制云端：Vite 控制台、FastAPI 指令 API、MQTT Broker 配置。 | 可运行链路，面向云端/公网控制。 | `/remote/`, Web `:5173`, API `:8000` |
| `remote_control_edge` | 远程控制边缘侧：PC 串口服务、MQTT 串口桥、Arduino/ESP32 固件工程。 | 边缘运行，不作为 compose 公网服务。 | 本地 PC/串口/PlatformIO |
| `remote_sensing` | 遥感模块脚手架，提供健康检查、模块元数据和简易页面，预留影像、地图、时间轴、告警能力。 | 脚手架。 | `/sensing/`, `:8090` |
| `camera_snapshot` | TurboPi/Raspberry Pi 摄像头快照服务，支持单帧、持续上传、最新 JPEG 预览和 GPIO 状态上报。 | 可运行，依赖边缘上传器。 | `/camera/`, `:8099` |
| `action_move` | TurboPi 动作队列和执行层，支持底盘前后左右、转向、急停、复位、摄像头云台动作。 | 可运行，含云端任务队列和树莓派轮询执行器。 | `/action/`, `:8094` |
| `audio_recognition` | 语音识别与动作路由监控，承接 WonderEchoPro/ASR、ReAct/技能路由、任务执行结果和摄像头预览。 | 可运行，含测试与回归脚本。 | `/audio/`, `:8095` |
| `common_api_manager` | 平台公共 AI/API 适配层，统一 ASR、TTS、LLM、健康检查等对外接口。 | 代码和文档存在，但根 compose 未编排为服务。 | `/common/`, 预期 `:8101` |
| `llm_manager` | Web Manager：模块网页监控、开发进度、模型 Provider 管理、OpenAI 兼容聊天接口。 | 可运行，服务身份为 `web-manager`。 | `/web/`, `/llm/`, `:8092` |
| `pi5_robot` | Raspberry Pi 5 巡查机器人 MVP 控制台，含 robotd/visiond/harnessd 仿真、任务日志和硬件接入骨架。 | 脚手架/仿真优先。 | `/robot/`, `:8093` |
| `smile_face` | 机器人表情屏，提供 Canvas 表情、眨眼、说话动画和树莓派 LCD 同步显示。 | 可运行。 | `/face/`, `:8096` |
| `pi_slam` | SLAM/Nav2/LiDAR/深度相机方向预研占位模块。 | 扩展入口，无运行服务。 | 暂无 |
| `common_sense` | 网络拓扑、远程访问等公共认知/说明材料。 | 文档型辅助目录。 | 静态文档 |
| `scripts` | 部署、检查、发布辅助脚本。 | 工具目录。 | PowerShell/Shell |
| `WORKLOG` | 工作日志和阶段记录。 | 记录目录。 | Markdown |

## 高优先级整改建议

1. 修复中文编码乱码。
   - 影响文件包括多个 README 和 `control_platform/modules/registry.json`。
   - 建议统一为 UTF-8 无 BOM，增加一次编码修复提交，并在 CI 中加入 UTF-8/JSON 解析检查。

2. 对齐模块注册表、Compose 和 Caddy。
   - `common_api_manager` 有 `/common` 路由，但根 `docker-compose.yml` 没有对应 `common-api` 服务，Caddy 目前代理到 `172.18.0.1:8101`，部署可移植性弱。
   - `remote-control` 在 Compose/Caddy 中存在，但当前注册表输出中缺少对应模块项，门户展示会不完整。
   - 建议建立单一模块清单，生成或校验 registry、compose、Caddy 三处配置。

3. 收紧仓库运行产物管理。
   - 日志、截图、验证图片、缓存、上传音频、`__pycache__` 等不应进入版本库。
   - 已在 `.gitignore` 增加日志、data、dist、验证图片目录忽略规则；后续应清理已跟踪的运行产物。

4. 统一硬件控制鉴权和审计。
   - `action_move`、`camera_snapshot`、`audio_recognition`、`remote_control_*` 都可能触发真实设备动作。
   - 建议统一 token、权限级别、动作白名单、急停优先级、执行日志和操作来源记录。

5. 增加端到端健康检查。
   - 现有 `scripts/check-local.ps1` 覆盖范围有限。
   - 建议按模块补充：服务健康、路由健康、关键 API schema、硬件边缘在线状态、任务队列闭环。

## 分模块整改建议

### `control_platform`

- 将模块注册表改为可验证 schema，至少校验 `id/name/path_prefix/service_url/health_url/status`。
- `registry.json` 需要修复乱码，避免前端展示不可读。
- `api/modules/health` 应区分“未配置”“服务离线”“路由失败”“探测超时”，方便运维判断。

### `paper_learning_system`

- 保留 Hermes sidecar，但需要明确模型密钥、base URL、fallback 行为的优先级。
- 增加数据库迁移策略，避免 SQLite schema 随功能扩展后不可追踪。
- 补充核心 API 的回归测试：论文保存、分析、问答、Hermes fallback。

### `remote_control_cloud`

- 将 Vite 开发服务和生产静态构建区分开，公网部署建议使用构建产物而不是 dev server。
- API 指令需要统一签名/鉴权和 replay 防护。
- MQTT topic、device id、命令 schema 建议集中定义，避免前端、API、边缘桥各写一份。

### `remote_control_edge`

- 建议把串口号、设备 ID、MQTT 地址、token 全部放入 `.env.example` 或本地配置模板。
- 增加边缘侧自检脚本：串口可用、MQTT 可连、固件版本、最后一次状态上报。
- 固件和 PC 桥接应记录协议版本，云端可拒绝不兼容版本。

### `remote_sensing`

- 明确第一阶段数据模型：影像来源、空间坐标、时间戳、元数据、告警规则。
- 增加样例数据和地图层接口，避免长期停留在 landing page。
- 如果接入公网数据源，补充缓存、限流和失败重试策略。

### `camera_snapshot`

- 将边缘上传 token 强制化，生产环境不应允许空 token。
- 图片保存策略需要区分最新帧、历史帧、验证帧和临时帧，避免数据目录膨胀。
- GPIO 查询建议抽象为可配置清单，不固定在单一 pin。

### `action_move`

- 动作执行应有更明确的安全上限：最大速度、最大持续时间、最小间隔、急停抢占。
- `remote_shutdown` 这类高风险动作需要二次确认、独立权限和审计。
- 云端队列建议持久化或至少落地最近任务，便于追查执行失败。

### `audio_recognition`

- 语音识别、技能路由、动作执行之间应使用统一 envelope，保证可追踪。
- 建议将真实硬件路径和 mock/regression 路径隔离，避免测试误触发设备。
- 加强“识别文本 -> intent -> tool call -> action result”的回放测试。

### `common_api_manager`

- 应纳入根 `docker-compose.yml`，并让 Caddy 代理服务名而不是宿主网桥 IP。
- 对 ASR/TTS/LLM 返回结构做稳定 schema，业务模块只依赖平台标准响应。
- 增加 provider 健康检查、超时、重试、熔断和用量统计。

### `llm_manager`

- 目录名 `llm_manager` 与服务名 `web-manager` 不一致，短期可保留，但文档和 UI 要持续说明。
- API Key 建议默认只支持环境变量或加密存储，避免明文持久化。
- 模块状态页可加入 registry/compose/Caddy 对齐检查结果。

### `pi5_robot`

- 继续保持“仿真先行”，真实硬件接入前必须完成安全测试。
- 增加真实机器人配置模板：电机、编码器、IMU、相机、急停输入。
- harnessd 的任务日志建议形成统一 episode schema，后续可接入学习/回放。

### `smile_face`

- 表情状态接口建议支持来源优先级，避免多个模块同时写状态导致闪烁。
- LCD 客户端需要 systemd 示例和断线重连策略。
- 可补充音频模块调用表情屏的集成测试。

### `pi_slam`

- 在正式实现前先定义传感器清单、ROS 2 发行版、地图格式和 Nav2 接入边界。
- 若短期不实现，应在平台中标记为 `extension-point`，避免被当作故障模块。

### `common_sense`

- 建议将公共网络拓扑、远程访问、安全约定迁入 `docs/architecture` 或作为平台级文档索引。
- 静态 HTML 若用于发布，需要明确路由和维护归属。

## 建议落地顺序

1. 编码修复和 JSON/schema 校验。
2. registry、compose、Caddy 三方对齐。
3. 运行产物清理与 `.gitignore` 强化。
4. 硬件控制鉴权、审计、急停策略统一。
5. 模块级回归测试和端到端健康检查。
6. 文档按模块归档，保留平台总览索引。
