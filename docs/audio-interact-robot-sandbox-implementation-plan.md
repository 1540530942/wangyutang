# audio_interact / robot_sandbox 拆分落地方案（可实现、可验收版）

更新时间：2026-07-07
设计背景见 `docs/audio-interact-robot-sandbox-design.md`。本文档只回答三个问题：**改什么文件、暴露什么契约、怎么验收**。每个阶段独立交付、独立验收、可独立回滚，按 P1 → P5 顺序执行。

> **状态更新（2026-07-07）**：P1–P4 已全部实现并实车验收通过。原计划延后的包/容器重命名（见 §13、§6、§8）已执行完成——`audio_recognition` 包/容器/卷已更名为 `robot_sandbox`，公网 `/audio/*` 路由保留作兼容别名。下文保留原始计划表述以供追溯。

## 0. 目标边界（一句话版）

```text
audio_interact (8097)：音频 -> ASR 文本；tts_text -> TTS 音频 -> 播放。拥有音频文件和输入模式设置。
robot_sandbox  (8095)：文本 -> tool_calls -> 校验 -> 安全 -> 执行 -> diagnostics -> tts_text。不碰音频。
```

过渡期 `robot_sandbox` 曾继续使用 `audio_recognition` 包名和容器名，只增加新语义接口。**（2026-07-07 已完成重命名为 `robot_sandbox`。）**

## 1. 现状基线（已核实，落地前提）

| 事实 | 位置 |
|---|---|
| VAD 流式 + ASR + 唤醒 + TTS 推送已在 audio_interact | `audio_interact/server.py`、`wake_state.py` |
| Pi 流式采集端已独立瘦依赖（无 audio_recognition 包） | `audio_interact/edge_streamer.py`、`requirements-edge.txt` |
| envelope 已含 tool_calls / dispatch_results / latency_ms / dispatch_mode | `audio_recognition/core/envelope.py` |
| dry_run / cloud_queue / local_first 已实现 | `audio_recognition/tools/dispatcher.py` |
| **缺口 1**：WonderEchoPro Pi 监听器在边缘侧调 ASR，且 import 整个 audio_recognition 包 | `audio_recognition/transport/edge_listener.py:17` |
| **缺口 2**：input_mode / manual_recording 设置及持久化在 audio_recognition；audio_interact 无设置 API、compose 无数据卷 | `audio_recognition/web/server.py`（AudioSettings）、`docker-compose.yml` |
| **缺口 3**：音频归档走 `/api/results` 的 audio_base64，改纯文本链路后 replay 素材断链 | `audio_recognition/web/server.py`（save_embedded_audio） |
| **缺口 4**：Pi 本地 TTS 播放依赖 edge_listener 自取自播，audio_interact 的 TTS 只推浏览器 WS | `transport/edge_listener.py`、`audio_interact/server.py` |

## 2. P1 — robot_sandbox 命令契约（纯增量，先行）

### 改动

| 文件 | 动作 |
|---|---|
| `audio_recognition/web/server.py` | 新增 `POST /api/command`，包装现有 `route_transcript()`，映射为下述契约 |
| `audio_recognition/core/contracts.py` | 新增 `CommandRequest` / `CommandResponse` pydantic 模型 |
| `audio_recognition/tests/unit/test_command_contract.py` | 新增契约单测 |

**不改** `/api/recognize-text`、`/api/results` 的任何请求/响应字段。

### 契约

请求：

```json
POST /api/command
{
  "text": "前进10厘米",
  "device_id": "turbopi-01",
  "dispatch_mode": "dry_run | cloud_queue | local_first",
  "context": {
    "source": "audio_interact",
    "audio_url": "http://audio-interact:8097/api/audio/seg-xxx.wav",
    "asr": {},
    "wake_status": "active"
  }
}
```

响应（字段与 envelope 的映射固定如下）：

```json
{
  "ok": true,
  "command_id": "<envelope.envelope_id>",
  "skill_id": "move_forward",
  "tool_calls": "<envelope.tool_calls>",
  "execution_results": "<envelope.dispatch_results>",
  "diagnostics": {
    "planner": "<react_agent mode>",
    "safety": "passed | rejected",
    "errors": [],
    "latency_ms": "<envelope.latency_ms>"
  },
  "tts_text": "<现有 _build_tts_text(envelope)>",
  "envelope": "<envelope 全量 dump>"
}
```

