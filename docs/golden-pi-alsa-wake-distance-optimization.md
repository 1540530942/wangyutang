# 金标用例 pi_alsa_wake_distance_001 分析与优化验收标准

> 用例位置：`audio_interact/tests/golden/sessions/pi-1783880433141/`
> 5 turns：sleeping 闲聊 → 唤醒词 → look_right → move_backward(5cm) → empty
> 前置依赖：耗时基线部分依赖 dashboard 重构第 2 步埋点落盘（见 `dashboard-restructure-plan.md`）
> 制定日期：2026-07-14

## 一、现有 CI 已覆盖（不重复做）

- 文本规范化比对（`strip_text` 去标点，语气词/标点差异不误报）；
- VAD 边界 ±400ms 容差断言（`VAD_TOL_MS`）；
- 唤醒状态机推进断言（sleeping→awake 顺序、唤醒前不路由）；
- turn_003 距离**正例**：`distance_cm==5.0`、`unit_distance_cm==5.0`
  （`ci_tests/robot_sandbox/test_react_distance_guard.py`、`ci_tests/audio_interact/test_golden_semantic_pipeline.py`）。

## 二、优化验收标准

### 1. 回放稳定性（flaky 防护）

- 连续 3 次完整 WS 回放，全部断言通过率 3/3；
- 记录 3 次回放的 VAD 边界实测抖动：若 P95 < 200ms，将该案容差从 400ms 收紧到 200ms
  （用实测数据定容差，不拍脑袋）。

### 2. 距离参数反例

- turn_002（look_right）断言 dispatch 参数中**不含** `distance_cm`，
  且 `settings.unit_distance_cm` 不被改写；
- 与 turn_003 正例成对：该有距离时有、不该有时没有，防止距离参数误注入非移动技能。

### 3. empty turn 负向断言

- turn_004 在现有"文本为空、状态 empty"之上，补充断言：**无路由调用、无 TTS 产生**
  （空文本触发下游动作是真实故障模式）。

### 4. 时延基线与优化（依赖第 2 步埋点落盘）

优化流程（顺序不可跳）：

1. **基线先行**：live 执行连续 3 次，各 turn 的 `asr_elapsed_ms` 等耗时字段落盘，取 P95 定为基线；
2. **定位瓶颈**：用瀑布图确认各阶段（ASR / 路由 / TTS）耗时占比，只优化占比最大的阶段；
3. **针对性优化**：每轮只做一项优化改动，部署后复测 3 次取 P95 与基线对照；
4. **回归红线**：优化后全部功能断言（第 1-3 条 + 现有 CI）仍须全绿——不允许用正确性换时延。

停止条件（满足其一即停止并汇报）：

- 达到目标阈值（基线确定后与用户商定具体数值）；
- 连续两轮优化改动 P95 改善 < 10%（视为收益耗尽或在追测量噪声）。

## 三、验收汇总表

| # | 验收项 | 判定方式 |
|---|---|---|
| 1 | 3 次回放 3/3 通过 | CI 循环执行 |
| 2 | VAD 容差按实测收紧或维持并记录依据 | 抖动数据 + 容差值变更/不变的说明 |
| 3 | turn_002 无距离参数反例断言存在且通过 | CI 断言 |
| 4 | turn_004 无路由/无 TTS 断言存在且通过 | CI 断言 |
| 5 | 时延基线落盘并记录 P95 | 部署后 live 执行 3 次 |
| 6 | 优化改动 P95 对照基线有效且功能全绿 | 部署后复测 |
