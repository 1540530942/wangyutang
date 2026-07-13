# Dashboard 重构执行方案总纲

> 目标站点：https://www.wangyutang.cn/dashboard/
> 承载服务：`audio_interact/server.py`（静态页在 `audio_interact/web/static/`）
> 制定日期：2026-07-14

## 一、现状与结论

当前 `/dashboard/` 下有两个页面：

| 现路由 | 现名称 | 实际内容 |
|---|---|---|
| `/dashboard/detail` | 数据看板 | Session 列表 + 单条全链路 trace（名实不符，实为日志） |
| `/dashboard/vad_asr` | VAD+ASR 金标测试集 | 金标用例回放 + live 执行触发 |

定案：

1. **两个页面**：数据日志（sessions）+ 金标测试集（golden），命名与内容对齐。
2. **瀑布图嵌入 session 详情**，不独立成页——用户的真实问题是"这一次对话慢在哪"。
3. **聚合数据看板（overview）下一阶段做**，本期不实现、导航不留入口。
4. **数据基础部分具备，需补埋点**（已核实 `session_writer.py` 与 `get_session_route` 序列化路径）：
   - `events.jsonl` 已有全链路事件类型，但 `asr.final` / `robot.command` / `tts.*` 的 `ts_ms`
     是**音频时间轴位置**（`end_ms` / `duration_ms`），事件在处理完成后批量补写，不反映处理时刻；
   - 唯一真实耗时 `asr_elapsed_ms` 只存在于 WS 响应中，未持久化到 events.jsonl；
   - `get_session_route` 序列化时也未透传 `asr.final` / `robot.command` 的 `ts_ms`，未解析 `tts.*` 事件。
   - 结论：现有数据可画"音频时间轴"（每句话在录音中的位置），画不了"阶段耗时"（ASR/路由/TTS 各花多久）。
     瀑布图目标是后者，故第 2 步为**埋点 + API 透传 + 前端渲染**三层改动，非纯前端。

## 二、目标结构

```
/dashboard/
├── sessions   数据日志 —— 现 /dashboard/detail 改名
│              左侧 session 列表 + 右侧全链路详情
│              详情内嵌「瀑布图」：每个 utterance 一条时间轴，
│              分段展示 VAD → ASR → 路由 → TTS 各阶段耗时
└── golden     金标测试集 —— 现 /dashboard/vad_asr 改名
               金标用例回放 + live 执行触发，功能不变
```

- `/dashboard/` 根路径 308 重定向 → `/dashboard/sessions`
- 旧路由 `/dashboard/detail`、`/dashboard/vad_asr` 保留 308 重定向，书签不失效

### 各页面回答的问题

| 页面 | 回答的问题 |
|---|---|
| sessions（数据日志） | 这一条对话发生了什么？慢在哪一段？ |
| golden（金标测试集） | 回归过了吗？改动有没有破坏基线？ |
| overview（数据看板） | 系统整体健康吗？——**下一阶段** |

## 三、命名变更清单

### 路由

| 旧 | 新 | 处理 |
|---|---|---|
| `/dashboard/detail` | `/dashboard/sessions` | 旧路由 308 重定向 |
| `/dashboard/vad_asr` | `/dashboard/golden` | 旧路由 308 重定向 |
| `/dashboard/` → detail | `/dashboard/` → sessions | 改重定向目标 |

`vad_asr → golden` 的理由：
- 页面本质是金标测试集，不只是 VAD+ASR（含路由→动作 live 执行）；
- 后端 API 本就叫 `/api/golden/*`，前后端语义统一；
- 金标集未来可能扩展到文本/视觉用例，`golden` 装得下。

### 静态文件

| 旧 | 新 | 说明 |
|---|---|---|
| `web/static/dashboard.html` | `web/static/sessions.html` | |
| `web/static/dashboard.js` | `web/static/sessions.js` | 仅 sessions 页引用；后续瀑布图逻辑直接写入本文件，**不另拆文件** |
| `web/static/vad_asr.html` | `web/static/golden.html` | |
| `web/static/vad_asr.js` | `web/static/golden.js` | |
| `web/static/dashboard.css` | **不改名** | 两个页面共享的样式表，改名会破坏 golden 页引用 |

