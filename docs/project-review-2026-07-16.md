# 工程全面评审与演进方案（2026-07-16）

> 范围：`wangyutang` 仓库全部模块（8 个 compose 服务 + 1 个宿主机服务 + 辅助模块），
> 基于当前 `feature/llm-manager` 分支代码的静态审视。逐条结论均可在文中给出的
> 文件路径处复核。
>
> 修订（2026-07-16 二审）：逐路由复验后修正三处——P0-1 改为"可用性依赖"
> 论据（hairpin 回环性能损耗很小）；P0-3 升级为"鉴权方向装反"（设备端点
> 有票、指令下发端点裸奔）；阶段一#1 补 extra_hosts 前提、#3 改为应用层
> 精确鉴权（Caddy 整段拦截会弄断真机）。新增"用 loop 巡检闭环推进落地"一节。
>
> 后续模型更新（2026-07-19）：生产 `common_api_manager` 的 chat 与 lv vision
> 实际加载标识已统一为 `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`。本文保留 7 月 16 日
> 评审时的 MTP 部署记录；当前模型配置以 `common_api_manager` 的 settings、env
> 示例和 health 接口为准。

---

## 〇、评审的前因后果

**缘起。** 2026 年上半年，本工程完成了一轮密集演进：audio_interact 与
robot_sandbox 拆分为语音/规划两个服务并各自建站、路由从 `/audio`/`/interact`
迁移到现名、function_center 退役并入 `/common/robot-skills`、自建 lv_server
GPU 推理栈逐步替代云厂商 API。到 2026-07 中旬，`feature/llm-manager` 分支
上刚跑通 Qwen3.6 统一端点，工程从"堆功能"进入"收口平台"的转折点。此时
需要一次全局审视：哪些设计该固化，哪些债务在利滚利，下一步怎么走——这就
是本文的由来（2026-07-16，应仓库所有者要求）。

**一审（本文主体）。** 对全部 8 个 compose 服务、1 个宿主机服务和辅助模块
做静态代码审视，产出：目标使命（一章）、值得保持的设计（三章）、按严重度
排序的问题清单（四章）、三阶段演进方案（五章）。

**同日并行事件，催生了方法论一节。** 评审当天，lv_server 上
Qwen3.6-35B-A3B-MTP-GGUF 的部署正通过"30 分钟一轮 loop 巡检"做端到端
收尾：四项真实调用检查（8012/8013 健康、公网工具调用、公网图像识别、
chat/vision 同端点）连续多轮全绿，期间发现并修复了 Tencent 侧 vision 路径
缺 `enable_thinking: False` 导致慢 10 倍的问题——**配置看着全对、真实调用
才暴露问题**，这一实战直接沉淀为五章的"用 loop 巡检闭环推进落地"。

**二审（修订记录见文首）。** 一审发布后按"是否合理"逐条质询复验：逐路由
核对了 action_move 的鉴权覆盖、通读了 Caddyfile 全文、验证了容器内域名
解析条件。结果三处修正、一处加重（P0-3 从"缺鉴权"升级为"鉴权方向装反"）。
未被推翻的结论均在文中保留原有文件:行号证据。

**本文的定位。** 演进工作的基线文档：后续每个演进项以五章验收表为判定
标准，用 loop 巡检推进；完成一项，在本文对应条目标注完成日期与验证记录
链接（`docs/verification/`）。

---

## 一、工程的目标使命

**打造一个"云端大脑 + 边缘身体"的个人具身智能机器人平台。**

具体拆开是三层使命：

1. **交互闭环**：让实体机器人（TurboPi / Raspberry Pi 5）能通过语音完成
   "听（VAD/ASR/唤醒）→ 想（LLM 规划 tool_calls）→ 查（校验 + 安全前置，如
   front_distance）→ 做（底盘/舵机执行）→ 说（TTS 播报）→ 看（相机/表情屏反馈）"
   的完整闭环，并且每一步都留下可审计、可回放的 DecisionEnvelope。
