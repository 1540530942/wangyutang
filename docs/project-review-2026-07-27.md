# 工程现状复核与下一代演进建议（2026-07-27）

> 评审范围：`feature/llm-manager` 分支当前工作树、核心模块代码、部署流水线、
> 模块测试，以及 `www.wangyutang.cn` 生产环境只读健康检查。
>
> 本文是 2026-07-16 全面评审之后的增量复核，重点检查旧问题是否收敛，
> 以及全双工音频、边缘自动部署和 Qwen3.6 升级带来的新风险。

## 一、执行摘要

当前工程已经是一个真实在线、具备完整交互闭环的具身机器人研发平台，
不是简单 Demo。网关、音频、动作、沙箱和模型服务均能从公网返回健康状态，
Pi 设备在线，全双工音频监听进程也已实际运行。

从成熟度看，它更接近“功能强、可研究的 MVP+”，尚不能定义为安全、稳定、
可持续演进的生产平台：

| 维度 | 判断 | 说明 |
|---|---|---|
| 产品能力 | 较完整 | 语音、规划、安全检查、动作、视觉、TTS、表情和回放链路已贯通 |
| 架构基础 | 合理 | 模块边界、边缘主动连接、DecisionEnvelope 和回放体系方向正确 |
| 工程一致性 | 中等 | 代码、配置、文档、测试和线上模型版本已有明显漂移 |
| 发布治理 | 偏弱 | feature 分支可直接部署生产，集成测试发生在部署后，缺少自动回滚 |
| 安全性 | 高风险 | 控制接口 fail-open，公开健康接口泄露设备诊断信息 |
| 可验证性 | 有基础但未闭环 | 模拟测试较好，全双工真实硬件金标与量化门禁尚未落地 |

综合判断：核心能力约为 7/10，安全与发布治理约为 3/10。下一代的重点不应
继续增加模型和页面，而应从“能运行”升级为“可控制、可证明、可演进”。

## 二、当前合理的地方

### 2.1 目标和完整链路清晰

工程已经形成“云端大脑 + 边缘身体”的完整链路：

```text
语音输入 -> VAD / ASR / 唤醒 -> LLM 或确定性规划
         -> 工具校验 -> 安全检查 -> 真机动作
         -> TTS / 表情反馈 -> DecisionEnvelope / Session 留痕
```

这是一个完整的具身交互系统，而不是若干孤立的机器人接口。

### 2.2 audio_interact 与 robot_sandbox 的边界正确

`audio_interact` 负责音频 I/O、VAD、ASR、TTS 和全双工协议；
`robot_sandbox` 负责指令规划、工具调用、安全和执行。这个逻辑边界应继续保留，
不建议因为部署单元较多而重新揉成一个上帝服务。

### 2.3 DecisionEnvelope 和回放体系有长期价值

工程已经能记录识别结果、规划过程、工具调用、安全结果、动作结果和耗时，
并通过 session、golden、replay 和 dashboard 查询。这让“机器人为什么这样做”
成为可审计、可复现的问题，是当前最值得固化的工程资产。

### 2.4 边缘设备只主动连接云端

Pi 通过轮询、上传和 WebSocket 主动连接云端，不要求家庭网络开放入站端口。
这个连通模型适合当前真实部署环境，也便于未来扩展多设备身份。

### 2.5 全双工技术方向合理

WonderEchoPro 已从直接 ALSA / SpeexDSP 尝试切换到 PipeWire WebRTC
`module-echo-cancel`。当前实现保持麦克风持续上行，并同时支持本地能量检测和
服务端 `speech_start` 两级打断，方向符合真实全双工交互需要。

### 2.6 模块纪律和部署资产较完整

主要模块具有 README、Dockerfile、健康检查、数据卷和独立测试。动作、相机、
公共模型和音频边缘均已有专项部署 workflow。这些资产应继续使用，但需要收紧
触发条件和验收顺序。

## 三、主要问题与风险

### P0-1 控制面鉴权 fail-open，且生产健康接口泄露设备信息

`action_move/server.py` 的 token 逻辑在 token 文件不存在或内容为空时直接放行。
更严重的是，以下指令下发接口没有调用 `require_token`：

- `POST /api/settings`
- `POST /api/tasks/clear`
- `POST /api/tasks`

其中 `/api/tasks` 可以让真机移动、转向或关机。Caddy 网关也没有为控制面提供
操作员身份认证。

线上只读检查进一步确认：公开的 `/action/api/health` 返回了设备内网信息、
Wi-Fi 标识、服务状态和进程命令行；当前边缘动作 token 为空。本文不记录具体
敏感值，但这说明风险已经存在于生产环境，而非纯静态推断。

处理原则：