### 文案

- 「机器人交互数据看板」→「数据日志」（sessions 页标题）
- 「VAD+ASR 金标测试集 · 单工模式」→「金标测试集」（单工模式已有用例 badge，不占标题）
- 两页 topbar 导航同步：`数据日志 | 金标测试集`

### 不改的内容

- `ci_tests/vad_asr/` 目录名——测试目录改名影响面大，后续单独评估。
- 金标 case_id `vad_asr_simplex_001` 及 `/api/golden/*` 接口路径——CI 断言依赖，保持不变。
- `web/static/dashboard.css`——两页共享样式表，保持原名。

## 四、施工顺序

> 顺序原则：先做行为保持不变的命名重构（易审查、易回滚），再做纯功能增量（瀑布图），
> 两步各自独立成 PR，互不掺杂。瀑布图代码直接落在改名后的最终文件里，避免二次搬运。

### 第 1 步：命名重构（行为保持不变）

本步不变量（审查依据）：

- 用户可见功能与改名前完全一致，旧入口经 308 重定向仍可达；
- 不碰任何业务逻辑（VAD / ASR / 路由分发 / 金标执行的代码路径零改动）。

注：文件名与 `server.py` 路由耦合（`FileResponse` 路径），无法拆成"纯文件改名"独立 PR，
路由、文件名、引用、文案四类改动必须同一 PR 落地。

改动清单：

- `server.py`：
  - 新增 `/dashboard/sessions` → 返回 `sessions.html`；`/dashboard/golden` → 返回 `golden.html`；
  - `/dashboard/`、`/dashboard/detail` → 308 重定向到 `/dashboard/sessions`；
  - `/dashboard/vad_asr` → 308 重定向到 `/dashboard/golden`。
- 静态文件按「三、命名变更清单」改名；页内引用同步：
  - `sessions.html` 引用 `sessions.js`（bump 版本参数）、`dashboard.css`（不变）；
  - `golden.html` 引用 `golden.js`（bump 版本参数）、`dashboard.css`（不变）；
  - 两页 topbar 链接指向新路由，文案改为 `数据日志 | 金标测试集`。
- 页面标题文案按「三、命名变更清单」调整。
- 把两个页面各自内联的 topbar 与导航样式统一抽取到共享的 `dashboard.css` 中，
  使两页渲染出完全一致的顶栏骨架，解决页面切换时顶栏样式不一致导致的视觉跳变问题。
- 全站检索旧路由字符串（`robot_gateway/site/`、`ci_tests/`、`docs/`），有直链则改为新路由；
  已核实 CI 测试未断言页面路由（仅引用 case_id 与 API 路径），预期无需改 CI。

验收标准（本步独立验收，通过后才进入第 2 步）：

- `/dashboard/sessions`、`/dashboard/golden` 返回 200 且页面功能与改名前完全一致；
- 用不跟随重定向的请求（`curl -sI`，不加 `-L`）确认三个旧入口（`/dashboard/`、`/dashboard/detail`、
  `/dashboard/vad_asr`）返回 308 且 `Location` 指向对应新路由——浏览器与多数 HTTP 客户端会自动跟随
  重定向并显示最终 200，仅看"页面可达"验证不了状态码；
- 两页 topbar 渲染一致，导航切换无顶栏样式跳变；
- `ci_tests/audio_interact/`、`ci_tests/vad_asr/` 全绿；
- diff 审查确认：仅路由、文件名、引用路径、文案变更，无逻辑改动。

### 第 2 步：瀑布图嵌入 session 详情（埋点 + API 透传 + 前端渲染，三层改动）

改动清单（按数据流顺序）：

1. **埋点**（`server.py` `_process` → `session_writer.py`）：
   处理链路中已计算的真实耗时（如 `asr_elapsed_ms`）目前只进 WS 响应、不落盘。
   将各阶段耗时字段随 utterance 数据传入 `write_session_package` / `write_segment_session_package`，
   写入对应事件：`asr.final` 加 `asr_elapsed_ms`，`robot.command` 加 `route_elapsed_ms`，
   `tts.audio_ready` 加 `tts_elapsed_ms`（字段名以实现时统一为准，禁止用音频位置冒充耗时）。