约定：`context` 整体存入 envelope（`audio_url` 落在 envelope 内可回查）；`dispatch_mode` 缺省 `dry_run`；`X-Audio-Token` 校验与现有 `require_token` 一致。

### 验收标准

```bash
# A1: dry_run 契约字段齐全
curl -s -X POST http://localhost:8095/api/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"前进","device_id":"acc-test","dispatch_mode":"dry_run"}' | \
  jq -e '.ok==true and .command_id!="" and .skill_id=="move_forward"
         and (.tool_calls|length)>=1
         and (.execution_results[0].status=="dry_run")
         and .diagnostics.safety=="passed"
         and .tts_text!=""'

# A2: 拒绝路径可诊断（空文本）
curl -s -X POST http://localhost:8095/api/command -H 'Content-Type: application/json' \
  -d '{"text":"","device_id":"acc-test"}' -o /dev/null -w '%{http_code}\n'   # 期望 400

# A3: envelope 可回查、可 replay
CID=$(curl -s -X POST http://localhost:8095/api/command -H 'Content-Type: application/json' \
  -d '{"text":"左转","device_id":"acc-test","dispatch_mode":"dry_run"}' | jq -r .command_id)
curl -s http://localhost:8095/api/envelopes/$CID | jq -e '.envelope'
curl -s -X POST "http://localhost:8095/api/envelopes/$CID/replay?replay_from=text" | jq -e .

# A4: 旧链路零回归
python -m pytest audio_recognition/tests -q          # 全部通过
python audio_recognition/scripts/run_regression_suite.py   # 通过率不低于基线
```

回滚：删除新路由即可，无其他链路依赖。

## 3. P2 — audio_interact 设置中心与持久化

### 改动

| 文件 | 动作 |
|---|---|
| `audio_interact/settings.py` | 新增：`input_mode`(`web_input|wonderechopro|vad_asr`)、`manual_recording_enabled`，JSON 落盘到 `AUDIO_INTERACT_DATA_DIR`（默认 `/app/data`），字段与现有 `AudioSettings` 保持一致 |
| `audio_interact/server.py` | 新增 `GET/POST /api/settings`、`POST /api/manual-recording/start|stop`，鉴权沿用 `X-Audio-Token` |
| `docker-compose.yml` | audio-interact 增加 `audio_interact_data:/app/data` 卷 |
| `audio_recognition/web/server.py` | `/api/settings`、`/api/manual-recording/*` 改为**代理转发**到 audio_interact（`AUDIO_INTERACT_URL` 环境变量），转发失败时回落本地文件（兼容 shim） |

### 验收标准

```bash
# B1: 写入并读取
curl -s -X POST http://localhost:8097/api/settings -H 'Content-Type: application/json' \
  -d '{"input_mode":"wonderechopro","manual_recording_enabled":true}' | jq -e '.ok==true'
curl -s http://localhost:8097/api/settings | jq -e '.settings.input_mode=="wonderechopro"'

# B2: 重启不丢（验证卷持久化）
docker compose restart audio-interact && sleep 5
curl -s http://localhost:8097/api/settings | jq -e '.settings.manual_recording_enabled==true'

# B3: 旧入口透明兼容（Pi 现有轮询不感知迁移）
curl -s http://localhost:8095/api/settings | jq -e '.settings.input_mode=="wonderechopro"'
curl -s -X POST http://localhost:8095/api/manual-recording/stop | jq -e '.ok==true'
curl -s http://localhost:8097/api/settings | jq -e '.settings.manual_recording_enabled==false'
```

回滚：去掉 shim 转发（audio_recognition 恢复读本地文件），Pi 侧无感。

## 4. P3 — audio_interact 分段音频入口（WonderEchoPro 云端汇合点）

### 改动

