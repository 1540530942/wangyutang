# 机器人完整语音交互回灌系统设计方案

> 目标：建设一套可落地的 **Session 级 VAD + ASR + TTS 播报 + Barge-in 打断回灌系统**，用于真实机器人语音交互数据采集、标注、回放、评测、问题归因和回归测试。

---

## 1. 背景与目标

当前机器人已经具备完整语音交互能力，包括：

- 用户语音输入；
- VAD 检测语音起止；
- ASR 识别用户语音；
- LLM / Agent 决策；
- TTS 播报回复；
- 用户在 TTS 播报过程中打断机器人；
- 多轮连续交互。

因此，回灌系统不能只保存单条音频或 ASR 文本，而必须以一次完整交互为单位，保存：

- 连续音频；
- VAD 运行时事件；
- ASR 运行时事件；
- TTS 播报事件；
- Barge-in 打断事件；
- 人工标注真值；
- 回放评测结果。

最终目标是支持以下验证：

1. **VAD 验证**  
   验证语音起点、终点、漏检、误检、过切、粘连、端点延迟等问题。

2. **ASR 验证**  
   验证多轮用户语音识别是否完整，是否吞开头、吞结尾、串轮、空识别、重复 final。

3. **TTS 抗干扰验证**  
   验证机器人播报时，TTS 回声是否误触发 VAD，是否污染 ASR。

4. **Barge-in 打断验证**  
   验证用户在 TTS 播报时插话，系统是否完整收音，并在用户说完后提交打断，避免吞字。

---

## 2. 核心原则

### 2.1 最小数据单元是 Session

不要以单条 wav 作为数据单元。

正确方式：

```text
一次完整机器人语音交互 = 一个 session 数据包
```

一个 session 可能包含：

- 多轮用户语音；
- 多段机器人 TTS；
- 一次或多次 TTS 播报中插话；
- 机器人动作、移动、观察等运行时事件。

### 2.2 所有数据必须使用统一时间轴

每个 session 的第一个音频采样点定义为：

```text
session_start = 0 ms
```

所有事件都用相对时间记录：

```text
ts_ms
start_ms
end_ms
```

不要只依赖系统时间戳。

### 2.3 回放必须模拟真实流式链路

不要直接整段音频丢给 ASR。

正确方式是：

```text
按 20ms / 40ms / 100ms chunk 流式推送音频
同步注入 TTS / VAD / ASR / Barge-in 状态事件
复现真实运行时链路
```

### 2.4 TTS 和 Barge-in 是一级验证对象

TTS 不是附属字段，而是 VAD/ASR 的核心干扰源。

必须能还原以下关键时间点：

```text
1. TTS 开始播放时间
2. 用户真实开始说话时间
3. 用户真实说完时间
4. 系统实际提交打断时间
```

核心判断：

```text
bargein_commit_ms >= user_speech_end_ms
```

同时：

```text
bargein_commit_ms - user_speech_end_ms <= 合理延迟阈值
```

建议第一版阈值：

```text
0 ms <= commit_delay_after_user_end <= 500 ms
```

---

## 3. 总体架构

```text
真实机器人交互
    ↓
Session 数据采集
    ↓
音频与事件落盘
    ↓
人工 / 半自动标注
    ↓
离线 Replay Harness 回放
    ↓
VAD / ASR / TTS / Barge-in 评测
    ↓
问题归因
    ↓
错例池 / 回归集 / 训练集
```

推荐模块拆分：

```text
audio_capture          音频采集模块
event_logger           运行时事件记录模块
session_writer         Session 数据包写入模块
label_tool             标注工具或标注格式转换模块
replay_harness         离线回放模块
vad_eval               VAD 评测模块
asr_eval               ASR 评测模块
tts_eval               TTS 播报评测模块
bargein_eval           Barge-in 打断评测模块
case_manager           错例与回归集管理模块
report_generator       评测报告生成模块
```

---

## 4. Session 数据目录结构

每次完整交互生成一个目录。

```text
session_20260709_000001/
├── manifest.json
├── audio/
│   ├── mic_raw_16k.wav
│   ├── mic_proc_16k.wav
│   ├── tts_ref_16k.wav
│   └── playback_mix_16k.wav
├── events/
│   ├── runtime_events.jsonl
│   ├── vad_runtime.jsonl
│   ├── asr_runtime.jsonl
│   ├── tts_runtime.jsonl
│   └── bargein_runtime.jsonl
├── labels/
│   ├── vad_label.json
│   ├── asr_label.json
│   ├── tts_label.json
│   └── bargein_label.json
├── replay/
│   ├── replay_config.json
│   └── replay_outputs.jsonl
└── reports/
    └── eval_result.json
```

说明：

