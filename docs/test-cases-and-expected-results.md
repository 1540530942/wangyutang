# 测试用例语料与预期结果

更新时间：2026-07-05

本文档从当前仓库测试文件整理，列出主要测试语料、输入场景和预期结果。它不是测试运行报告，而是测试用例目录，便于人工复核和补齐质量门禁。

## 1. robot_sandbox 语音控制与 ReAct 路由

来源：

- `robot_sandbox/tests/unit/test_react_pipeline.py`
- `robot_sandbox/tests/integration/test_regression_suite.py`
- `robot_sandbox/tests/fixtures/audio_cases.json`
- `smoke/cases.py`
- `smoke/test_routing.py`

### 1.1 基础动作语料

| 语料 | 预期 skill_id | 预期 route/tool | 预期结果 |
|---|---|---|---|
| `前进` | `move_forward` | `action` / `dispatch_action` | exact alias 命中；前进前需要前方距离/安全检查；安全通过时 dry-run 或下发动作 |
| `往前走` | `move_forward` | `action` / `dispatch_action` | 解析为前进 |
| `向前走` | `move_forward` | `action` / `dispatch_action` | exact alias 命中 |
| `后退` | `move_backward` | `action` / `dispatch_action` | exact alias 命中；解析为后退 |
| `往后走` | `move_backward` | `action` / `dispatch_action` | 解析为后退 |
| `左移` | `move_left` | `action` / `dispatch_action` | exact alias 命中；解析为左移 |
| `向左平移` | `move_left` | `action` / `dispatch_action` | 解析为左移 |
| `右移` | `move_right` | `action` / `dispatch_action` | exact alias 命中；解析为右移 |
| `向右平移` | `move_right` | `action` / `dispatch_action` | 解析为右移 |
| `左转` | `turn_left` | `action` / `dispatch_action` | exact alias 命中；解析为左转 |
| `向左旋转` | `turn_left` | `action` / `dispatch_action` | 解析为左转 |
| `右转` | `turn_right` | `action` / `dispatch_action` | exact alias 命中；解析为右转 |
| `向右旋转` | `turn_right` | `action` / `dispatch_action` | 解析为右转 |
| `复位` / `reset` | `reset_pose` | `action` / `dispatch_action` | 解析为姿态复位 |
| `急停` / `stop` | `emergency_stop` | `action` / `emergency_stop` | exact alias 命中；立即急停 |

### 1.2 摄像头/云台/灯光语料

| 语料 | 预期 skill_id | 预期结果 |
|---|---|---|
| `向左看` | `look_left` | 生成 `dispatch_action`，云台/视角向左 |
| `向右看` | `look_right` | 生成 `dispatch_action`，云台/视角向右 |
| `向上看` | `look_up` | 生成 `dispatch_action`，云台/视角向上 |
| `向下看` | `look_down` | 生成 `dispatch_action`，云台/视角向下 |
| `开灯` | `rgb_on` | 生成 `dispatch_action`，打开 RGB |
| `关灯` | `rgb_off` | 生成 `dispatch_action`，关闭 RGB |

### 1.3 表情语料

| 语料 | 预期 skill_id | 预期 route | 预期结果 |
|---|---|---|---|
| `笑一笑` | `face_happy` | `face` | 路由到表情服务 |
| `超开心` | `face_joy` | `face` | 路由到开心表情 |
| `委屈一下` | `face_sad` | `face` | 路由到难过表情 |
| `生气一点` | `face_angry` | `face` | 路由到生气表情 |
| `说句话` | `face_speak` | `face` | 路由到说话表情/口型 |
| `眨眨眼` | `face_blink` | `face` | 路由到眨眼 |
| `表情复位` | `face_reset` | `face` | 表情恢复默认 |

### 1.4 观察与视觉语料

