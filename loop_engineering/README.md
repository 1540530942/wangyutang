# loop_engineering

Loop 评估框架，用于对 audio_recognition 语音识别 → 技能路由 → 机器人执行的完整链路做自动化质量评估。

## 结构

```
loop_engineering/
└── audio/
    ├── case.py        LoopCase 数据类，定义单条测试场景
    ├── runner.py      执行单条 case（plan_only / simulate / execute_on_robot）
    ├── evaluator.py   对 CaseResult 评分（planning_ok, execution_ok, score 0-1）
    ├── observer.py    观测机器人执行结果（前方距离、相机帧等）
    ├── session.py     批量执行多条 case，汇总通过率和平均 reward
    └── run.py         CLI 入口
```

## 评分标准

| 指标 | 权重 | 说明 |
|---|---|---|
| 规划正确（route + skill_id 匹配） | 60% | exact-alias 或 LLM 路由到正确技能 |
| 执行成功（机器人 ok=true） | 40% | 动作实际在硬件上完成 |

通过标准：`pass_rate ≥ 0.80`，`avg_reward ≥ 0.70`

## 运行模式

| 模式 | 说明 |
|---|---|
| `plan_only` | 只走规划器，不下发机器人指令（适合 CI） |
| `simulate` | 规划 + 模拟执行（无真实机器人） |
| `execute_on_robot` | 完整链路，真实执行并观测结果 |

## 规划器类型

| 类型 | 说明 |
|---|---|
| `rule` | 纯规则+别名匹配，不调 LLM，适合测试 alias 覆盖度 |
| `llm` | 完整 LLM ReAct 链路，需要 router_config 配置 |

## 与 pi5_robot/loop_engineering 的关系

`pi5_robot/loop_engineering/` 是针对机器人场景的专用评估库（scenario_generator + reward_evaluator），
本目录 `loop_engineering/audio/` 专注于语音识别链路评估，两者独立但共用评分思路。