| 文件 | 是否必须 | 说明 |
|---|---:|---|
| `manifest.json` | 是 | Session 元信息 |
| `mic_raw_16k.wav` | 是 | 原始麦克风音频 |
| `mic_proc_16k.wav` | 是 | 实际送入 VAD/ASR 的音频 |
| `tts_ref_16k.wav` | 强烈建议 | TTS 播放参考音频 |
| `playback_mix_16k.wav` | 可选 | 扬声器播放后环境混合音 |
| `runtime_events.jsonl` | 是 | 全局运行时事件 |
| `vad_runtime.jsonl` | 是 | VAD 帧级/段级输出 |
| `asr_runtime.jsonl` | 是 | ASR partial/final 输出 |
| `tts_runtime.jsonl` | 是 | TTS 播放状态 |
| `bargein_runtime.jsonl` | 是 | 打断状态机事件 |
| `vad_label.json` | 是 | 用户真实语音边界标注 |
| `asr_label.json` | 是 | 用户真实文本标注 |
| `tts_label.json` | TTS 验证必需 | TTS 播放真值 |
| `bargein_label.json` | 打断验证必需 | 用户插话与打断真值 |
| `eval_result.json` | 是 | 评测结果 |

---

## 5. 音频文件设计

### 5.1 mic_raw_16k.wav

原始麦克风音频。

用途：

- 判断真实环境噪声；
- 判断麦克风爆音；
- 判断机器人运动噪声；
- 判断 TTS 回声是否进入麦克风；
- 排查前处理是否损坏音频。

格式建议：

```text
sample_rate: 16000
channels: 1
format: PCM S16LE WAV
```

### 5.2 mic_proc_16k.wav

实际送入 VAD/ASR 的音频。

可能经过：

- AEC；
- 降噪；
- AGC；
- 重采样；
- 通道选择；
- 音量归一化。

评测主输入应该优先使用它。

原则：

```text
线上 VAD/ASR 实际吃什么音频，离线回灌就使用什么音频。
```

### 5.3 tts_ref_16k.wav

TTS 播报参考音频。

用途：

- 判断 VAD 是否被 TTS 回声误触发；
- 判断 ASR 是否混入机器人播报内容；
- 支持 AEC / Double-talk 场景归因；
- 支持 TTS 播报中用户插话测试。

### 5.4 playback_mix_16k.wav

可选。

表示扬声器播放后被环境采集到的混合音，通常是：

```text
用户语音 + TTS 回声 + 环境噪声 + 机器人运动噪声
```

如果系统只能保存 `mic_raw` 和 `mic_proc`，第一版可以不保存此文件。

---

## 6. manifest.json Schema

示例：

```json
{
  "schema_version": "1.0",
  "session_id": "session_20260709_000001",
  "created_at": "2026-07-09T10:20:30+08:00",
  "robot_id": "walle_001",
  "operator": "tester_001",
  "duration_ms": 18420,
  "audio": {
    "sample_rate": 16000,
    "channels": 1,
    "format": "wav_pcm_s16le",
    "chunk_ms": 40
  },
  "scene": {
    "environment": "indoor_room",
    "noise_level": "low",
    "distance_cm": 80,
    "robot_moving": true,
    "tts_playing": true,
    "interaction_type": "multi_turn_voice_control"
  },
  "versions": {
    "vad": "vad_0.3.1",
    "asr": "sensevoice_xxx",
    "audio_pipeline": "audio_pipeline_0.2.0",
    "tts": "tts_0.1.0",
    "bargein": "bargein_0.1.0",
    "robot_runtime": "robot_runtime_0.1.0"
  },
  "tags": [
    "multi_turn",
    "robot_moving",
    "short_command",
    "tts_overlap",
    "barge_in"
  ]
}
```

推荐 tags：

```text
single_turn
multi_turn
short_command
long_command
low_volume
far_field
noise
robot_moving
tts_playing
tts_overlap
barge_in
false_wakeup
vad_miss
vad_false_alarm
asr_prefix_drop
asr_suffix_drop
cross_turn_contamination
tts_echo
early_interrupt
late_interrupt
```

---

## 7. runtime_events.jsonl Schema

用于记录完整运行时事件。

每行一个 JSON 对象。

示例：

```json
{"ts_ms": 0, "type": "session.start", "session_id": "session_20260709_000001"}
{"ts_ms": 920, "type": "vad.speech_start", "segment_id": "seg_001", "confidence": 0.91}
{"ts_ms": 2380, "type": "vad.speech_end", "segment_id": "seg_001", "confidence": 0.87}
{"ts_ms": 2450, "type": "asr.final", "segment_id": "seg_001", "turn_id": "turn_001", "text": "往前走一点"}
{"ts_ms": 2600, "type": "robot.action_start", "action": "move_forward"}
{"ts_ms": 4820, "type": "robot.action_end", "action": "move_forward"}
{"ts_ms": 5300, "type": "tts.play_start", "tts_id": "tts_001", "text": "好的，我现在向前走一点"}
{"ts_ms": 6410, "type": "bargein.candidate_start", "tts_id": "tts_001", "vad_segment_id": "seg_002"}
{"ts_ms": 7180, "type": "vad.speech_end", "segment_id": "seg_002"}
{"ts_ms": 7350, "type": "bargein.commit", "tts_id": "tts_001", "vad_segment_id": "seg_002"}
{"ts_ms": 7360, "type": "tts.stop", "tts_id": "tts_001", "reason": "barge_in_commit"}
{"ts_ms": 7480, "type": "asr.final", "segment_id": "seg_002", "turn_id": "turn_002", "text": "别往前了停下"}
{"ts_ms": 12000, "type": "session.end"}
```