2. **API 透传**（`server.py` `get_session_route`）：
   解析 `tts.request` / `tts.audio_ready` 事件（现完全未解析）；
   将各阶段耗时字段收入 utterance dict 返回。
3. **前端渲染**（`sessions.js`，第 1 步改名后的最终文件，瀑布图逻辑直接写入，不拆新文件）：
   在 utterance 详情中渲染耗时分段条：VAD 段长 → ASR 耗时 → 路由耗时 → TTS 耗时；
   每段标注耗时（ms），hover 显示原始字段值。

降级规则：

- 未唤醒 / `asr_only` 等缺失阶段：对应分段不渲染，不画空条、不报错；
- **历史 session 无新埋点字段**：耗时分段整体不渲染，仅显示音频时间轴位置（现有字段可支撑），
  提示"该 session 无耗时数据"。

验收标准：

- 新产生的完整链路 session：各阶段耗时分段渲染正确，数值与 WS 响应中的耗时一致；
- `asr_only` session 与埋点前的历史 session：降级正确；
- 金标页（golden）不受影响；
- CI 全绿。

### 第 3 步：数据看板 overview（下一阶段，本期不做）

- 聚合指标：日均 session 数、唤醒成功率、ASR 平均耗时趋势、skill 分布、错误率。
- 触发条件：实际产生看周趋势的需求时再启动。

## 五、部署后线上复验

每步部署后在 www.wangyutang.cn 复验该步验收标准全部条目。

## 六、Loop 执行方式

通过 `/loop` 驱动施工，loop 提示词如下（验收依据全部指向本文档与
`golden-pi-alsa-wake-distance-optimization.md`，不依赖对话记忆）：

```
按 docs/dashboard-restructure-plan.md 与 docs/golden-pi-alsa-wake-distance-optimization.md 推进。
每轮执行：
1) 读两份文档与 git log/status，判断当前进度
   （阶段1 命名重构 → 阶段2 瀑布图 → 阶段3 金标 pi_alsa_wake_distance_001 优化；overview 不做）；
2) 本轮只推进一个最小可验收的改动单元，禁止跨阶段混改；
3) 改完立即按对应文档的验收标准逐条自验并输出对照结果：
   阶段1——curl -sI（不加 -L）断言三个旧入口 308 且 Location 正确；新路由 200 且功能一致；
   两页 topbar 渲染一致；ci_tests/audio_interact 与 ci_tests/vad_asr 全绿；
   diff 仅含路由/文件名/引用/文案变更，无业务逻辑改动；
   阶段2——新 session 各阶段耗时字段落盘且与 WS 响应数值一致；get_session_route 解析 tts 事件
   并透传耗时；瀑布图对完整链路/asr_only/无埋点历史 session 三类渲染与降级正确；
   golden 页无回归；ci_tests 全绿；
   阶段3——按 golden-pi-alsa-wake-distance-optimization.md 验收汇总表逐项执行：
   先做功能项（3次回放稳定性、距离反例断言、empty负向断言），再做时延项（基线先行→
   定位瓶颈→每轮一项优化→复测对照）；时延优化满足停止条件（达标或连续两轮改善<10%）即停；
4) 任一验收项不过，本轮只修复不推进新项；同一验收项连续 3 轮仍不过，停止循环并输出
   阻塞报告（现象、已尝试的修复、建议），不做无限重试；
5) 每阶段本地验收全部通过后 git commit 并 push（push 到 feature 分支会自动触发
   GitHub Actions deploy-modules.yml 部署 audio_interact 到腾讯云）；push 后直接轮询
   线上验收断言本身（如 curl -sI 打 www.wangyutang.cn 的新旧路由），断言通过即视为
   部署生效，30 分钟仍不通过视为部署失败，按第 4 条处理；生效后继续执行该阶段其余
   线上复验项（时延基线/复测等）；
6) 三个阶段全部验收通过（含线上复验）后，输出最终汇总（改动清单 + 验收标准逐条对照 +
   各次部署的 Actions 运行链接），结束循环。
```
