# 2026-09-08 Action Move `speak` Skill — 让机器人说话作为原子技能

## Request

`audio_interact` 页面的合成方向（TTS）此前只在浏览器本地出声。需求：把同一段合成结果也能下发到树莓派外放，并对外提供通用 API。

对齐决定：这不是 `audio_interact` 的私有端点，而是和 `move_forward` / `rgb_on` / `front_distance` **同一层的原子技能**——`voice_prompts/*.wav` 说明机器人本来就在说话，只是现在只能播固定句。因此做成 `skill_catalog.json` 里的目录技能，走同一条 `POST /action/api/tasks` 队列、同一个 `/common/robot-skills` 页面、可与运动指令排序、被急停抢占。

分工：`action_move` = 编排/目录/队列（机器人「做什么、何时做」），`audio_interact` 保留自己的 TTS 能力实现，`speak` 的执行直接复用边缘 poller 已有的「拉云端 TTS + 本机 aplay」路径（前者方案：边缘执行，不新增传输通道，不依赖 WonderEcho 的 `/ws/audio` 长连接）。

## 调用方式

云端任务指令（通用 API）：

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"speak","params":{"text":"你好，我到了","voice":"vivian"},"source":"manual"}'
```

- `params.text`（必填，≤200 字）：要播报的文本
- `params.voice`（可选）：TTS 音色，默认 `vivian`
- `params.instructions`（可选）：播报风格 prompt，默认「用清新自然、甜美温柔的语气说」
- `settings.voice_volume_percent`（沿用现有持久设置，非本次任务参数）：本机音量，`0` = 静音

别名（NL / alias 匹配）：说、播报、讲话、朗读、say、speak、broadcast。

UI 入口：

- `https://www.wangyutang.cn/common/robot-skills` —— 新增「说话」行，「验证」按钮 `window.prompt` 输入文本后提交同一个 `POST /api/tasks`。
- `https://www.wangyutang.cn/audio_interact/` 的「合成方向（TTS）」区（VAD_ASR_TTS 模式下）—— 新增「🚗 推送到实车播报」按钮，复用该区的文本框 / 音色 / 风格提示词，同源 `fetch("/action/api/tasks", {action:"speak", params, source:"audio_interact"})`，与旁边的浏览器本地播报相互独立。改的是 `audio_interact/web/static/index.html` + `app.js`，`audio_interact` 服务端零改动。
- `https://www.wangyutang.cn/action/` 控制台**没有** speak 按钮（只有 `voice_volume_percent` 设置）。

## Implementation

### `action_move/skill_catalog.json`

新增 `speak` 技能，`type: "speak"`，带 `speak` 元信息块（`text_param` / `optional_params` / `max_chars: 200` / `tts_endpoint` / `executor`）。

### `action_move/server.py`（云端）

- `ActionRequest` 新增 `params: dict[str, Any]` 字段。
- 新增 `build_task_params(skill, params)`：仅对 `speak` 生效，校验 `text` 非空且 ≤200 字，归一化可选 `voice` / `instructions`（各截断 200 字），返回的 dict 写进任务记录的 `params` 字段（`public_task` 原样透传给 poller）。
- `create_task` 在 `resolve_skill` 后调用它；`speak` 需要边缘在线（命中现有 `not current_device().online` → 503），但不受 `MOTION_TYPES` 运动队列互斥限制。

### `action_move/edge_action_poller.py`（树莓派）

- `fetch_tts_audio(...)` 新增可选 `instructions` 参数，默认值抽为模块常量 `DEFAULT_TTS_INSTRUCTIONS`。
- 新增 `speak_text(text, device, tts_url, tts_model, tts_voice, tts_language, instructions, volume_percent)`：
  - 复用现成的 `shutil.which("aplay"/"paplay")`、`detect_usb_audio_device()`、`apply_voice_volume()`、`play_audio_file()`。
  - `fetch_tts_audio` 拉 WAV → `tempfile` 落盘 → `play_audio_file` → `unlink`。
  - 返回 `(ok, stdout, stderr)`，成功时 stdout 含 `[INFO] speak_played chars=.. device=.. voice=.. text=..`。
  - 不受 `--no-voice` 影响（该开关只静音自动的动作完成提示音）。
- 主循环：`action == "speak"` 分支，发一次 `running` 心跳后调 `speak_text`，**不走** `execute_with_running_heartbeats` → controller `/execute`；`if ok and action != "speak"` 跳过 `schedule_completion_voice`（否则会念「speak完成」）。

### `common_api_manager/static/robot_skills.js`