---

## 8. VAD Runtime 数据

文件：

```text
events/vad_runtime.jsonl
```

### 8.1 帧级输出

建议每 20ms / 40ms / 100ms 记录一次。

示例：

```json
{"chunk_id": 101, "start_ms": 2020, "end_ms": 2040, "is_speech": false, "score": 0.12, "rms_db": -45.3}
{"chunk_id": 102, "start_ms": 2040, "end_ms": 2060, "is_speech": true, "score": 0.71, "rms_db": -28.1}
{"chunk_id": 103, "start_ms": 2060, "end_ms": 2080, "is_speech": true, "score": 0.84, "rms_db": -25.6}
```

### 8.2 段级输出

示例：

```json
{"type": "vad.segment", "segment_id": "seg_001", "start_ms": 2040, "end_ms": 3360, "start_score": 0.71, "end_score": 0.66}
```

必须字段：

| 字段 | 说明 |
|---|---|
| `segment_id` | VAD 段 ID |
| `start_ms` | VAD 判断语音开始时间 |
| `end_ms` | VAD 判断语音结束时间 |
| `score` | VAD 置信度 |
| `source_audio` | 使用的音频源，例如 `mic_proc_16k.wav` |

---

## 9. ASR Runtime 数据

文件：

```text
events/asr_runtime.jsonl
```

必须保存：

- ASR partial；
- ASR final；
- segment_id；
- turn_id；
- ASR 使用的音频起止边界；
- final 产生时间。

示例：

```json
{"ts_ms": 2110, "type": "asr.partial", "segment_id": "seg_001", "turn_id": "turn_001", "text": "往"}
{"ts_ms": 2360, "type": "asr.partial", "segment_id": "seg_001", "turn_id": "turn_001", "text": "往前走"}
{"ts_ms": 2700, "type": "asr.partial", "segment_id": "seg_001", "turn_id": "turn_001", "text": "往前走一点"}
{"ts_ms": 2860, "type": "asr.final", "segment_id": "seg_001", "turn_id": "turn_001", "audio_start_ms": 2040, "audio_end_ms": 3360, "text": "往前走一点"}
```

注意：

```text
audio_start_ms / audio_end_ms 必须记录。
```

这样才能判断：

- ASR 错误是模型问题；
- 还是 VAD 切出来的音频已经被截断；
- 还是多轮状态污染。

---

## 10. TTS Runtime 数据

文件：

```text
events/tts_runtime.jsonl
```

示例：

```json
{"ts_ms": 5200, "type": "tts.request", "tts_id": "tts_001", "text": "好的，我现在向前走一点"}
{"ts_ms": 5320, "type": "tts.audio_ready", "tts_id": "tts_001", "audio_duration_ms": 2600}
{"ts_ms": 5400, "type": "tts.play_start", "tts_id": "tts_001", "audio_start_ms": 0}
{"ts_ms": 6200, "type": "tts.playing", "tts_id": "tts_001", "audio_pos_ms": 800}
{"ts_ms": 6500, "type": "tts.duck", "tts_id": "tts_001", "volume_ratio": 0.3}
{"ts_ms": 7350, "type": "tts.stop", "tts_id": "tts_001", "reason": "barge_in_commit"}
```

必须字段：

| 字段 | 说明 |
|---|---|
| `tts_id` | TTS 段 ID |
| `text` | TTS 文本 |
| `play_start_ms` | 播放开始时间 |
| `play_end_ms` | 播放结束时间 |
| `stop_reason` | 正常结束 / 打断停止 / 错误停止 |
| `audio_pos_ms` | 当前播放到 TTS 音频的哪个位置 |
| `volume_ratio` | 如果 ducking，记录降音量比例 |

---

## 11. Barge-in Runtime 数据

文件：

```text
events/bargein_runtime.jsonl
```

Barge-in 不要混在 VAD 里。

VAD 只表示“检测到语音”，Barge-in 表示“在 TTS 播报中对用户插话做状态机处理”。

示例：

```json
{"ts_ms": 6410, "type": "bargein.candidate_start", "tts_id": "tts_001", "vad_segment_id": "seg_002"}
{"ts_ms": 6500, "type": "bargein.user_speaking_confirmed", "tts_id": "tts_001", "vad_segment_id": "seg_002"}
{"ts_ms": 6510, "type": "bargein.audio_buffer_start", "pre_roll_ms": 600, "audio_start_ms": 5910}
{"ts_ms": 6520, "type": "bargein.tts_duck_request", "volume_ratio": 0.3}
{"ts_ms": 7200, "type": "bargein.user_speech_end_detected", "vad_segment_id": "seg_002"}
{"ts_ms": 7350, "type": "bargein.commit", "tts_id": "tts_001", "vad_segment_id": "seg_002"}
{"ts_ms": 7360, "type": "bargein.tts_stop_request", "tts_id": "tts_001"}
{"ts_ms": 7480, "type": "bargein.asr_final_ready", "text": "别往前了停下"}
```

