# WonderEchoPro Pi 模式全双工 + Barge-in 实现方案

> 目标：在 WonderEchoPro Pi 模式下实现**真全双工**语音交互——TTS 播报期间麦克风持续收音、
> 用户随时插话打断、打断响应实车可感知"瞬时"（≤150ms 降音、≤400ms 停播），
> 且不因电机/舵机噪声、TTS 残余回声产生误打断。
>
> 关联文档：[audio-interact-robot-sandbox-design.md](audio-interact-robot-sandbox-design.md)、
> `audio_interact/asr_vad_tts_bargein_feedback_system.md`（回灌评测体系）

---

## 0. 实现状态（2026-07-19）

代码已落地 P0/P1/P2-A/P2-B，全部随附离线回归用例并通过：

| 阶段 | 状态 | 落地位置 |
|---|---|---|
| P0-A 服务端收包/轮处理解耦 | ✅ 已实现 | `audio_interact/server.py` `/ws/audio`：`send_lock` + `turn_queue` + `_turn_worker` + `_run_turn`；`stop_stream` 前 `turn_queue.join()` 保证结果先于 `stream_stopped` |
| P0-B Pi 播放自持 + AEC 参考硬对齐 | ✅ 已实现 | `edge/wonderecho_listener.py` `_Player`（pyalsaaudio 逐帧写 + 写时喂参考；aplay 兜底）+ `_AudioEngine` |
| P1-A 两级打断（本地 duck + 云端 kill） | ✅ 已实现 | `_AudioEngine._local_barge`（RMS duck，hangover 自恢复）+ 收 `speech_start`/`tts_cancel` 硬 kill |
| P1-B 打断弃旧轮 | ✅ 已实现 | 服务端 `_TurnState.cancelled` 拦截 TTS 下发；Pi `discarding` 丢弃至下个 `tts_begin` |
| P2-A 流式 TTS 逐句下发 | ✅ 已实现 | `proto>=2` 走 `_stream_tts`（复用既有 `_iter_tts_stream`）；`proto<2` 保留 base64 兼容 |
| P2-B tts_state + VAD 双 profile | ✅ 已实现 | Pi 播放翻转上报 `tts_state`；`StreamingSileroVad.tts_active` 切 `SILERO_BARGEIN_*` 门槛 |
| P2-C 实车降噪 | ⬜ 可选 | 视 §6 底噪采样决定，未实现 |

回归用例：`ci_tests/audio_interact/test_fullduplex_pipeline.py`（服务端解耦/顺序/流式/barge-in，
假 WS+假 VAD，免 torch）、`ci_tests/audio_interact/test_wonderecho_engine.py`（Pi 两级打断状态机，
纯 stdlib）。Pi 依赖：`audio_interact/edge/requirements.txt`（websockets/pyalsaaudio/speexdsp）。

**仍需人工完成（无法在开发机执行）：**

1. **§6 硬件 AEC 验证**——一键探针 `audio_interact/edge/aec_probe.py` 已备好（stdlib-only，
   跑完自动判读 + 给 DUCK 阈值建议）；只需在物理 Pi + WonderEchoPro 上执行一次。
   代码当前走软件 AEC 路线，对该结论不敏感（硬件 AEC 确认后可把 `_AudioEngine` 的 speex 分支改直通）。
2. **§1 指标实车标定**——`DUCK_RMS_THRESHOLD` 等阈值需按实车底噪采样标定（见 §6 命令）。
3. **联调发版**——服务端与 Pi 端需同步部署（协议二进制帧语义变更，`proto:2` 灰度）。

---

## 1. 体验指标（验收标准）

| 指标 | 目标值 | 测量方法 |
|---|---|---|
| 打断响应（用户开口 → TTS 音量明显下降） | ≤ 150ms | Pi 端日志 `duck_at - local_vad_trigger_at` |
| 打断确认（用户开口 → TTS 完全停止） | ≤ 400ms | Pi 端日志 `kill_at - speech_onset`（对齐录音时间轴） |
| 误打断率（TTS 播放期间无人插话） | 连续 30min 播报 0 次误 kill（允许误 duck 但须自动恢复） | 回灌系统 bargein_runtime.jsonl 统计 |
| 打断句 ASR 完整率（不吞字） | 打断话语首字不丢（依赖 pre-roll） | 金标 session 人工标注比对 |
| 首响应延迟（speech_end → 首句 TTS 开始播放） | ≤ 1.5s | `tts_elapsed_ms` + Pi 播放起始日志 |
| TTS 播放期间 VAD 假阳性（回声误触发 speech_start） | 0（AEC + 高门槛双保险） | 回灌 vad_runtime.jsonl 中 during-TTS 窗口统计 |