2. **模型自主权**：不绑死单一云厂商。`common_api_manager` 把 DashScope、Spark
   服务器、自建 lv_server GPU（Qwen3.6-35B-A3B-MTP-GGUF，文本和视觉共用）统一收口成平台级
   ASR/TTS/Chat/Vision API，Model Studio 提供真实推理校验的模型目录，可随时切换。
3. **工程可信度**：用金标会话（golden sessions）、回放（replay）、无 LLM 的
   smoke 必过集、分层 CI 守护（`ci_tests/`）来保证"改了代码，机器人行为不退化"。
   这是把具身智能当正经软件工程做，而不是 demo 堆砌。

一句话：**这是一个把自建 LLM 栈接到真实机器人身上、并坚持可回放可验证的
个人具身智能实验平台。**

---

## 二、全局架构现状

```
                         公网 www.wangyutang.cn
                                │
                    ┌───────────▼───────────┐
                    │  robot_gateway (Caddy) │ 80/443，路径路由
                    └───┬───────┬───────┬───┘
        compose 内网     │       │       │        宿主机
  ┌────────────┬────────┼───────┼───────┼──────────────┐
  │ camera_    │ action_│ robot_│ audio_│ common_api_  │
  │ snapshot   │ move   │sandbox│interact│ manager      │← host:8101（不在 compose）
  │ :8099      │ :8094  │ :8095 │ :8097 │  DashScope/Spark/lv_server
  ├────────────┼────────┴───────┴───────┤              │
  │ pi5_robot  │ slam_mapping  smile_face│              │
  │ :8093      │ :8301         :8096     │              │
  └────────────┴─────────────────────────┴──────────────┘
        ▲ 轮询/上传（HTTP，Pi 侧无入站端口）
  ┌─────┴──────────────────────────────────────────┐
  │ 边缘设备：TurboPi / Pi5 / WonderEchoPro / LCD 屏 │
  │ pi_camera_sender / edge_action_poller /         │
  │ edge_ros_controller / wonderecho_listener /     │
  │ pi_imu_sender / qt_lcd_client（5+ 个独立边缘进程）│
  └──────────────────────────────────────────────────┘
```

代码规模（Python）：robot_sandbox 6.9k 行（71 文件）、common_api_manager 3.9k、
audio_interact 3.8k、action_move 3.2k、camera_snapshot 2.3k、pi5_monitor 2.2k，
其余各 <1.5k。全部服务统一 FastAPI + uvicorn + python:3.12-slim。

---

## 三、合理的地方（值得保持）

### 3.1 模块纪律是真实执行的，不是纸面文章
`MODULE_MERGE_GUIDE.md` 要求每个模块有独立目录、README、`/api/health`、
Dockerfile、数据卷——抽查 8 个 compose 服务全部满足，`docker-compose.yml`
里每个服务都配了 healthcheck。`docs/module-compliance-audit.md` 还在持续
跟踪合规状态。个人项目能维持这个纪律很少见。

### 3.2 audio_interact / robot_sandbox 的职责切分正确
音频 I/O（VAD/ASR/唤醒/TTS 通道）与指令规划执行（tool_calls/校验/安全/执行）
分成两个服务，`/robot_sandbox/` 页面纯文本、`/audio_interact/` 页面纯语音，
两者共用同一条决策链路。这是正确的关注点分离，且有设计文档
（`docs/audio-interact-robot-sandbox-design.md`）背书。

### 3.3 robot_sandbox 内部分层清晰，legacy 有隔离
`core/`（contracts + DecisionEnvelope）、`agent/`（ReAct）、`tools/`
（schema/validator/adapter/dispatcher）、`safety/guard.py`（独立安全阶段）、
`skills/registry.yaml`（技能配置化，TTS 文案进 registry 而非硬编码——这条
还是历史 review 教训沉淀成的规矩）、`storage/`（case_store/envelope_store/replay）、
旧代码收进 `legacy/`。**DecisionEnvelope 作为可落盘、可回放的决策工件**是
整个工程最有价值的设计决定，它让"机器人为什么这么动"变成可审计问题。