---

## 12. Barge-in 状态机设计

推荐两阶段打断，不要一检测到声音就立刻停止 TTS。

### 12.1 状态流转

```text
TTS_PLAYING
    ↓ VAD 检测到疑似用户说话
BARGE_IN_CANDIDATE
    ↓ 连续语音确认，不是 TTS 回声 / 噪声
USER_SPEAKING_OVER_TTS
    ↓ VAD 检测到用户说完
BARGE_IN_COMMIT
    ↓
TTS_STOP + ASR_FINAL + NEXT_TURN
```

### 12.2 状态说明

#### TTS_PLAYING

机器人正在播报。

系统持续：

- 采集 mic 音频；
- 维护 pre-roll buffer；
- 监测 VAD；
- 监测 TTS 播放状态。

#### BARGE_IN_CANDIDATE

VAD 检测到疑似语音。

此时不要立刻停止 TTS。

应该：

- 开始记录候选插话；
- 将 pre-roll 音频并入本段候选用户音频；
- 继续判断是否为有效用户语音；
- 可选择降低 TTS 音量，但不要直接停止。

#### USER_SPEAKING_OVER_TTS

确认用户正在说话。

系统应该：

- 持续缓存用户语音；
- 持续向 ASR 输入音频；
- 不 reset ASR session；
- 不清空 audio buffer；
- 可保持 TTS ducking。

#### BARGE_IN_COMMIT

VAD 判断用户说完。

此时才提交打断。

系统应该：

- 触发 TTS stop；
- finalize ASR；
- 输出完整用户文本；
- 进入下一轮语义处理。

### 12.3 关键约束

错误做法：

```text
检测到一点声音 → 立刻 stop TTS → reset ASR → 清空 buffer
```

正确做法：

```text
检测到疑似用户说话 → 进入 candidate → 缓存 pre-roll + speech audio → 用户说完 → commit 打断 → stop TTS → ASR final
```

---

## 13. 防吞字机制

### 13.1 Pre-roll Buffer

系统应持续维护最近一段音频。

建议：

```text
pre_roll_ms = 500 ~ 1000 ms
```

当 VAD 检测到 speech_start 时，将 pre-roll 一起拼入 ASR 输入。

原因：

```text
VAD speech_start 往往晚于用户真实开口。
如果不保留 pre-roll，容易吞开头。
```

记录方式：

```json
{"ts_ms": 6510, "type": "bargein.audio_buffer_start", "pre_roll_ms": 600, "audio_start_ms": 5910}
```

### 13.2 Tail Buffer

用户说完后，不要立刻截断音频。

建议：

```text
tail_ms = 300 ~ 800 ms
```

原因：

```text
VAD endpoint 可能早于真实尾音结束。
如果没有 tail，容易吞结尾。
```

记录方式：

```json
{"ts_ms": 7600, "type": "asr.audio_finalize", "segment_id": "seg_002", "tail_ms": 400}
```

### 13.3 禁止打断时重置 ASR Buffer

错误流程：

```text
barge-in detected
    → stop TTS
    → reset ASR
    → clear audio buffer
    → 用户前半句丢失
```

正确流程：

```text
barge-in candidate
    → keep ASR buffer
    → append pre-roll
    → append speech audio
    → append tail audio
    → finalize ASR
    → then cleanup
```

---

## 14. 标注数据设计

### 14.1 vad_label.json

标注用户真实语音起止。

示例：

```json
{
  "session_id": "session_20260709_000001",
  "speech_segments": [
    {
      "label_segment_id": "gt_seg_001",
      "speaker": "user",
      "start_ms": 1980,
      "end_ms": 3420,
      "text": "往前走一点",
      "turn_id": "turn_001"
    },
    {
      "label_segment_id": "gt_seg_002",
      "speaker": "user",
      "start_ms": 6320,
      "end_ms": 7180,
      "text": "别往前了停下",
      "turn_id": "turn_002",
      "overlap_tts_id": "tts_001"
    }
  ]
}
```

### 14.2 asr_label.json

标注每轮用户真实文本。

示例：

```json
{
  "session_id": "session_20260709_000001",
  "turns": [
    {
      "turn_id": "turn_001",
      "start_ms": 1980,
      "end_ms": 3420,
      "raw_text": "往前走一点",
      "normalized_text": "往前走一点"
    },
    {
      "turn_id": "turn_002",
      "start_ms": 6320,
      "end_ms": 7180,
      "raw_text": "别往前了，停下",
      "normalized_text": "别往前了停下"
    }
  ]
}
```

### 14.3 tts_label.json

标注 TTS 真实播放区间。

示例：

```json
{
  "session_id": "session_20260709_000001",
  "tts_segments": [
    {
      "tts_id": "tts_001",
      "text": "好的，我现在向前走一点",
      "play_start_ms": 5400,
      "expected_play_end_ms": 8000,
      "actual_stop_ms": 7350,
      "interrupted": true,
      "stop_reason": "barge_in_commit"
    }
  ]
}
```