- 控制接口必须 secure by default，缺少凭证配置时拒绝启动或拒绝写操作；
- 设备身份与操作员身份分离，不再共用一个 token；
- `/api/health` 只返回存活与版本，完整诊断迁入受保护端点；
- 急停可以设计专用权限，但不能通过“全部裸奔”实现可用性。

### P0-2 feature 分支可以直接修改生产，验证顺序反了

`.github/workflows/deploy-modules.yml` 和 `deploy-audio-edge.yml` 同时监听
`main`、`master`、`feature/**` 和 `codex/**`。这意味着普通开发分支 push
即可进入 production environment。

主部署流程目前是：

```text
构建 -> 上传 -> 更新生产容器 -> 健康检查 -> 公网检查 -> 集成测试
```

集成测试发生在生产容器更新之后，测试失败没有自动切回上一个 release。
音频边缘部署使用 `continue-on-error: true`，Pi 更新失败只产生 warning，
因此云端和边缘可能长时间运行不同协议或版本。

目标流程应改为：

```text
单元测试 / 契约测试 / Compose 校验
  -> 构建不可变制品
  -> staging 或离线验收
  -> production 发布
  -> 版本与真实链路断言
  -> 失败自动回滚
```

### P1-3 代码、配置、测试和线上模型版本漂移

本次模块测试实跑结果为：

```text
315 passed, 2 failed, 1 skipped
```

两个失败项分别是：

1. `robot_sandbox` 的“向左转然后掉头”不再走确定性复合动作路径，测试期待两个
   `turn_left`，当前实现转而调用 LLM。需要明确这是产品契约仍应保留，还是测试
   已经过期，不能长期保持模糊状态。
2. `common_api_manager` 已返回 Qwen3.6 模型选择，测试仍硬编码 Qwen3.5 和旧视觉
   模型。工作区文档已经开始改成 Qwen3.6，但测试尚未同步。

生产只读检查也显示：common API 默认模型已经是 Qwen3.6，而 robot_sandbox
仍缓存旧的 Qwen3.5 / 旧视觉模型选择。当前缺少统一的配置修订号和生效确认。

此外，`scripts/run_all_module_tests.sh` 把 `smoke|.` 作为一个模块运行，但
`smoke/` 目录没有测试，真正的 smoke tests 位于 `ci_tests/smoke/`。该入口实际
返回 `no tests ran`，归档的旧测试结果容易造成“全部通过”的错觉。

### P1-4 全双工规格尚未变成可执行质量门禁

新增的全双工 golden spec 定义了正确的场景和指标，包括：

- `echo_only`
- `bargein_early`
- `bargein_middle`
- `bargein_late`
- `speech_after_tts`
- false barge-in、cancel delay、TTS tail 和 residual echo 指标

但当前仍有四类不一致：

1. 实现已经使用 PipeWire WebRTC AEC，规格示例仍写 `speexdsp`；
2. 规格使用 `labels/turns.json`、`tts.json`、`bargein.json`，校验器仍要求
   `vad_label.json`、`asr_label.json`、`tts_label.json`、`bargein_label.json`；
3. 当前两个 golden session 只有单路 `mic_proc` 和简单 turns label；
4. CI 的全双工测试主要使用 Fake WebSocket、Fake VAD 和模拟 TTS，可以验证协议
   顺序，无法证明真实 WonderEchoPro 的 AEC、误打断率和延迟。

真实硬件三轨数据 `mic_raw`、`mic_proc`、`tts_ref` 和运行时事件必须成为发布
门禁，否则全双工“可用”仍主要依赖人工体验。

### P1-5 服务边界和配置边界没有完全落地

当前 Compose 中每个服务都加载同一个 `.env`，不需要模型密钥的模块也能获得
全部环境变量。`audio_interact` 的内部 ASR 调用仍走公网域名；
`robot_sandbox` 默认模型、视觉和 ASR 地址也依赖公网网关。

同时，`robot_sandbox/web/server.py` 仍通过 `../action_move/skill_catalog.json`
引用另一个模块的文件。在本地 checkout 中可用，但容器内需要依赖额外复制布局。
这说明模块边界在目录层存在，在契约层尚未完全建立。

### P2-6 维护成本开始明显上升

- `audio_interact/server.py` 已超过 1600 行；
- `robot_sandbox/web/server.py` 已超过 1100 行；
- 大部分 requirements 没有锁定版本；
- HTTP client、token、JSON 存储和重试逻辑分散在多个模块；
- 关键状态仍以 JSON 文件和进程内列表保存；
- `test_results/` 和 `common_api_manager/online_backup/` 仍被 Git 跟踪。

这些问题暂时不妨碍单机运行，但会放大模型升级、协议升级和多设备扩展的成本。