| 语料 | 预期 skill_id/tool | 预期 route | 预期结果 |
|---|---|---|---|
| `前方距离` | `front_distance` | `observation` | 调用前方距离观测，返回 `front_distance_estimate_cm` |
| `拍照` | `camera_snapshot` | `observation` | 调用摄像头截图 |
| `前面有什么` | `inspect_scene` | `observation` | 调用视觉模型描述前方 |
| `前面有没有人` | `inspect_scene` | `observation` | 调用视觉模型判断是否有人 |
| `有没有人` | `inspect_scene` | `observation` | 调用视觉模型判断是否有人 |
| `前面有人吗` | `inspect_scene` | `observation` | 调用视觉模型判断是否有人 |
| `前方有什么` | `inspect_scene` | `observation` | 调用视觉模型描述场景 |
| `前面有障碍物吗` | `inspect_scene` | `observation` | 调用视觉模型判断障碍物 |

### 1.5 复杂 ReAct 语料

| 语料 | 预期结果 |
|---|---|
| `请左转一下` | LLM 生成 `dispatch_action(turn_left)`；envelope 记录 tool/task；dry-run 状态为 `dry_run` |
| `先左转然后右转` | 生成有序任务：`turn_left` -> `turn_right`，order 为 1、2 |
| `左转，不要往上看` | 只执行 `turn_left`；否定片段 `不要往上看` 不生成 `look_up` |
| `先往前走，再往后走，抬头看，不要往前走了，低头看` | 不通过规则 fallback；LLM 生成序列：`move_forward` -> `move_backward` -> `look_up` -> `look_down` |
| `请前进然后右转` | native 多 tool_calls 应按顺序执行：`move_forward` -> `turn_right`；deferred tool_call 不丢失 |
| `看看前面有没有障碍物，没有障碍物的话前进15cm` | 不走 exact/preflight 规则；先 `inspect_scene`；若视觉返回无障碍，则生成 `move_forward`，`settings_override.unit_distance_cm=15.0` |
| `先左转然后后退10cm` | 不走 exact/preflight 规则；生成 `turn_left` -> `move_backward`；后退动作携带 `settings_override.unit_distance_cm=10.0` |

### 1.6 安全与异常语料

| 场景/语料 | 预期结果 |
|---|---|
| `前进` 且前方距离清晰、置信度高、距离大于阈值 | 允许 `move_forward`，dry-run/执行成功 |
| `前进` 且前方距离过近 | 拒绝 `move_forward`，reason 为 `front_distance_too_close` |
| `front distance then forward` 且距离置信度低 | 拒绝前进，预期 reason 为 `front_distance_low_confidence` |
| `front distance then forward` 且距离数据过期 | 拒绝前进，预期 reason 为 `front_distance_stale` |
| `您的指令已经完成了` | 不生成动作，`skill_id=""`，route 为 `none` |
| `今天上海天气怎么样` | 不生成机器人动作，`skill_id=""`，route 为 `none` |
| ASR/文本为空 | envelope reason 为 empty transcript，不生成动作 |

注意：当前完整 `robot_sandbox.tests.unit.test_react_pipeline` 中前方距离相关测试存在实现/测试预期不一致，实际运行时仍需先修复这组用例。

## 2. audio_interact 唤醒词状态机

来源：`audio_interact/tests/test_wake_state.py`

| 输入语料 | 初始状态 | 预期结果 |
|---|---|---|
| `你好，瓦力` | 任意 | 识别为唤醒词 |
| `你好 哇力` | 任意 | 识别为唤醒词同音变体 |
| `你好 walle` | 任意 | 识别为英文唤醒词 |
| `前进` | sleeping | 不路由，状态保持 `sleeping` |
| `你好瓦利` | sleeping | 状态变为 `awake`，本句不路由动作 |
| `前进` | awake | 路由，`route_text="前进"` |
| `你好瓦力向左转` | sleeping | 直接唤醒并路由后缀，`route_text="向左转"` |
| `退下吧` | awake | 状态变回 `sleeping`，不路由 |
| `退下吧` 后再说 `前进` | sleeping | 不路由 |

## 3. common_api_manager 公共模型接口

来源：

- `common_api_manager/tests/test_smoke.py`
- `common_api_manager/tests/test_lab_selection.py`

### 3.1 健康检查