### 14.4 bargein_label.json

标注用户是否真的打断，以及正确打断窗口。

示例：

```json
{
  "session_id": "session_20260709_000001",
  "bargein_cases": [
    {
      "case_id": "bargein_001",
      "tts_id": "tts_001",
      "user_turn_id": "turn_002",
      "user_speech_start_ms": 6320,
      "user_speech_end_ms": 7180,
      "user_text": "别往前了停下",
      "should_interrupt": true,
      "expected_interrupt_commit_after_ms": 7180,
      "expected_interrupt_commit_before_ms": 7680
    }
  ]
}
```

---

## 15. Replay Harness 设计

### 15.1 输入

```text
session_dir/
├── manifest.json
├── audio/mic_proc_16k.wav
├── audio/tts_ref_16k.wav
├── events/tts_runtime.jsonl
├── labels/*.json
```

### 15.2 输出

```text
replay/replay_outputs.jsonl
reports/eval_result.json
```

### 15.3 回放流程

伪代码：

```python
def replay_session(session_dir):
    manifest = load_manifest(session_dir)
    mic_stream = open_audio_stream("audio/mic_proc_16k.wav", chunk_ms=manifest.audio.chunk_ms)
    tts_events = load_events("events/tts_runtime.jsonl")

    tts_state = TTSState()
    vad_engine = VADEngine()
    asr_engine = ASREngine()
    bargein_engine = BargeInEngine()

    for chunk in mic_stream:
        now_ms = chunk.start_ms

        for event in events_at(tts_events, now_ms):
            tts_state.update(event)

        vad_out = vad_engine.process(chunk)

        bargein_out = bargein_engine.process(
            now_ms=now_ms,
            audio_chunk=chunk,
            vad_out=vad_out,
            tts_state=tts_state
        )

        asr_out = asr_engine.process(
            audio_chunk=chunk,
            vad_out=vad_out,
            bargein_out=bargein_out
        )

        write_replay_output(now_ms, vad_out, asr_out, bargein_out)
```

### 15.4 回放要求

必须支持：

- 只跑 VAD；
- 只跑 ASR；
- 跑 VAD + ASR；
- 跑 VAD + ASR + TTS 状态；
- 跑完整 Barge-in 状态机；
- 对比不同 VAD / ASR / Barge-in 版本。

---

## 16. VAD 评测设计

### 16.1 对齐对象

人工标注：

```text
gt_start_ms / gt_end_ms
```

VAD 输出：

```text
pred_start_ms / pred_end_ms
```

计算：

```text
start_error_ms = pred_start_ms - gt_start_ms
end_error_ms = pred_end_ms - gt_end_ms
```

解释：

```text
start_error_ms > 0：VAD 起点晚，可能吞开头
end_error_ms < 0：VAD 终点早，可能吞结尾
```

### 16.2 VAD 指标

| 指标 | 含义 |
|---|---|
| `speech_recall` | 用户语音是否被检出 |
| `false_alarm_rate` | 无用户语音是否误触发 |
| `start_error_ms_p50/p95` | 起点偏差 |
| `end_error_ms_p50/p95` | 终点偏差 |
| `early_cut_rate` | 终点提前比例 |
| `over_segment_rate` | 一句话被切成多段 |
| `under_segment_rate` | 多句话被粘成一段 |
| `endpoint_delay_ms_p95` | 用户说完后多久判定结束 |
| `tts_false_alarm_rate` | TTS 播放时误触发率 |

### 16.3 建议第一版门禁

```text
VAD 起点延迟 P95 < 300 ms
VAD 终点提前率 < 1%
短句漏检率 < 1%
纯噪声误触发率 < 2%
TTS 播放时误触发率 < 2%
```

---

## 17. ASR 评测设计

### 17.1 对齐对象

按 turn 对齐。

```text
label turn_001: 往前走一点
asr final:      往前走一点
```

不要只按整段 session 计算 CER。

### 17.2 ASR 指标

| 指标 | 含义 |
|---|---|
| `cer` | 中文字错误率 |
| `turn_recall` | 用户轮是否都有 final |
| `empty_final_rate` | 用户说了但 final 为空 |
| `prefix_drop_rate` | 开头吞字比例 |
| `suffix_drop_rate` | 结尾吞字比例 |
| `duplicate_final_rate` | 同一轮重复 final |
| `cross_turn_contamination_rate` | 串入上一轮/下一轮内容 |
| `vad_caused_asr_error_rate` | 由 VAD 截断导致的 ASR 错误比例 |

### 17.3 吞字归因方法

每条数据跑两种 ASR：

#### 方式一：使用 VAD 输出边界切音频

```text
mic_proc.wav → VAD pred segment → ASR
```

这是线上真实链路。

#### 方式二：使用人工标注边界切音频

```text
mic_proc.wav → GT speech segment → ASR
```

归因逻辑：

```text
如果 VAD segment 下吞字，但 GT segment 下正确：
    主要是 VAD 边界问题。

如果 VAD segment 下吞字，GT segment 下仍吞字：
    主要是 ASR / 音频质量 / 前处理问题。

如果 GT segment 正确但线上 replay 错：
    可能是流式状态、buffer reset、多轮状态污染问题。
```