## 四、下一代目标架构

建议保留逻辑模块边界，同时减少设备通信和发布层面的碎片化：

```text
统一操作台
    |
Gateway / Auth
    +-- Interaction Runtime
    |     audio / wake / planner / safety / session / envelope
    |
    +-- Model Gateway
    |     ASR / TTS / LLM / Vision / provider fallback
    |
    +-- Device Gateway
          command queue / telemetry / camera metadata / device identity
                         |
                   Robot Edge Agent
             audio / motion / camera / face adapters
             local safety / watchdog / emergency stop

所有关键事件 -> Event Store -> Replay / Golden / Dashboard
```

关键原则：

- `audio_interact` 与 `robot_sandbox` 保持清晰代码边界，但共享版本化协议；
- 动作、相机和设备状态统一使用设备身份与 command/event envelope；
- 真正的碰撞保护、急停、TTL 和重复命令抑制必须在边缘侧最终裁决；
- 云端 LLM 只产生受约束计划，不能绕过确定性校验和本地安全；
- 当前单机规模优先使用 SQLite WAL + 文件化音频，不引入 Kafka 或 Kubernetes；
- 多设备或多实例出现后，再评估 PostgreSQL 和独立对象存储。

## 五、分阶段演进计划

### 阶段 0：安全与发布止血（1–2 周）

1. 控制写接口强制鉴权，缺少凭证时 fail closed。
2. 拆分 device token、operator token 和只读观察权限。
3. 健康接口脱敏，诊断端点进入受保护区域。
4. 生产部署只允许受保护的主分支或 release tag。
5. 单元、smoke、契约和 Compose 校验全部前置到部署之前。
6. 增加制品 SHA、配置 revision、边缘 revision 的发布断言与自动回滚。
7. 修复当前两个失败测试和错误的 smoke 测试入口。

验收条件：

```text
无凭证调用任意控制写接口 -> 401/403
公开 health -> 不含网络标识、进程参数和敏感诊断
feature 分支 push -> 不触发 production
全部本地测试和 CI smoke -> 实际收集到测试且全绿
发布失败 -> 线上自动恢复到上一制品版本
```

### 阶段 1：契约和配置收敛（约 1 个月）

1. 定义并版本化 `ActionCommand`、`AudioEvent`、`DeviceState`、
   `ModelSelection` 和 `DecisionEnvelope`。
2. 每个 session 记录代码 SHA、模型版本、配置 revision、边缘版本和协议版本。
3. 内部调用改为服务地址或明确的宿主机内网地址，删除默认公网回环。
4. 每个服务使用最小化 env 和 secret 集合。
5. skill catalog 通过构建制品或版本化 API 分发，不再跨目录读取。
6. 将任务、设备状态和 session 索引迁入原子、可查询的状态存储。
7. 拆分两个千行 server 中的协议、应用服务、存储和外部客户端。

### 阶段 2：真实硬件质量闭环（1–2 个季度）

1. 采集 WonderEchoPro 的 `mic_raw`、`mic_proc`、`tts_ref` 三轨金标。
2. 覆盖不同音量、说话距离、背景噪声和早中晚打断场景。
3. 自动计算 false barge-in、speech recall、duck/cancel delay、TTS tail、
   residual echo、ASR 成功率和动作完成率。
4. golden spec、schema、validator、runtime writer 和 CI 使用同一套字段定义。
5. 将家庭操作台与工程调试台分离，普通用户只看到对话、驾驶、观察和急停；
   trace、envelope、原始 JSON 和模型调试进入管理员界面。

## 六、近期优先级结论

下一轮工作不建议继续新增模型、模块或控制页面。最值得优先完成的三个闭环是：

1. 安全控制面：默认拒绝、身份分离、诊断脱敏；
2. 可信发布：测试前置、主干保护、版本断言、失败回滚；
3. 全双工真机金标：让“可用”变成可重复测量的质量指标。

完成这三项后，工程才真正从可运行的研发平台进入下一代机器人平台阶段。

## 七、本次验证记录

评审期间执行了以下只读或本地验证：

- 检查 Git 状态、目录结构、近期提交和部署 workflow；
- 逐模块运行现有测试，不覆盖仓库中的归档结果；
- 运行 `ci_tests/audio_interact`：10 passed，1 skipped；
- 运行 `ci_tests/robot_sandbox` 与 `ci_tests/action_move`：2 passed，4 skipped；
- 只读检查五个生产健康入口，均返回 HTTP 200；
- 检查全双工实现、golden 数据、schema、validator 和新规格的一致性；
- 当前环境未安装 Docker，因此未实际执行 `docker compose config` 或启动容器。

本次评审未修改业务代码，未调用任何线上控制写接口，也未触发生产动作。
