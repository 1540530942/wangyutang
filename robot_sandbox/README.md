# robot_sandbox

语音识别与技能路由服务。接收转录文本，通过规则别名或 LLM ReAct 链路解析成具体技能指令，再分发给机器人执行器。

## 架构

```
ASR 文本输入
  │
  ├─[exact-alias]─▶ skill_registry  ← 纯规则匹配，<1ms，不调 LLM
  │
  └─[LLM 路由]──▶ skill_router / planner
                      │
                      ▼
                  react_agent (ReAct 循环)
                      │
                      ├─ tool_validator   安全校验
                      ├─ tool_call_adapter 工具调用适配
                      └─ executors        实际执行（机器人 / 观测 / 表情）
```

## 核心文件

| 文件 | 职责 |
|---|---|
| `server.py` | FastAPI 服务入口，暴露 `/api/route`、`/api/health` 等 |
| `skill_registry.py` | 技能注册表，exact-alias 快速路径 |
| `skill_router.py` | 路由决策：alias 命中则直通，否则走 LLM |
| `dispatcher.py` | 把路由结果分发给具体 executor |
| `react_agent.py` | ReAct 推理循环，支持多轮工具调用 |
| `planner.py` | 早期规划器（legacy，逐步迁移到 react_agent） |
| `tool_validator.py` | 校验工具调用参数合法性，拦截危险指令 |
| `safety_guard.py` | 安全护栏，过滤不安全输入 |
| `pipeline.py` | 端到端流水线：ASR → 路由 → 执行 → 观测 |
| `envelope.py` | 请求/响应信封格式定义 |
| `edge_audio_listener.py` | Pi 端监听器（alternative to audio_interact） |

## 子目录

| 目录 | 说明 |
|---|---|
| `skills/` | 技能定义与注册 |
| `tools/` | 工具实现（move、camera、sensor 等） |
| `agent/` | ReAct agent prompt 和合约 |
| `transport/` | HTTP/WebSocket 传输层 |
| `safety/` | 安全规则集 |
| `simulation/` | 仿真执行后端（无需真实机器人） |
| `harness/` | 测试 harness |
| `tests/` | 单元/集成测试 |
| `docs/` | 详细设计文档 |

## 路由分类

| route | 说明 |
|---|---|
| `movement` | 底盘运动指令（move_forward 等） |
| `camera` | 摄像头控制与拍照 |
| `sensor` | 传感器读取（超声波等） |
| `system` | 系统控制（RGB、关机等） |
| `face` | 表情控制（转发 smile_face） |
| `unknown` | 无法识别，返回兜底响应 |

## 启动

```bash
# cloud 端
docker compose up robot-sandbox

# 本地开发
uvicorn robot_sandbox.server:app --port 8095 --reload
```