| 接口 | 预期 provider/结果 |
|---|---|
| `GET /api/health` | `status=ok` |
| `GET /api/asr/qwen3/health` | `provider=lv_qwen_asr` |
| `GET /api/tts/qwen3/health` | `provider=lv_qwen_tts` |
| `GET /api/chat/qwen3/health` | `provider=lv_qwen_chat` |
| `GET /api/llm/qwen3-32b/health` | `provider=dashscope_qwen_chat` |
| `GET /api/llm/qwen3.6-35b/health` | `provider=spark_qwen_chat` |
| `GET /api/vision/lv/health` | `provider=lv_qwen_vision` |
| `GET /api/vision/spark/health` | `provider=spark_qwen_vision` |
| `GET /api/vision/dashscope/health` | `provider=dashscope_qwen_vision` |

### 3.2 功能请求

| 输入/请求 | 预期结果 |
|---|---|
| LV chat：`只回答数字：1+1=` | HTTP 200；`provider=lv_qwen_chat`；`text` 非空 |
| DashScope chat：`只回答数字：2+2=` | HTTP 200；`text` 非空 |
| Spark LLM：简单文本问题 | HTTP 200；`provider=spark_qwen_chat`；`text` 非空 |
| LV vision：64x64 红色 JPEG + `图片主要是什么颜色？` | HTTP 200；`provider=lv_qwen_vision`；返回文本非空 |
| Spark vision：64x64 红色 JPEG + `图片主要是什么颜色？` | HTTP 200；`provider=spark_qwen_vision`；返回文本非空 |
| DashScope vision：64x64 红色 JPEG + `图片主要是什么颜色？` | HTTP 200；`provider=dashscope_qwen_vision`；返回文本非空 |

### 3.3 common lab 模型选择

| 输入选择 | 预期保存结果 |
|---|---|
| `llm.provider=lv`, `vision.provider=lv` | LLM endpoint 为 `/common/api/chat/qwen3/completions`，model 为 `Qwen3.5-35B-A3B-Q4_K_M.gguf` |
| `llm.provider=lv`, `vision.provider=lv` | Vision endpoint 为 `/common/api/vision/lv/analyze-json`，model 为 `qwen25vl7b-q4km.gguf` |
| 未知 provider | LLM 回退到 `dashscope`，Vision 回退到 `spark` |

## 4. camera_snapshot 摄像头/截图服务

来源：`camera_snapshot/tests/test_smoke.py`

| 场景 | 预期结果 |
|---|---|
| 静态页面加载 | 页面暴露独立的摄像头截图、屏幕截图、表情截图入口 |
| 不同 kind 上传 latest frame | 各 kind 的最新帧互不覆盖 |
| control/stop 请求 | 按 kind 隔离，不影响其他 capture kind |
| task 状态失败 | 失败信息按 kind 记录 |
| legacy mode 映射 | 旧 mode 能映射到正确 frame kind |
| sender 摄像头捕获成功 | 上报真实 camera source，不回退 screenshot |
| sender 摄像头捕获失败 | 不伪装成 screenshot fallback |
| 上传 frame | 发送 capture kind header |
| 获取 latest.jpg | 返回对应 kind 的最新 JPEG |

注意：本地运行该测试需要 `fastapi.testclient` 依赖链完整；当前虚拟环境提示需要 `httpx2`。

## 5. action_move 与 SLAM 上报

来源：`action_move/tests/test_slam_reporting.py`

| 场景 | 输入 | 预期结果 |
|---|---|---|
| 前进里程计 | `skill_id=move_forward`, `unit_distance_cm=20.0` | SLAM payload 的 `dx_m=0.2` |
| 左转角度 | `skill_id=turn_left`, `turn_angle_deg=90` | SLAM payload 的 yaw 为正 90 度 |
| IMU 实际距离覆盖命令距离 | output 含 `[IMU] actual_distance_cm=12.0` | 使用 12cm 作为上报距离 |
| IMU 实际 yaw 覆盖命令角度 | output 含 `[IMU] actual_yaw_deg=...` | 使用 IMU yaw 作为上报角度 |
| 解析 edge actuals | `[IMU] actual_distance_cm=12.3` | 预期解析出实际距离 |

注意：当前 `parse_edge_actuals` 实现返回 `({"actual_distance_cm": 12.3}, "imu")`，测试仍期望单独 dict，存在一个失败点。

## 6. slam_mapping

来源：

- `slam_mapping/tests/test_slam_core.py`
- `slam_mapping/2d_action/tests/test_closed_loop.py`