`SKILLS` 数组加 `speak` 行；`cloudCommand` / `localCommand` / `rosCommand` 各加 `speak` 分支展示真实机制（`/common/api/tts/speech` + `aplay`，无 ROS 话题）；`runSkill` 对 `speak` 弹 `prompt` 收文本塞进 `payload.params`。

### `action_move/atomic_skills.md`

补 `speak` 的 curl 契约、执行链路、技能总览表 / 执行规则表两行、已知边界。

### `action_move/regression_guard.py`

`speak` 加入 `REQUIRED_SKILLS`；`local_guard` 新增 4 项断言（缺 text → 400 / params 透传到任务记录 / 离线 → 503 / poller 暴露 `speak_text`）。

## 执行链路

```
POST /action/api/tasks {"action":"speak","params":{"text":...}}
  → 云端 server.py: build_task_params 校验 → 入队 (type=speak, 与运动同队列, 急停优先)
  → Pi edge_action_poller.py: GET /api/tasks/next 取到 → action=="speak" 分支
  → speak_text(): GET https://www.wangyutang.cn/common/api/tts/speech 合成 WAV
  → aplay -D plughw:2,0 (USB 声卡)
  → POST /api/tasks/result complete，output 带 speak_played
```

不经 `edge_ros_controller.py` 的 `/execute`。

## Verification

本地：

- `python3 -m py_compile action_move/{server,edge_action_poller,edge_ros_controller,regression_guard}.py`
- `python3 -m json.tool action_move/skill_catalog.json`
- `node --check common_api_manager/static/robot_skills.js`
- `python3 regression_guard.py --local` 全绿（含新增 4 项）
- `pytest action_move/tests/` 21 passed / 1 skipped
- `speak_text()` 三条路径（成功 / 空文本 / TTS 失败）单测通过

部署（commit `ea33528` → push `feature/llm-manager` → CI）：

- **Deploy action_move #68** ✅ / **Deploy common-api #27** ✅（smoke 通过）
- **Deploy robot platform #170** ❌ —— 卡在 `Run CI integration tests`，本分支自 #166（8/25）起一直红，与本次改动无关（`ci_tests/` 未改，distance-mapping 测试不校验技能数量/存在性）；真正的部署步骤 1–11 全绿
- `https://www.wangyutang.cn/action/api/skills` 返回 21 个技能，含完整 `speak` 定义
- turbopi-01 online、idle（新版 poller 重启正常）

真机端到端：

- 任务 `1788801001859-34d347` → `complete`，completion latency 20.6s（合成+播放）
- Pi output：`[INFO] speak_played chars=36 device=plughw:2,0 voice=vivian text=你好，我是 turbopi 零一号...`
- 用户现场听到播报声。

## 已知边界 / 踩坑

- **音量全局设置**：`action_move` 持久设置 `voice_volume_percent` 默认 `0.0`，poller 播放前会把 `Speaker` 混音器设成 `0% mute`。首次真机测试 output 显示 `voice_volume_percent=0 mixer_control=Speaker`、任务 `complete` 但一开始几乎无声；提高音量用 `POST /action/api/settings`（会一并影响动作完成提示音）或 `https://www.wangyutang.cn/action/` 页面。误判过的假设：以为 `speak` 任务参数能单独带音量——不能，音量只走持久 `settings`。
- **急停不打断播放**：`emergency_stop` 目前只清运动类待执行任务、只对 `/cmd_vel` 发停车，不会 kill 正在播放的 `aplay`；已排进队列的 `speak` 也不会被急停取消。
- **仅 turbopi-01 生效**：只在装了 `edge_action_poller.py` 且接了音箱的机器人上工作；WonderEcho Pro 是独立设备，仍走 `audio_interact` 的 `/api/device/{id}/broadcast`（它要的是全双工 / barge-in 流式）。
- **两个 TTS 入口**：`action_move` 边缘用 `/common/api/tts/speech`，`audio_interact` 用自己的 `/api/tts`，最终都打 `common_api` TTS，音色一致，但 voice/style 参数需在目录里对齐维护。

## Follow-up

- 2026-09-08：`audio_interact` 的 TTS 合成区加了「🚗 推送到实车播报」按钮（见上「UI 入口」），走同源 `/action/api/tasks` speak 技能。

候选：

- 急停时同时终止本机 `aplay` 进程 + 清 pending `speak` 任务。
- `robot_skills.js` 用真正的文本输入框替代 `window.prompt`。
- 若要「一次合成、多端播」（浏览器 + Pi + ESP32），再在 `audio_interact` 抽 `POST /api/say {text, targets[]}` 统一入口。