---

## 18. TTS 评测设计

### 18.1 TTS 指标

| 指标 | 含义 |
|---|---|
| `tts_play_start_delay_ms` | TTS 请求到开始播放延迟 |
| `tts_play_complete_rate` | 正常播报完成比例 |
| `tts_interrupted_rate` | 被打断比例 |
| `tts_unexpected_stop_rate` | 非预期停止比例 |
| `tts_echo_vad_trigger_rate` | TTS 回声导致 VAD 误触发比例 |
| `tts_leak_to_asr_rate` | TTS 文本混入 ASR 的比例 |

### 18.2 TTS 抗干扰验证

必须包含一组数据：

```text
TTS 播放中，用户不说话。
```

期望：

```text
不产生用户 VAD segment
不产生 ASR final
不触发 barge-in
```

如果触发，说明：

- AEC 不足；
- VAD 被 TTS 回声误触发；
- Barge-in 状态机缺少 TTS 回声过滤。

---

## 19. Barge-in 评测设计

### 19.1 核心指标

| 指标 | 含义 |
|---|---|
| `bargein_detect_rate` | 用户插话是否被检测到 |
| `false_bargein_rate` | 用户没说话却打断 |
| `early_interrupt_rate` | 用户没说完就打断 |
| `late_interrupt_rate` | 用户说完很久才打断 |
| `commit_after_user_end_rate` | 是否在用户说完后提交打断 |
| `commit_delay_after_user_end_ms_p95` | 用户说完到提交打断的延迟 |
| `user_audio_complete_rate` | 插话音频是否完整进入 ASR |
| `bargein_asr_cer` | 插话内容 ASR 字错误率 |
| `tts_leak_to_asr_rate` | ASR 是否混入 TTS 内容 |

### 19.2 正确打断判定

对于每个 barge-in case：

```text
user_speech_end_ms = 人工标注的用户真实说完时间
commit_ms = 系统实际 bargein.commit 时间
```

正确：

```text
expected_interrupt_commit_after_ms <= commit_ms <= expected_interrupt_commit_before_ms
```

错误类型：

```text
commit_ms < user_speech_end_ms:
    early_interrupt，用户没说完就打断。

commit_ms > expected_interrupt_commit_before_ms:
    late_interrupt，打断太慢。

没有 commit:
    missed_bargein，漏打断。

用户没有说话但 commit:
    false_bargein，误打断。
```

---

## 20. 典型测试集设计

### 20.1 基础 VAD + ASR 集

覆盖：

```text
单轮短命令
单轮长命令
多轮连续命令
低音量
远距离
机器人运动噪声
用户停顿后继续说
```

示例：

```text
往前走
停下
左转一点
看一下前面有什么
往前走一点，然后停在桌子前面
先停一下，然后看一下左边
```

### 20.2 TTS 无用户说话集

目的：验证 TTS 不误触发。

示例：

```text
机器人：好的，我现在准备向前移动，请注意安全。
用户：不说话。
期望：不触发 VAD 用户段，不触发 ASR final，不触发 barge-in。
```

### 20.3 TTS 中短句插话集

目的：验证短命令插话不吞字。

示例：

```text
机器人：好的，我现在准备向前移动，请注意安全。
用户：停下。
期望：完整识别“停下”，用户说完后提交打断。
```

### 20.4 TTS 中长句插话集

目的：验证长句不会中途截断。

示例：

```text
机器人：好的，我现在准备向前移动，请注意安全。
用户：不是往前走，是往左边靠近桌子一点。
期望：完整识别整句话，不提前 endpoint。
```

### 20.5 TTS 中低音量插话集

目的：验证低音量用户插话。

示例：

```text
机器人播报中。
用户小声说：停一下。
期望：能触发有效插话，不把 TTS 当成用户。
```

### 20.6 TTS 结束后立刻说话集

目的：验证 TTS 到用户轮切换。

示例：

```text
机器人刚播报结束后 100ms 内，用户说：再往左一点。
期望：不吞开头，不受上一轮 TTS 状态污染。
```

### 20.7 Bug 回归集

每次发现问题，都沉淀成 case。

示例命名：

```text
case_001_vad_start_late_prefix_drop
case_002_vad_endpoint_early_suffix_drop
case_003_multi_turn_cross_contamination
case_004_tts_echo_false_vad
case_005_short_command_empty_final
case_006_bargein_early_interrupt
case_007_bargein_audio_prefix_drop
case_008_tts_leak_to_asr
```

---

## 21. eval_result.json 示例