---

## 2. 现状与差距

现有链路（2026-07-19，`feature/llm-manager` 分支）：

```
Pi: arecord 16k → AEC(speexdsp) → WS 512样本/帧 上行
Cloud: /ws/audio → StreamingSileroVad 切句 → ASR → 路由 → TTS 整段合成 → base64+binary 下行
Pi: aplay 播放；收到服务端 speech_start 时 kill aplay（打断）
```

| # | 差距 | 位置 | 影响 |
|---|---|---|---|
| G1 | **收包循环被单轮处理阻塞**：`speech_end` 后在循环内 `await` ASR/路由/TTS 全流程 | `server.py:262-286` | 处理期间（2-5s）新音频帧堆在 socket 缓冲，不进 VAD → 打断延迟秒级，全双工不成立 |
| G2 | **AEC 参考信号用 `time.sleep` 定时喂帧**，与 aplay 实际输出无硬对齐 | `edge/wonderecho_listener.py:159-168` | aplay 启动延迟/ALSA 缓冲导致参考错位 >256ms 滤波器长度 → AEC 失效 → 残余回声误触发打断 |
| G3 | **打断只有云端往返一条路**：服务端 VAD 积帧(3×32ms) + 公网 RTT 后才 kill | `wonderecho_listener.py:277-279` | 实测打断延迟 ≈ 300-800ms+G1 阻塞时间，体验差 |
| G4 | **无 speexdsp 时降级为播报期全静音** | `wonderecho_listener.py:133-134` | 降级路径 = 半双工，打断完全不可能 |
| G5 | **打断后服务端不弃旧轮**：上一轮 TTS 仍会合成完并下发 | `server.py` 无取消机制 | 打断后旧播报"复活"，体验混乱 |
| G6 | **TTS 整段合成才下发**；流式 `_push_tts`（`server.py:643`）已实现但是**死代码** | `server.py:268` 走 `_fetch_tts_audio` | 首响应延迟 = 全文合成时间；打断浪费大 |
| G7 | **服务端不知道 Pi 是否在播 TTS**：VAD 全程单一门槛 | 无 tts_state 协议 | 无法在播报窗口提高门槛抗残余回声；回灌评测缺 TTS 时间轴 |
| G8 | aplay 子进程无法运行时调音量 | `wonderecho_listener.py:141-184` | 无法实现"先 duck 后 kill"的两级打断 |

---

## 3. 目标架构

```
┌─ Pi (wonderecho_listener.py) ──────────────────────────────────────┐
│  arecord ─→ AEC ─→ ┬─→ WS 上行（永不因播放暂停）                    │
│            ↑       └─→ 本地快检（RMS/webrtcvad）─→ duck 播放音量     │
│            │                                                       │
│  播放循环（alsaaudio 自持，替代 aplay 子进程）:                      │
│    帧队列 → 逐帧 ×gain 写 ALSA → 同帧推 AEC 参考（写后即推，硬对齐） │
│    支持: duck(gain=0.3) / kill(清队列) / 逐句 WAV 入队              │
│  WS 控制帧: tts_state 上报 / tts_cancel 接收 / speech_start 确认kill │
└────────────────────────────────────────────────────────────────────┘
        ↕ WSS (wangyutang.cn 网关)
┌─ Cloud (server.py /ws/audio) ──────────────────────────────────────┐
│  收包循环: 只做 VAD 喂帧 + 事件下发（永不阻塞）                      │
│  轮处理:  speech_end → 后台 turn task (ASR→路由→流式TTS逐句下发)     │
│  打断:    TTS窗口内 speech_start → cancel turn task + 下发 tts_cancel│
│  VAD:     双 profile（空闲 0.55/3帧；TTS播放窗口 0.70/5帧）          │
└────────────────────────────────────────────────────────────────────┘
```

核心原则：

1. **收音永不停**——上行链路与播放/处理完全解耦（G1、G4）。
2. **打断两级确认**——本地快检先 duck（<150ms，可恢复、允许误触发），云端 VAD 确认后 kill（不可逆、必须准）。
3. **播放自持**——Pi 端自己写 ALSA 帧，同时获得：AEC 参考硬对齐（G2）、逐帧调 gain（G8）、32ms 粒度 kill、逐句排队播放（G6 客户端侧）。
4. **轮世代（turn generation）**——每轮递增编号，打断即作废旧世代，旧世代的 TTS 帧到达即丢弃（G5）。

---

## 4. 分阶段实施

### P0-A 服务端：收包循环与轮处理解耦（G1）