### 3.4 common_api_manager 的 provider-per-module 模式
`modules/{dashscope,spark,lv}_qwen_{chat,vision,asr,tts}` 每个 provider×能力
一个目录（router + service），加 `model_studio_catalog` 做统一目录和真实推理
校验（点"校验"跑真实推理，不是硬编码徽章）。新增一个后端只需加一个目录，
这个扩展模式和当前 `feature/llm-manager` 的方向是配套的。

### 3.5 边缘侧"只出不进"的连通模型
Pi 设备全部通过轮询/上传对接云端（`edge_action_poller` 拉任务、
`pi_camera_sender` 推帧），Pi 上不开入站端口，靠 www.wangyutang.cn 网关
穿透 NAT。对家庭网络环境这是唯一稳妥的选择。

### 3.6 分层测试体系
43 个 `test_*.py`；`smoke/` 是不依赖 LLM 的必过集；`ci_tests/` 按模块组织
金标用例（含 `vad_asr_simplex_001` 金标）；`robot_sandbox` 有 1173 行的
ReAct pipeline 单测 + 回归套件 + `react_regression_check.py`。对"改提示词/
换模型会不会弄坏机器人"这个具身智能特有风险，这套体系是对症的。

### 3.7 文档留痕文化
`docs/logs/`（按日期的工程日志）、`docs/incidents/`（事故复盘）、
`docs/verification/`（验证记录）、迁移文档（路由改名史）。debug 时能查到
"当时为什么这么改"，这是复利资产。

---

## 四、不合理的地方（按严重程度排序）

### P0-1 内部调用被公网域名绑架
`docker-compose.yml:103` 给 audio-interact 配的是
`COMMON_ASR_URL: https://www.wangyutang.cn/common/api/asr/transcribe`；
`robot_sandbox/web/server.py:45-48` 里 ASR/Vision/Model-Studio 的默认地址
也全是公网域名。这些调用发起方和 common_api_manager 在**同一台服务器**上。
注意：性能损耗其实很小——访问的是本机自己的公网 IP（hairpin 回环），流量
并不真出机房。**真正的要害是可用性依赖**：每次内部调用都多走一次 TLS 握手
和 Caddy 转发，且内网功能被域名解析、证书续期、Caddy 存活三者绑架——域名
或证书一出问题，语音链路整体瘫痪（`docs/www-wangyutang-cn-fix.md` 就是前科）。
正确做法是容器内直连宿主机 8101（见阶段一第 1 条，注意 extra_hosts 前提）。

### P0-2 所有容器共享同一个 .env，密钥全量下发
每个 compose 服务都是 `env_file: .env`。DashScope API key 这类密钥被注入到
smile_face、slam_mapping 等根本用不到它的容器里。任何一个容器被攻破或日志
泄露 env，全部密钥沦陷。同时 compose 里还硬编码了 Tailscale 内网 IP
（`AUDIO_SENSOR_SERVER: http://100.118.92.117:8088`），环境迁移即失效。

### P0-3 公网可达的机器人控制面缺乏鉴权（且鉴权方向装反了）
`/action/`、`/camera/`、`/robot_sandbox/` 等控制页面直接暴露在公网域名下，
Caddyfile 全文无任何 auth 指令。逐条核对 action_move 路由后发现问题比
"缺鉴权"更刺眼——**鉴权方向装反了**：

- **设备侧端点有票**：`/api/tasks/next`、`/api/tasks/result`、
  `/api/device/heartbeat` 都要求 `X-Action-Token`
  （`action_move/server.py:369,401,438`）；
- **指令下发端点裸奔**：`POST /api/tasks`（让真机移动的那个接口，
  `server.py:308`）、`POST /api/tasks/clear`、`POST /api/settings`
  **全部无需任何凭证**。

也就是说：机器人自己来取任务要验票，公网上任何陌生人来"给机器人派任务"
反而畅通无阻。这是一台能物理移动的机器，此项优先级最高。

### P1-4 横切代码八份复制，没有共享库
8 个 FastAPI 服务各自手写了一遍：health 端点、`settings.json` + `threading.Lock`
的状态持久化、`urllib/requests` 出站调用+超时+重试、token 生成校验。TTS/ASR
客户端逻辑在 audio_interact、robot_sandbox/harness、action_move/edge_action_poller、
wonderecho_listener 至少四处各有一份。每修一个坑（比如最近的 `[4B][WAV]` TTS
framing 修复）都要判断"其他几份要不要同步改"。