```json
{
  "session_id": "session_20260709_000001",
  "versions": {
    "vad": "vad_0.3.1",
    "asr": "sensevoice_xxx",
    "bargein": "bargein_0.1.0"
  },
  "vad_eval": {
    "speech_recall": 1.0,
    "false_alarm_rate": 0.0,
    "start_error_ms_p95": 180,
    "end_error_ms_p95": 220,
    "early_cut": false,
    "tts_false_alarm": false
  },
  "asr_eval": {
    "cer": 0.02,
    "turn_recall": 1.0,
    "prefix_drop": false,
    "suffix_drop": false,
    "empty_final": false,
    "cross_turn_contamination": false
  },
  "tts_eval": {
    "tts_id": "tts_001",
    "play_start_ms": 5400,
    "expected_play_end_ms": 8000,
    "actual_stop_ms": 7350,
    "interrupted": true,
    "tts_leak_to_asr": false
  },
  "bargein_eval": {
    "case_id": "bargein_001",
    "should_interrupt": true,
    "detected": true,
    "commit_ms": 7350,
    "user_speech_end_ms": 7180,
    "commit_delay_after_user_end_ms": 170,
    "early_interrupt": false,
    "late_interrupt": false,
    "user_audio_complete": true,
    "bargein_asr_cer": 0.0
  },
  "diagnosis": {
    "primary_issue": "none",
    "suspected_module": null,
    "notes": "case passed"
  }
}
```

---

## 22. 实现任务拆分

### P0：Session 数据采集闭环

目标：每次真实机器人交互后，能保存完整 session 数据包。

任务：

- [ ] 生成全局 `session_id`；
- [ ] 生成 `turn_id`、`segment_id`、`tts_id`；
- [ ] 保存 `mic_raw_16k.wav`；
- [ ] 保存 `mic_proc_16k.wav`；
- [ ] 保存 `tts_ref_16k.wav`；
- [ ] 记录 `runtime_events.jsonl`；
- [ ] 记录 `vad_runtime.jsonl`；
- [ ] 记录 `asr_runtime.jsonl`；
- [ ] 记录 `tts_runtime.jsonl`；
- [ ] 记录 `bargein_runtime.jsonl`；
- [ ] 生成 `manifest.json`。

验收标准：

```text
一次完整交互结束后，session 目录结构完整；
所有事件时间戳都能对齐到音频时间轴；
可以根据 session_id 找到所有音频和事件文件。
```

---

### P1：标注数据格式落地

目标：支持人工或半自动标注。

任务：

- [ ] 定义 `vad_label.json`；
- [ ] 定义 `asr_label.json`；
- [ ] 定义 `tts_label.json`；
- [ ] 定义 `bargein_label.json`；
- [ ] 支持从标注工具导出为上述格式；
- [ ] 提供 label schema 校验脚本。

验收标准：

```text
给定一个 session，能补齐四类 label；
label 时间戳与音频时间轴一致；
label schema 校验通过。
```

---

### P2：Replay Harness

目标：离线复现真实流式链路。

任务：

- [ ] 读取 session 目录；
- [ ] 按 chunk 流式读取 `mic_proc_16k.wav`；
- [ ] 按时间注入 TTS 事件；
- [ ] 调用 VAD；
- [ ] 调用 ASR；
- [ ] 调用 Barge-in 状态机；
- [ ] 输出 `replay_outputs.jsonl`。

验收标准：

```text
同一个 session 可以离线重复回放；
不同 VAD / ASR / Barge-in 版本可配置切换；
回放输出包含 VAD / ASR / TTS / Barge-in 全部事件。
```

---

### P3：VAD + ASR 评测

目标：输出 VAD 和 ASR 指标。

任务：

- [ ] 实现 VAD segment 与 GT segment 对齐；
- [ ] 计算 start_error / end_error；
- [ ] 计算漏检、误检、过切、粘连；
- [ ] 实现 turn 级 ASR 对齐；
- [ ] 计算 CER；
- [ ] 计算 prefix_drop / suffix_drop；
- [ ] 计算 empty_final / duplicate_final；
- [ ] 判断 cross_turn_contamination；
- [ ] 输出 eval_result.json。

验收标准：

```text
每个 session 可以生成 VAD/ASR 评测结果；
能区分 VAD 截断导致的 ASR 错误和 ASR 自身错误。
```

---

### P4：TTS + Barge-in 评测

目标：验证 TTS 播报和打断链路。

任务：

- [ ] 实现 TTS 播放区间评测；
- [ ] 实现 TTS 回声误触发判断；
- [ ] 实现 Barge-in case 对齐；
- [ ] 判断 missed_bargein；
- [ ] 判断 false_bargein；
- [ ] 判断 early_interrupt；
- [ ] 判断 late_interrupt；
- [ ] 判断 user_audio_complete；
- [ ] 判断 tts_leak_to_asr；
- [ ] 输出 bargein_eval。

验收标准：

```text
能判断用户是否在 TTS 播报中插话；
能判断系统是否在用户说完后提交打断；
能判断打断是否导致用户语音吞字。
```

---

### P5：错例池与回归集

目标：将问题样本沉淀为长期回归 case。

任务：

- [ ] 定义 case 命名规范；
- [ ] 支持按 tags 筛选 session；
- [ ] 支持将失败 session 加入 regression_cases；
- [ ] 支持批量跑 regression_cases；
- [ ] 输出版本对比报告。

验收标准：