**这是全双工的前提，其他一切改动在此之上才有意义。**

改动集中在 `server.py` `/ws/audio` 的 `speech_end` 分支（`server.py:251-304`）：

```python
# 连接级状态
turn_gen = 0                      # 轮世代
turn_task: asyncio.Task | None = None
tts_playing = False               # 由 Pi 端 tts_state 帧维护（P2-B 前恒 False）

# speech_end 分支：不再内联 await，改为投递后台任务
elif event.get("type") == "speech_end" and event.get("wav_bytes"):
    wav_bytes = event.pop("wav_bytes")
    await websocket.send_text(json.dumps(event, ensure_ascii=False))
    turn_gen += 1
    if turn_task and not turn_task.done():
        await turn_task          # 上一轮还在跑：顺序等待（VAD 已保住音频，不丢）
    turn_task = asyncio.create_task(
        _run_turn(websocket, wav_bytes, turn_gen, session_id, device_id, route_enabled)
    )
    # 循环立即回到 receive()，继续喂 VAD
```

`_run_turn()` 承接原 262-304 行逻辑（ASR → 路由 → TTS → 下发 → 记 session_utterances），
每个下发动作前检查 `gen == turn_gen`，不等则静默丢弃（世代已被打断作废）。

**打断触发**（`speech_start` 分支追加）：

```python
if event.get("type") == "speech_start":
    await websocket.send_text(json.dumps(event, ensure_ascii=False))
    if tts_playing or (turn_task and not turn_task.done() and turn_in_tts_stage):
        turn_gen += 1                                  # 作废当前轮
        await websocket.send_text(json.dumps(
            {"type": "tts_cancel", "session_id": session_id}))
```

注意语义边界：**打断只作废 TTS 下发，不回滚已派发的动作**（路由/action 在 TTS 之前已完成，
物理动作不可撤销）。若轮尚在 ASR/路由阶段用户又说话，视为连续追问，顺序处理，不算打断。

回归保障：`ci_tests/` 现有 WS 用例（`server.py:1153` 起的自测链路）必须全绿；
新增用例"TTS 处理期间持续送音频帧，验证 speech_start 在 500ms 内下发"。

### P0-B Pi 端：播放循环自持（G2 + G8 + 为 P1/P2 打底）

用 `pyalsaaudio` 重写 `_AECContext.play_tts`（`wonderecho_listener.py:141-184`），
替换 aplay 子进程：

```python
class _Player:
    """自持播放循环：帧队列 → ×gain → 写 ALSA → 同帧推 AEC 参考。"""
    def enqueue_wav(self, wav_bytes): ...   # 逐句 WAV 入队（天然支持流式多句）
    def duck(self): self._gain = 0.3        # 本地快检触发，可恢复
    def unduck(self): self._gain = 1.0
    def kill(self): 清空队列 + 停止写入      # 云端确认打断，不可逆
    @property
    def playing(self): ...                  # 供 tts_state 上报

    def _loop(self):  # 播放线程
        while frame := q.get():
            out = scale_pcm16(frame, self._gain)   # 逐帧调 gain
            pcm.write(out)                         # 写 ALSA
            aec_ref_q.put(out)                     # 写后即推参考 → 硬对齐
```

- AEC 参考在**写入 ALSA 的同一时刻**入队，消除 `time.sleep` 定时漂移（G2 根因）。
  ALSA period 缓冲引入的固定延迟（设 period_size=512、periods=4，约 96-128ms）落在
  256ms 滤波器长度内，AEC 可收敛。
- `requirements-edge.txt` 增加 `pyalsaaudio`；**speexdsp 升级为硬依赖**——
  无 AEC 的全静音降级路径保留但打日志告警"半双工降级"，实车部署前置检查必须确认
  speexdsp 可用（G4）。
- 保留 aplay 兜底分支（pyalsaaudio 不可用时），兜底路径不支持 duck，仅支持 kill。

### P1-A Pi 端：两级打断（G3）

**第一级（本地，快、可恢复）**：TTS 播放期间对 AEC 后的干净帧做轻量检测——
RMS 能量门限 + 连续 2 帧（64ms）判定；触发即 `player.duck()`。
检测在 `aec_ctx.process()` 返回后顺路计算，零额外通路。

```python
DUCK_RMS_THRESHOLD = 800      # PCM16 RMS，实车标定（见 §6 验证步骤）
DUCK_HANGOVER_S    = 1.2      # duck 后无后续确认则恢复音量
```