| 文件 | 动作 |
|---|---|
| `audio_interact/server.py` | 新增 `POST /api/audio/segment`（multipart：`file`=WAV、`device_id`、`session_id` 可选）。内部复用现有 `_process()` 路径：ASR → 唤醒门控 → 调 P1 的 `POST /api/command` → 组装响应。WAV 落盘到数据卷 `segments/`，生成 `audio_url` 传入 command context |
| `audio_interact/server.py` | 新增 `GET /api/audio/{name}` 供回放/replay 取音频 |
| `audio_interact/server.py` | `_process()` 的路由目标由 `/api/recognize-text` 切换为 `/api/command`（VAD 流式路径同步受益，响应字段兼容映射） |

### 契约

```json
POST /api/audio/segment  (multipart)
响应:
{
  "ok": true,
  "session_id": "…",
  "text": "<ASR 文本>",
  "wake_status": "active | asleep | woken | empty",
  "command": { "<P1 CommandResponse，未唤醒/空文本时为 null>" },
  "tts_text": "…",
  "tts_audio_base64": "<wav，Pi 直接播放；文本为空时省略>",
  "audio_url": "/api/audio/seg-xxx.wav",
  "elapsed_ms": 0
}
```

### 验收标准

```bash
# C1: 静音段正确判空（不产生 command，不产生 TTS）
python3 - <<'EOF'
import io,wave,requests
buf=io.BytesIO()
with wave.open(buf,'wb') as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\x00'*32000)
r=requests.post('http://localhost:8097/api/audio/segment',
  files={'file':('t.wav',buf.getvalue(),'audio/wav')},data={'device_id':'acc-test'}).json()
assert r['ok'] and r['text']=='' and r.get('command') is None, r
print('C1 pass')
EOF

# C2: 真人语音段全链路（用真实录音 fixture，如 "前进"）
curl -s -X POST http://localhost:8097/api/audio/segment \
  -F 'file=@smoke/fixtures/qianjin.wav' -F 'device_id=acc-test' | \
  jq -e '.ok==true and .text!="" and .command.skill_id=="move_forward"
         and .tts_audio_base64!=null and .audio_url!=""'

# C3: 音频归档链路不断（envelope 里能找回原始音频）
#   用 C2 返回的 command.command_id：
curl -s http://localhost:8095/api/envelopes/<command_id> | jq -e '.envelope | tostring | contains("audio_url")'
#   audio_url 可下载且为 WAV：
curl -s http://localhost:8097/api/audio/<name> -o /tmp/seg.wav && file /tmp/seg.wav | grep -q WAVE

# C4: VAD 流式路径回归（浏览器/edge_streamer 走 /ws/audio 说"前进"，
#     WS result 帧仍含 text/skill_id/tts_text，且随后收到 TTS 二进制帧）
```

前置条件：录制 `smoke/fixtures/qianjin.wav`（16 kHz 单声道，内容"前进"），随仓库提交。

回滚：新端点独立，删除即回滚；`_process()` 路由目标用环境变量 `ROUTE_COMMAND_API=1` 开关控制。

## 5. P4 — Pi 端 WonderEchoPro 监听器迁移（唯一动真车的阶段）

### 改动

| 文件 | 动作 |
|---|---|
| `audio_interact/edge/wonderecho_listener.py` | 新增：录音循环结构参考 `audio_recognition/transport/edge_listener.py`（settings 轮询 + arecord + 异步上传），但**只做**：轮询 `GET {AUDIO_INTERACT}/api/settings` → `arecord` 定长 WAV → `POST /api/audio/segment` → 播放响应中的 `tts_audio_base64`（aplay/paplay）。零第三方包、零 `audio_recognition` 依赖 |
| `audio_interact/edge/recorder.py` | 从 `transport/recorder.py` 复制（arecord 封装） |
| `audio_interact/edge/config.example.json` | `{server, token, device_id, recorder{...}}` |
| 旧 `transport/edge_listener.py` | 保留不动，作为回滚路径 |

### 验收标准