### 6.1 SLAM core

| 场景 | 输入 | 预期结果 |
|---|---|---|
| 前进 20cm | `update_odometry(dx_m=0.2)` | pose `x_m=0.2`，`distance_travelled_cm=20.0`，trail 增长 |
| 左转 90 度后前进 | yaw 加 `pi/2` 后 `dx_m=0.2` | `x_m≈0`，`y_m=0.2`，`yaw_deg=90.0` |
| 前方 80cm 扫描 | `update_scan([0.8])` | occupancy grid 有 1 个 occupied cell，`map_available=true` |

### 6.2 闭环控制

| 场景 | 输入 | 预期结果 |
|---|---|---|
| 平移闭环 | 目标 10cm，真实 scale=1.2 | 收敛；误差不超过 1cm |
| 旋转闭环 | 目标 45 度，真实 scale=0.85 | 收敛；误差不超过阈值 |
| 标定后单次 feed-forward | 预热 5 次，再移动 10cm | 学到 scale≈1.2；单次命中误差 |
| 异常 scale 防护 | 真实 scale=100 | 标定 scale 被 clamp 到 <=2.5 |
| SLAM pose feedback 闭环 | fake SLAM pose + action commander | 至少一次 command；最终实际距离接近目标 |
| traceability | 移动 20cm | 每个 burst 记录 commanded、actual、error |

## 7. pi5_robot

来源：

- `pi5_robot/tests/test_robot_safety.py`
- `pi5_robot/tests/test_harness_executor.py`

| 场景 | 预期结果 |
|---|---|
| forward move updates pose | 前进会更新模拟机器人 pose |
| front obstacle blocks forward motion | 前方障碍阻止前进 |
| backward distance is limited | 后退距离被限制在安全范围 |
| rotate is limited | 旋转角度被限制 |
| patrol task writes trace and report | harness executor 生成 trace 和 report |

## 8. smoke 必过技能集

来源：`smoke/test_schema.py`、`smoke/test_routing.py`

| 类别 | 预期结果 |
|---|---|
| registry 加载 | `skills/registry.yaml` 可加载，不报错 |
| 必过 skill 存在 | emergency、移动、转向、云台、RGB、观察工具均注册 |
| route/tool 一致性 | smoke cases 中的 tool/route 与 registry 一致 |
| action enum | `dispatch_action` enum 包含动作技能，不包含 observation/emergency |
| duration limits | action schema 中包含 duration 限制 |
| exact alias | `stop`、`急停`、`前进`、`后退`、`左移`、`右移`、`左转`、`右转` 命中快速路径 |
| validator | 合法 tool call 不应被拒绝 |

## 9. 当前质量状态摘录

截至本次盘点，低成本测试运行情况：

| 命令/范围 | 当前结果 |
|---|---|
| `python3 -m unittest discover -s smoke -q` | 56 passed |
| `PYTHONPATH=slam_mapping python3 -m unittest discover -s slam_mapping/tests -q` | 3 passed |
| `PYTHONPATH=slam_mapping/2d_action python3 -m unittest discover -s slam_mapping/2d_action/tests -q` | 6 passed |
| `.venv/bin/python -m pytest audio_interact/tests/test_wake_state.py -q` | 5 passed |
| `PYTHONPATH=pi5_robot .venv/bin/python -m unittest pi5_robot.tests.test_harness_executor pi5_robot.tests.test_robot_safety -q` | 5 passed |
| `.venv/bin/python -m unittest robot_sandbox.tests.unit.test_react_pipeline` | 49 tests 中 6 个失败，集中在 front_distance 预期 |
| `PYTHONPATH=action_move python3 -m unittest discover -s action_move/tests -q` | 5 tests 中 1 个失败，集中在 parse_edge_actuals 返回结构 |

建议优先修复：

1. 统一 `front_distance` 的数据源字段和测试预期。
2. 统一 `parse_edge_actuals` 返回结构或更新测试断言。
3. 增加统一测试入口，例如 `scripts/test_local.sh`，内置必要 `PYTHONPATH` 和 pytest/unittest 分流。
4. 将公网 live smoke 与本地 unit tests 分开，避免质量门禁受外部模型服务波动影响。