### P1-5 两个千行级"上帝文件"
`audio_interact/server.py` 1377 行、`robot_sandbox/web/server.py` 1160 行。
audio_interact 的内部重构（拆 `runtime/silero_vad + asr_client + tts_client`）
只完成了一半——`runtime/` 目前只有 session_writer/audio_writer/event_logger，
VAD/ASR/TTS 主逻辑仍在 server.py 里。robot_sandbox 的 web 层把路由、模型选择
缓存、音频存储、外部服务调用全部揉在一个文件里，和它内部其他目录的整洁分层
形成反差。

### P1-6 跨模块直接读文件，破坏容器边界
`robot_sandbox/web/server.py:38`：
`ACTION_CATALOG_PATH = (PACKAGE_DIR / "../action_move/skill_catalog.json")`。
但 robot_sandbox 的 Dockerfile 只 COPY 自己的目录——这个路径**在容器里根本
不存在**，只在开发机 checkout 里成立。技能目录应该通过 action_move 的 HTTP
接口获取，或在构建时显式拷贝并写明来源。

### P1-7 同步阻塞调用跑在 async 框架里
各 server 在 FastAPI handler 里直接用 `requests` / `urllib.request`（如
`robot_sandbox/web/server.py`、`audio_interact/server.py`）。uvicorn 事件循环
被出站 HTTP 阻塞；靠 def-handler 的线程池兜底不是长久之计，一旦某个下游
（如 lv_server 推理 30s+）变慢，并发能力急剧退化。

### P2-8 产物和死代码入库
- `test_results/`（17 个文件）是生成产物，进了 git；
- `common_api_manager/online_backup/test_qwen.py` 765 行备份死代码入库
  （git 本身就是备份，`online_backup` 目录不该存在）；
- 各模块 `.pytest_cache/` 散落（应 gitignore）。

### P2-9 模块地图有重叠和错位命名
- **robot_sandbox 名不副实**：它是生产环境的指令规划执行核心，不是 sandbox，
  名字会误导协作者对其稳定性要求的判断；
- **pose_tracker vs slam_mapping**：都做位姿（IMU yaw + 航位推算 vs
  pose+占据栅格），slam_mapping 里还有个 `2d_action/`，边界模糊；
- **simulation/ vs pi5_robot 内置仿真** （robotd/visiond/harnessd）两套仿真并存；
- **loop_engineering 在两处**：根目录 `loop_engineering/` 和
  `pi5_robot/loop_engineering/`（reward_evaluator/scenario_generator），
  同名不同物。

### P2-10 部署与分支形态偏科
- 4 个 GitHub Actions workflow 共 1247 行，deploy-modules（426 行）和三个
  单模块 workflow 逻辑高度重复；最近 5 个提交里 3 个是修 CI 本身
  （`fix(ci): extract golden.py`、`fix(docker): include golden.py`、
  `fix(ci): drop stale preflight assertion`），说明部署管线脆、构建上下文
  与 CI 期望不同步；
- 仓库唯一分支是 `feature/llm-manager` 且它就是默认分支——"feature 分支
  即主干"的形态迟早在回滚和多线开发时付出代价；
- common_api_manager 是唯一跑在宿主机、不进 compose 的服务，独享一套部署
  路径，也是 P0-1 绕公网问题的根源之一。

### P2-11 状态层全靠 JSON 文件
results.json / events.json / settings.json / cases + `threading.Lock`。
MVP 阶段合理，但已出现多写者（HTTP handler + 后台线程）、无崩溃一致性
（写一半掉电即损坏）、无法做跨会话查询的问题。envelope/case/session 这类
核心审计数据值得一个真正的存储。

---

## 五、演进方案

### 阶段一：止血与还债（1–2 周，不改架构）