```text
每次修复 VAD / ASR / Barge-in 问题后，都能跑历史回归集；
新版本不能重新引入旧问题。
```

---

## 23. 第一版最小可落地范围

第一版不要做大平台，先跑通最小闭环。

### 必须实现

```text
1. session_id / turn_id / segment_id / tts_id 生成
2. mic_raw_16k.wav 保存
3. mic_proc_16k.wav 保存
4. tts_ref_16k.wav 保存
5. vad_runtime.jsonl 保存
6. asr_runtime.jsonl 保存
7. tts_runtime.jsonl 保存
8. bargein_runtime.jsonl 保存
9. vad_label.json / asr_label.json / tts_label.json / bargein_label.json 格式定义
10. replay_eval.py
11. eval_result.json 输出
```

### 可以暂缓

```text
1. Web 标注平台
2. 大规模数据管理平台
3. 自动训练集清洗
4. 复杂可视化看板
5. CI 全量门禁
```

### 第一版验收命令示例

```bash
python replay_eval.py \
  --session_dir data/sessions/session_20260709_000001 \
  --vad_version vad_0.3.1 \
  --asr_version sensevoice_xxx \
  --bargein_version bargein_0.1.0 \
  --output reports/eval_result.json
```

---

## 24. Agent 实施建议

如果交给 Coding Agent 实现，建议按以下顺序执行。

### Step 1：先实现数据结构

目标：

```text
创建 session 目录结构；
定义所有 json/jsonl schema；
提供 schema 校验脚本。
```

输出文件：

```text
schemas/manifest.schema.json
schemas/vad_label.schema.json
schemas/asr_label.schema.json
schemas/tts_label.schema.json
schemas/bargein_label.schema.json
tools/validate_session.py
```

### Step 2：实现事件记录器

目标：

```text
提供统一 EventLogger；
所有运行时模块都通过 EventLogger 写 jsonl。
```

建议接口：

```python
logger.emit(ts_ms=920, type="vad.speech_start", segment_id="seg_001", confidence=0.91)
```

输出文件：

```text
runtime/event_logger.py
runtime/id_generator.py
```

### Step 3：实现 SessionWriter

目标：

```text
统一创建 session 目录；
统一写 manifest；
统一管理音频文件路径；
统一 flush 事件文件。
```

输出文件：

```text
runtime/session_writer.py
```

### Step 4：接入 VAD / ASR / TTS / Barge-in 运行时

目标：

```text
在现有机器人运行链路中插入最小侵入式日志记录。
```

要求：

```text
不能影响实时交互；
日志失败不能阻断主链路；
必须保证时间戳统一。
```

### Step 5：实现 Replay Harness

目标：

```text
根据 session 数据离线回放；
输出 replay_outputs.jsonl。
```

输出文件：

```text
replay/replay_session.py
```

### Step 6：实现评测模块

输出文件：

```text
eval/vad_eval.py
eval/asr_eval.py
eval/tts_eval.py
eval/bargein_eval.py
eval/report_generator.py
```

### Step 7：实现回归集管理

输出文件：

```text
cases/case_manager.py
tools/run_regression.py
```

---

## 25. 核心验收标准

最终系统必须能回答以下问题：

### VAD

```text
用户真实什么时候开始说？
VAD 什么时候认为用户开始说？
VAD 起点是否晚？
用户真实什么时候说完？
VAD 什么时候认为用户说完？
VAD 是否提前截断？
TTS 播报时是否误触发 VAD？
```

### ASR

```text
每个用户轮是否都有 final？
是否吞开头？
是否吞结尾？
是否空识别？
是否串轮？
ASR 错误是 VAD 截断导致，还是模型自身错误？
```

### TTS

```text
机器人什么时候开始播报？
播报期间用户有没有说话？
TTS 是否被误停止？
TTS 回声是否污染 VAD/ASR？
```

### Barge-in

```text
用户是否真的在 TTS 播报时插话？
系统是否检测到插话？
系统是否在用户说完后提交打断？
是否过早打断？
是否打断太慢？
用户插话音频是否完整进入 ASR？
```

---

## 26. 最终结论

本系统的核心定义是：

```text
面向机器人完整语音交互的 Session 级 VAD + ASR + TTS + Barge-in 回灌与评测系统。
```

它不是普通 ASR 数据集，也不是单句音频评测工具。

它必须具备：

```text
1. 完整音频可保存
2. 运行时事件可追溯
3. 用户语音边界可标注
4. ASR 文本可对齐
5. TTS 播报状态可复现
6. Barge-in 打断时机可验证
7. 离线流式回放可重复
8. 错误样本可沉淀为回归集
```

最关键的四个时间点：

```text
tts_play_start_ms
user_speech_start_ms
user_speech_end_ms
bargein_commit_ms
```

最关键的判定：

```text
bargein_commit_ms >= user_speech_end_ms
```

同时：

```text
bargein_commit_ms - user_speech_end_ms <= 500 ms
```

只要这套数据闭环建立起来，后续无论优化 VAD、ASR、AEC、TTS 还是 Barge-in 状态机，都可以做到：

```text
可复现
可评测
可归因
可回归
```