**第二级（云端，准、不可逆）**：收到服务端 `speech_start` 且非静音窗口 → `player.kill()`，
同时丢弃本轮后续到达的 TTS 帧（按轮世代/`tts_cancel` 判断）。
若 `DUCK_HANGOVER_S` 内未收到 `speech_start`（咳嗽、关门声等误触发）→ `player.unduck()` 恢复。

延迟预算核对：本地 duck = 2帧64ms + 处理≈10ms ≈ **75ms** ✅；
云端 kill = 服务端积帧 160ms(P2-B 后 5 帧) + 上行缓冲 ≈50ms + RTT ≈80ms ≈ **290-350ms** ✅。

### P1-B 打断后弃旧轮（G5）

- 服务端：P0-A 的轮世代机制已覆盖——旧世代 turn task 的 TTS 下发被 gen 检查拦截；
  已在网络上飞行的帧由 Pi 端拦截。
- Pi 端：`_recv_loop` 维护 `active_turn`；收到 `tts_cancel` 后，直到下一个 `tts_begin`
  为止的所有二进制帧直接丢弃，不入播放队列。

### P2-A 接通流式 TTS（G6）

服务端 `_push_tts`（`server.py:643`，逐句 `[4B长度][WAV]` 分块）已完整实现，只需接线：

1. `_run_turn` 的 TTS 阶段改调 `_push_tts(ws, tts_text, session_id, turn_idx)`，
   替换 `_fetch_tts_audio` + base64 路径；下发前先发 `{"type":"tts_begin","turn":gen}`，
   `_push_tts` 结束的空二进制帧即 `tts_end` 哨兵（现有约定，`server.py:672`）。
2. **WS 流式路径去掉 `tts_audio_base64`**（避免双份带宽；HTTP/web 模式不受影响）。
   Pi 端 `last_turn_had_b64` 去重逻辑（`wonderecho_listener.py:262-269`）随之删除。
3. Pi 端 `_recv_loop`：二进制帧逐句 `player.enqueue_wav()`——P0-B 的队列天然支持。
   首句到达即开播，后续句子边合成边入队。

预期收益：首响应从"全文合成时间"（长回复 3-5s）降到首句合成时间（<1s）；
打断时未播放句子直接清队列，无浪费。

### P2-B 播报状态上报 + VAD 双 profile（G7）

Pi → 服务端控制帧（`player.playing` 状态翻转时发送）：

```json
{"type": "tts_state", "playing": true,  "ts_ms": 123456}
{"type": "tts_state", "playing": false, "ts_ms": 128900}
```

服务端三处消费：

1. **VAD 双 profile**：`tts_playing == true` 窗口内用抗回声门槛——

   | 参数 | 空闲 | TTS 播放窗口 | 环境变量 |
   |---|---|---|---|
   | 阈值 | 0.55 | 0.70 | `SILERO_BARGEIN_THRESHOLD` |
   | 起始帧数 | 3（96ms） | 5（160ms） | `SILERO_BARGEIN_START_FRAMES` |

   在 `StreamingSileroVad._consume_frame` 读连接级 `tts_playing` 标志切换参数。
2. **打断判定**：P0-A 的 `speech_start` 打断分支以此为准（不再猜测）。
3. **回灌评测**：`tts_state` 落入 session 事件流，补齐
   `asr_vad_tts_bargein_feedback_system.md` 要求的 TTS 时间轴（`tts.playback_start/end`），
   使 during-TTS 的 VAD 假阳性、barge-in 时延可离线统计。

### P2-C 实车噪声抑制（可选增强）

TurboPi 电机/舵机噪声是稳态宽带噪声，AEC 不处理。在 AEC 之后加 speexdsp
`SpeexPreprocess`（降噪 + AGC，同库零新依赖），链路：`mic → AEC → NS/AGC → 上行`。
先跑 §6 的实车底噪采样，若底噪 RMS 已低于 duck 门限的 1/3 则跳过本项。

---

## 5. WS 协议变更汇总

| 方向 | 帧 | 新/改 | 说明 |
|---|---|---|---|
| Pi→云 | `{"type":"tts_state","playing":bool,"ts_ms":int}` | 新 | 播放状态翻转时发 |
| 云→Pi | `{"type":"tts_begin","turn":int}` | 新 | 本轮 TTS 流开始，Pi 记 active_turn |
| 云→Pi | 二进制帧（逐句 WAV） | 改 | 一轮多帧（原一轮一帧整段 WAV） |
| 云→Pi | 空二进制帧 | 沿用 | TTS 流结束哨兵 |
| 云→Pi | `{"type":"tts_cancel","session_id":str}` | 新 | 打断确认；Pi kill 播放并丢弃至下个 tts_begin |
| 云→Pi | `result` 中 `tts_audio_base64` | 删（仅 WS 流式路径） | 避免双份带宽 |