| # | 动作 | 对应问题 |
|---|------|---------|
| 1 | 内部调用全部改内网直连：compose 内加 `COMMON_API_URL=http://host.docker.internal:8101`，代码默认值不再写公网域名。**前提**：目前只有 robot-gateway 配了 `extra_hosts: host.docker.internal:host-gateway`，audio-interact / robot-sandbox 等服务必须同步补上这条，否则该域名在容器内无法解析 | P0-1 |
| 2 | 拆 `.env` 为 per-service env 文件（`env/{service}.env`），密钥只发给用到的容器；Tailscale IP 收进 env | P0-2 |
| 3 | 给**指令下发类**路由补鉴权：`POST /api/tasks`、`/api/tasks/clear`、`/api/settings` 等复用 action_move 已有的 `X-Action-Token` 机制（`require_token`），前端页面带票调用。**不要**在 Caddy 层对 `/action/*` 做整段 forward_auth——TurboPi 边缘轮询器正是从公网走 `/action/api/tasks/next` 进来的（自带设备 token），整段拦截会把真机弄断线 | P0-3 |
| 4 | `test_results/`、`.pytest_cache/` 移出 git 并 gitignore；删除 `online_backup/` | P2-8 |
| 5 | 建 `main` 分支作为受保护主干，`feature/llm-manager` 回归 feature 语义 | P2-10 |
| 6 | skill_catalog 改为启动时从 action_move `/action/api/...` 拉取（带本地缓存兜底），删除 `../action_move` 路径引用 | P1-6 |

### 阶段二：收敛与重构（1–2 个月）

1. **建 `platform_common/` 共享库**（pip 可安装，各 Dockerfile COPY 进镜像）：
   - `health.py`（统一 health router）、`http_client.py`（超时/重试/内网寻址）、
     `json_store.py`（原子写 + 锁的过渡态存储）、`auth.py`（token）、
     `clients/`（asr/tts/vision/common_api 的唯一一份客户端）。
   - 迁移顺序按收益：先 TTS/ASR 客户端（四份合一），再 health/settings。
2. **完成 audio_interact 内部重构**（延续既定方案，不拆服务）：把 server.py 中
   VAD/ASR/TTS 主逻辑落进 `runtime/silero_vad.py`、`runtime/asr_client.py`、
   `runtime/tts_client.py`，server.py 只剩路由和装配，目标 <400 行。
3. **robot_sandbox/web 拆 router**：按 `command / envelope / audio / settings /
   dashboard` 拆成 APIRouter 文件，模型选择缓存独立成模块；顺手把
   robot_sandbox 更名为 `robot_agent`（对外路由可保留 `/robot_sandbox/` 兼容）。
4. **common_api_manager 进 compose**：容器化后它和其他服务同生命周期、同
   部署管线，`/common/*` 上游从 `host.docker.internal:8101` 改回容器名。
   lv_server/Spark 是外部 GPU 资源，继续走出站调用不受影响。
5. **模块合并**：pose_tracker 并入 slam_mapping（同为位姿反馈）；
   `pi5_robot/loop_engineering` 更名（如 `episode_eval/`）或并入根
   loop_engineering；明确 simulation/ 与 pi5_robot 内置仿真谁是唯一仿真源，
   淘汰另一个。目标：compose 服务数不增，辅助模块从 8 个收敛到 5 个左右。
6. **CI 收敛**：四个 workflow 合并为一个带 module-matrix 的 workflow，
   模块列表、构建上下文、健康检查 URL 定义成一份 YAML 数据，杜绝
   "改了模块忘了改 CI" 类事故。

### 阶段三：能力升级（一个季度）

1. **LLM Manager 落地**（当前分支方向）：在 common_api_manager 之上做统一
   模型路由层——按能力（chat/vision/asr/tts）选后端、lv_server → Spark →
   DashScope 的降级链、基于 `common/usage.py` 的用量计量与配额。让
   robot_sandbox/audio_interact 只面对"能力接口"，不再感知具体 provider。
2. **异步化与流式化**：出站调用统一迁到 `httpx.AsyncClient`（进
   platform_common），语音链路（ASR→规划→TTS）全程流式，压端到端延迟。