```bash
# D1: 依赖隔离（静态检查，CI 可执行）
grep -rn "audio_recognition" audio_interact/edge/ && echo FAIL || echo "D1 pass"
python3 -c "import ast,sys; tree=ast.parse(open('audio_interact/edge/wonderecho_listener.py').read()); \
  mods={n.name.split('.')[0] for x in ast.walk(tree) for n in getattr(x,'names',[]) if isinstance(x,(ast.Import,))} | \
  {x.module.split('.')[0] for x in ast.walk(tree) if isinstance(x,ast.ImportFrom) and x.module}; \
  bad=mods-{'argparse','base64','json','os','shutil','subprocess','sys','tempfile','threading','time','urllib','pathlib','typing','io','wave','recorder','__future__'}; \
  sys.exit(f'FAIL {bad}' if bad else print('D1 pass'))"

# D2: Pi 单机一次通（真机，ssh pi@raspberrypi）
python3 wonderecho_listener.py --config config.json --once
#   期望 stdout JSON: ok==true、text 非空、command.skill_id 正确、本地扬声器有 TTS 播放

# D3: 端到端真车验收（web 页选 WonderEchoPro → 开始录音 → 对麦克风说"前进"）
#   - audio_recognition dashboard 出现新 envelope，diagnostics.latency_ms 含 planning/dispatch
#   - action_move 收到任务（dispatch_mode=cloud_queue 时）
#   - Pi 播报 tts_text
#   - envelope 中 audio_url 可下载回放本次录音

# D4: 回滚演练（必须实际演练一次）
#   Pi 上把 systemd 服务切回旧 edge_listener.py，D3 场景旧链路仍通。
```

切流方式：Pi 上仅替换 systemd 单元指向的脚本和 config 中的 server URL（指向 audio_interact:8097），单台 turbopi-01 先行；回滚即切回旧单元。

## 6. P5 — 收尾（UI 迁移与观测面）

- 输入模式 UI 从 `robot_sandbox/web/static` 迁到 audio_interact（或改调 8097 API），保留 `/audio/api/*` nginx 兼容路由。
- 事件观测面**暂不拆**：audio_interact / 新 Pi 监听器继续把 stage 事件投递到 robot_sandbox `POST /api/events`，dashboard 单点可见。
- `robot_sandbox` 包/容器重命名：原计划 P1–P4 验收通过后另行提案。**（2026-07-07 已执行：`audio_recognition` → `robot_sandbox`，公网 `/audio/*` 保留兼容别名。）**

验收标准：web 页三种 input_mode 切换后，`GET :8097/api/settings` 值一致；dashboard 事件流中能看到来自 audio_interact 的 recording/model_asr/text_display 事件。

## 7. 总验收清单（全部勾完即整体完成）

| # | 项 | 验收命令/方式 | 阶段 |
|---|---|---|---|
| 1 | `/api/command` 契约字段齐全（dry_run） | A1 | P1 |
| 2 | envelope 可回查可 replay | A3 | P1 |
| 3 | 存量单测+回归套件零回归 | A4 | P1 |
| 4 | 设置读写 + 容器重启持久化 | B1/B2 | P2 |
| 5 | 旧设置入口透明代理 | B3 | P2 |
| 6 | 静音段判空 | C1 | P3 |
| 7 | 语音段→ASR→command→TTS 单请求闭环 | C2 | P3 |
| 8 | 音频归档进 envelope 且可下载 | C3 | P3 |
| 9 | VAD 流式路径回归 | C4 | P3 |
| 10 | Pi 边缘零 audio_recognition 依赖 | D1 | P4 |
| 11 | Pi 真机 --once 通 | D2 | P4 |
| 12 | 真车端到端（含 action_move 下发与 TTS 播报） | D3 | P4 |
| 13 | 回滚演练通过 | D4 | P4 |
| 14 | UI 切换与事件观测面正常 | 第 6 节 | P5 |

## 8. 明确不做（本方案范围外）

- ~~不重命名 `audio_recognition` 包/容器/卷。~~ **（已于 2026-07-07 完成重命名为 `robot_sandbox`；公网 `/audio/*` 路由保留兼容。）**
- 不修改 `/api/recognize-text`、`/api/results` 的请求/响应字段。
- 不拆分事件/dashboard 观测面。
- 不改动 action_move、camera_snapshot、slam_mapping 的任何接口。