兼容性：旧版 Pi listener 遇到未知 JSON 帧会忽略（现有 `_recv_loop` 行为），
但**二进制帧语义变化（整段→逐句）不向后兼容**，服务端与 Pi 端需同步发版；
`start_stream` 帧增加 `"proto": 2`，服务端据此选择新旧下发路径，实现灰度。

---

## 6. 硬件 AEC 验证（决定 P0-B 的 AEC 分支去留）

WonderEchoPro 模块若自带板载 AEC（TTS 走模块自身扬声器输出时，麦克风上行已消回声），
则 Pi 端软件 AEC 整条链路可删，这是实车最优解。

**已备好一键探针脚本 `audio_interact/edge/aec_probe.py`**（stdlib-only，无需 numpy/torch/网络），
在 Pi 上直接跑即可自动判读并给出 `DUCK_RMS_THRESHOLD` 建议值：

```bash
# 设备名从 aplay -l / arecord -l 查
python3 audio_interact/edge/aec_probe.py \
    --play-device    "plughw:CARD=Device,DEV=0" \
    --capture-device "plughw:CARD=Device,DEV=0"
# Phase 1 静音底噪采集时若同时开电机，脚本会顺带给出 DUCK 阈值建议
# 也可 --wav /path/to/tts_sample.wav 用真实 TTS 样本代替白噪声
```

脚本流程：① 录静音底噪 → ② 播宽带白噪声同时录麦 → ③ 比较播放窗口 RMS 与底噪 RMS。

| 脚本判读（echo_ratio = 播放RMS / 底噪RMS） | 决策（**改配置即可，无需改代码/重发版**） |
|---|---|
| `< 2x`（<6dB 残余）→ HARDWARE AEC PRESENT | config 设 `"hardware_aec": true` → 播放时 mic 直通，免软件 AEC 与启动静音 |
| `>= 2x` → ECHO PRESENT | 保持 `"hardware_aec": false`（默认）→ speexdsp 软件 AEC + 硬对齐参考 |

Phase 1 开电机跑，即可同时采集实车底噪，脚本按 `3×底噪` 给出 `DUCK_RMS_THRESHOLD` 建议
（填入 config 的 `"duck_rms_threshold"`；供 P1-A 标定与 P2-C 决策）。

**真实模式下也能读回声残余量**：listener 加 `--aec-debug`（或 config `"aec_debug": true`）后，
播放窗口内每 2s 打印一行 `[AEC-DEBUG] during-TTS residual mean=.. idle_floor=.. ratio=..x`，
`ratio<2x` 标 `GOOD`、否则 `ECHO LEAK`——这样探针（诊断）与实车（验收）给出同口径数字。

---

## 7. 测试与回归

1. **单元/CI**（`ci_tests/`）：
   - 现有 smoke + vad_asr 全绿（金标 `vad_asr_simplex_001` 不回退）；
   - 新增 `bargein_001`：回灌"TTS 播放中插话"金标 session，断言
     `speech_start(during-TTS) → tts_cancel` 下发间隔 < 200ms（服务端侧）、
     打断话语 ASR 文本完整；
   - 新增并发用例：turn task 处理期间持续送帧，`speech_start` 下发延迟 < 500ms（验 G1）。
2. **回灌评测**：用 `asr_vad_tts_bargein_feedback_system.md` 体系录制 ≥5 个实车
   barge-in session（不同插话时机：TTS 开头/中段/结尾、含误触发场景），
   人工标注后作为长期回归金标。
3. **实车验收**：按 §1 指标逐项测量，30 分钟连续对话无误 kill、无回声自打断。

## 8. 落地顺序与回滚

```
第1步  §6 硬件 AEC 验证（半天，纯测量，决定 AEC 分支）
第2步  P0-A 服务端解耦 + 并发 CI 用例（服务端独立发版，对旧 Pi 端行为不变）
第3步  P0-B + P1-A + P1-B  Pi 端播放自持 + 两级打断（proto:2 灰度，与服务端联调）
第4步  P2-A 流式 TTS 接线（服务端按 proto 分流，旧协议路径保留）
第5步  P2-B tts_state + VAD 双 profile；P2-C 视 §6 底噪数据决定
```

回滚策略：每步独立开关——P0-A 出问题回退 commit 即可（无协议变化）；
P0-B/P2-A 靠 `proto` 字段灰度，Pi 端配置回退 `"proto": 1` 即回到现行为；
VAD 双 profile 由环境变量控制，置空即禁用。