3. **存储升级**：envelope/case/session 从 JSON 文件迁到 SQLite（每服务一库，
   WAL 模式即可满足并发），保留 JSON 导出兼容 replay 工具。
4. **可观测性**：session_id 已贯穿语音链路，补一个跨服务传递的 trace 头 +
   结构化日志，dashboard 从"看结果"升级到"看每一跳耗时"。
5. **边缘 Agent 统一**：把 Pi 上 5+ 个独立进程（camera_sender / action_poller /
   imu_sender / wonderecho_listener / lcd_client）收敛为一个插件式边缘守护
   进程：统一的连接管理、重试、鉴权、配置和 systemd 单元，能力作为插件注册。
   这是边缘侧稳定性（也是 pi5_monitor 要 forensics 的那些掉线问题）的根治方向。

### 用 loop 巡检闭环推进落地

演进项不靠"改完就算完"，每一项都配一个**可机器判定的验收检查**，用
Claude Code 的 `/loop` 定时巡检推进："检查 → 不达标就修 → 复测 → 达标后
撤销循环"。这套打法已在 lv_server Qwen3.6 部署上验证过（30 分钟一轮，
四项检查：8012/8013 健康、公网工具调用真实产出 tool_calls、公网图像真实
识别、chat/vision 同端点），可直接复制到演进项上。

**用法模板**：

```text
/loop 三十分钟检查一次，直到 <目标状态>。<不达标时的修复指引>。
```

**要点**：

1. **验收必须是真实调用，不是配置检查**——测"公网发图能答出颜色"，
   而不是"env 里写了正确的 URL"。配置对但链路断的情况（隧道挂、容器
   OOM）只有真实调用能暴露。
2. **循环提示词里写清修复指引**（如"参考 docs/lv-server-deployment.md
   排障章节"），失败轮次才能自动诊断修复，而不是只报警。
3. **达标即撤**：目标态连续通过后删除定时任务，避免无意义空转；
   长期健康监控另配低频巡检（如 4 小时一轮），与推进型 loop 分开。

**各阶段验收检查示例**（作为 loop 的判定条件）：

| 演进项 | 机器可判定的验收 |
|--------|-----------------|
| 阶段一#1 内网直连 | 在 audio-interact 容器内 `curl http://host.docker.internal:8101/api/health` 通，且 `docker inspect` 确认 env 中无 wangyutang.cn；公网语音链路真实调用仍通过 |
| 阶段一#2 密钥拆分 | `docker exec smile-face env \| grep -c DASHSCOPE` 为 0；各服务健康检查全绿 |
| 阶段一#3 指令鉴权 | 无 token 的 `POST /action/api/tasks` 返回 401；带 token 返回 200；真机轮询器心跳仍正常 |
| 阶段一#6 catalog 走 HTTP | robot-sandbox 容器内技能列表接口返回非空，且代码无 `../action_move` 路径引用 |
| 阶段二共享库 | `grep -r "def synthesize" --include='*.py'` 全仓库仅命中 platform_common 一处；全模块测试通过 |
| 阶段三 LLM 降级链 | 手动停掉 lv 隧道后，公网 chat 真实调用仍返回（provider 显示降级目标）；恢复隧道后 provider 回到 lv |

### 判断优先级的原则

这个工程当前最大的风险不是功能不够，而是：**(a) 公网暴露的控制面（安全）、
(b) 八份复制的横切代码（每次修 bug 的成本在指数化）、(c) 脆弱的部署管线
（近期提交 3/5 在修 CI）**。阶段一的六条全部对准这三点，做完之后再谈重构
和新能力，顺序不能反。

---

## 六、总评

这是一个**方向感和工程纪律都明显高于典型个人项目**的仓库：模块边界、健康
检查、金标回放、事故留痕都是按团队工程标准执行的；DecisionEnvelope 和
provider-per-module 两个设计决定尤其正确。当前的短板集中在"平台化欠账"：
横切代码没有收口、内外网流量混用、密钥下发过宽、部署管线脆。这些都属于
已知模式的债务，按上面三个阶段偿还即可，不需要推倒任何现有架构。
