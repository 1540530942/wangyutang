# 机器人语音交互回灌数据落盘方案

> 本方案面向机器人真实语音交互场景,定义回灌验证所需的数据落盘体系。
> 只管"落盘",不展开回放执行与评测算法(见 `asr_vad_tts_bargein_feedback_system.md`)。
>
> 修订说明(v1.1):
> 1. 统一全文黄金示例时间线,修正打断示例超窗矛盾(commit 6900ms,窗口 6500~7000ms);
> 2. 新增 `capture_point` 字段,如实记录采集点(浏览器采集的 "raw" 已经过浏览器 AEC/降噪/AGC);
> 3. 明确"必须字段"分阶段:当前可采 / 需客户端播放遥测 / 需打断状态机上线;
> 4. 存储策略从简:当前数据量小,全量保留不自动清理,以可追踪(trace)为第一原则。

---

## 1. 目标定位

落盘数据需要支撑后续对以下问题的复现与分析:

1. VAD 是否准确检测用户语音起止;
2. ASR 是否完整识别多轮用户语音;
3. 是否存在吞开头、吞结尾、空识别、串轮等问题;
4. TTS 播报期间是否对 VAD/ASR 产生干扰;
5. 用户在 TTS 播报中插话时,音频和事件是否完整记录;
6. Barge-in 打断相关的关键时间点是否可追溯。

落盘数据的最小单元是一次完整语音交互 session,而不是单条用户音频或单条 ASR 文本。

---

## 2. 核心设计原则

### 2.1 以 Session 为最小落盘单元

一次完整机器人语音交互生成一个 session 数据包,内含多轮用户语音、多段 TTS 播报、
机器人动作事件、TTS 播报中的用户插话事件等。

```text
原始数据:保存连续 session 音频
逻辑数据:用时间戳描述多个用户轮、TTS 轮、VAD segment、ASR final 和打断事件
派生数据:后续可从连续音频中导出单轮小片段
```

### 2.2 长音频作为主数据,时间戳作为逻辑切分

黄金示例时间线(全文各节示例统一使用这条时间线):

```text
0ms       session start
1200ms    用户开始说:往前走一点
2600ms    用户说完
3000ms    TTS 请求合成
3200ms    TTS 开始播报(时长 6000ms,自然播完应到 9200ms)
5100ms    用户插话:停下
6500ms    用户说完
6900ms    系统提交打断(在 6500+500ms 窗口内)
6910ms    TTS 停止
10000ms   session end
```

`mic_proc_16k.wav` 是 0ms 到 10000ms 的连续音频,不同用户轮通过 `start_ms` / `end_ms` 描述。

### 2.3 所有数据使用统一时间轴

```text
session_start = 0ms
第一个音频采样点 = 0ms
所有事件均使用相对 session_start 的 ts_ms / start_ms / end_ms
```

### 2.4 运行时事件和音频必须同时落盘

音频用于复现真实声音现场,事件用于还原链路状态,标注用于后续评测和归因。

### 2.5 分阶段落盘范围(诚实标注可采集性)

落盘 TTS 播放时间线与打断事件**依赖实时链路的最小改造**(客户端播放遥测上报),
本方案按可采集性分三个阶段,完整性校验只按当前阶段执行:

| 阶段 | 数据 | 前置条件 |
|---|---|---|
| A(现在即可) | mic 音频、VAD 事件、ASR final、TTS request/audio_ready、robot.command | 无 |
| B | tts.play_start / play_end / audio_pos_ms | 浏览器/树莓派播放端上报播放事件 |
| C | bargein_runtime 全部事件 | Barge-in 状态机上线 |

`asr.partial` 仅在使用流式 ASR 时必须;当前 ASR 为整段调用,可缺省。

---

## 3. Session 目录结构

```text
session_20260709_000001/
├── manifest.json
├── audio/
│   ├── mic_raw_16k.wav
│   ├── mic_proc_16k.wav
│   └── tts_clips/
│       ├── tts_001.wav
│       └── tts_002.wav
├── events/
│   ├── runtime_events.jsonl
│   ├── vad_runtime.jsonl
│   ├── asr_runtime.jsonl
│   ├── tts_runtime.jsonl
│   └── bargein_runtime.jsonl
├── labels/
│   ├── turns.json
│   ├── vad_label.json
│   ├── asr_label.json
│   ├── tts_label.json
│   └── bargein_label.json
└── derived_clips/
    ├── turn_001_user.wav
    └── clips_index.json
```

`derived_clips/` 不是必需原始数据,必须能够通过 metadata 回溯到原始 session。

---

## 4. 音频落盘设计

### 4.1 mic_raw_16k.wav 与采集点(capture_point)

`mic_raw_16k.wav` 表示"服务端收到的最原始音频"。注意:**它有多原始,取决于采集点**:

```text
浏览器采集(web模式):getUserMedia 开启了 echoCancellation / noiseSuppression /
  autoGainControl,浏览器在交出音频前已做 AEC/降噪/AGC。
  此时服务端的 "raw" 实为浏览器处理后音频 → capture_point = "browser_processed"
树莓派采集(WonderEchoPro):ALSA 直采,未经处理
  → capture_point = "pi_alsa_raw"
```

manifest 必须记录 `capture_point`,后续分析回声/噪声问题时据此判断证据强度。
浏览器的 echoCancellation 是 web模式当前唯一的回声防线,**不要为了拿 raw 关掉它**。

### 4.2 mic_proc_16k.wav

实际送入 VAD/ASR 的处理后音频,是后续 VAD+ASR 验证的主输入。

原则:

```text
线上 VAD/ASR 实际吃什么,落盘就保存什么。
```

当前服务端没有独立前处理链,`mic_proc` 与 `mic_raw` 逐字节相同;
此时允许只存一份,并在 manifest 标 `"proc_same_as_raw": true`,避免双份浪费。
待引入服务端 AEC/降噪后再分开存储。

对于多轮长 session,默认一个 session 保存一条连续 `mic_proc_16k.wav`。
如果 session 过长,可物理切成多个 part,逻辑上仍视为一条连续流:

```json
{
  "stream_name": "mic_proc",
  "parts": [
    {"file": "audio/mic_proc_16k_part_000.wav", "start_ms": 0, "end_ms": 60000},
    {"file": "audio/mic_proc_16k_part_001.wav", "start_ms": 60000, "end_ms": 120000}
  ]
}
```

### 4.3 TTS 参考音频

```text
mic_raw_16k.wav   = 麦克风实际听到什么
mic_proc_16k.wav  = VAD/ASR 实际吃到什么
tts_clips/        = 机器人自己播放了什么
```

第一版采用 **多个 TTS clip + 时间戳事件**(服务端合成时顺手落盘,零额外成本);
后续需要精确回声分析时,再按时间戳派生连续静音填充的 `tts_ref_16k.wav`。

---

## 5. 事件落盘设计

事件文件统一 JSONL,每行一个事件,便于实时追加与按序解析。
**落盘不得阻塞实时链路**:帧级事件应缓冲批量 flush(按段或每秒),写失败只告警。

### 5.1 runtime_events.jsonl(黄金示例)

```json
{"ts_ms": 0, "type": "session.start", "session_id": "session_20260709_000001"}
{"ts_ms": 1290, "type": "vad.speech_start", "segment_id": "seg_001"}
{"ts_ms": 2680, "type": "vad.speech_end", "segment_id": "seg_001"}
{"ts_ms": 2750, "type": "asr.final", "segment_id": "seg_001", "turn_id": "turn_001", "text": "往前走一点"}
{"ts_ms": 3000, "type": "tts.request", "tts_id": "tts_001", "text": "好的，我现在向前走一点"}
{"ts_ms": 3200, "type": "tts.play_start", "tts_id": "tts_001"}
{"ts_ms": 5180, "type": "bargein.candidate_start", "tts_id": "tts_001", "segment_id": "seg_002"}
{"ts_ms": 6580, "type": "bargein.user_speech_end_detected", "segment_id": "seg_002"}
{"ts_ms": 6900, "type": "bargein.commit", "tts_id": "tts_001", "segment_id": "seg_002"}
{"ts_ms": 6910, "type": "tts.stop", "tts_id": "tts_001", "reason": "barge_in_commit"}
{"ts_ms": 7050, "type": "asr.final", "segment_id": "seg_002", "turn_id": "turn_003", "text": "停下"}
{"ts_ms": 10000, "type": "session.end"}
```

### 5.2 vad_runtime.jsonl

帧级(围绕用户 1200ms 真实开口):

```json
{"chunk_id": 39, "start_ms": 1248, "end_ms": 1280, "is_speech": false, "score": 0.12, "rms_db": -45.3}
{"chunk_id": 40, "start_ms": 1280, "end_ms": 1312, "is_speech": true, "score": 0.71, "rms_db": -28.1}
```

段级:

```json
{"type": "vad.segment", "segment_id": "seg_001", "start_ms": 1290, "end_ms": 2680, "source_audio": "audio/mic_proc_16k.wav"}
```

必须记录:`segment_id`、`start_ms`、`end_ms`、`score`、`source_audio`。

注意:定长上传(WonderEchoPro 整段)没有真实 VAD,段事件必须标
`"source": "fixed_window"`,评测时不得计入 VAD 预测。

### 5.3 asr_runtime.jsonl

```json
{"ts_ms": 2750, "type": "asr.final", "segment_id": "seg_001", "turn_id": "turn_001", "audio_start_ms": 1290, "audio_end_ms": 2680, "text": "往前走一点"}
```

必须记录:`turn_id`、`segment_id`、`audio_start_ms`、`audio_end_ms`、`text`、`ts_ms`。
`audio_start_ms / audio_end_ms` 用于判断 ASR 实际接收的是哪一段音频,是吞字归因的关键。
`asr.partial` 仅流式 ASR 必须(阶段见 §2.5)。

### 5.4 tts_runtime.jsonl(阶段 A + B)

```json
{"ts_ms": 3000, "type": "tts.request", "tts_id": "tts_001", "text": "好的，我现在向前走一点"}
{"ts_ms": 3120, "type": "tts.audio_ready", "tts_id": "tts_001", "file": "audio/tts_clips/tts_001.wav", "audio_duration_ms": 6000}
{"ts_ms": 3200, "type": "tts.play_start", "tts_id": "tts_001", "audio_pos_ms": 0}
{"ts_ms": 5100, "type": "tts.playing", "tts_id": "tts_001", "audio_pos_ms": 1900}
{"ts_ms": 6910, "type": "tts.stop", "tts_id": "tts_001", "reason": "barge_in_commit"}
```

阶段 A 必须:`tts_id`、`text`、`audio_file`、`audio_duration_ms`(服务端合成即知)。
阶段 B 必须:`play_start_ms`、`play_end_ms / stop_ms`、`stop_reason`、`audio_pos_ms`
(依赖播放端遥测:浏览器 `<audio>` 事件 / 树莓派播放器回报)。

### 5.5 bargein_runtime.jsonl(阶段 C)

```json
{"ts_ms": 5180, "type": "bargein.candidate_start", "tts_id": "tts_001", "vad_segment_id": "seg_002"}
{"ts_ms": 5300, "type": "bargein.user_speaking_confirmed", "tts_id": "tts_001", "vad_segment_id": "seg_002"}
{"ts_ms": 5310, "type": "bargein.audio_buffer_start", "pre_roll_ms": 600, "audio_start_ms": 4580}
{"ts_ms": 6580, "type": "bargein.user_speech_end_detected", "vad_segment_id": "seg_002"}
{"ts_ms": 6900, "type": "bargein.commit", "tts_id": "tts_001", "vad_segment_id": "seg_002"}
```

必须记录:`tts_id`、`vad_segment_id`、`candidate_start_ms`、`user_speaking_confirmed_ms`、
`pre_roll_ms`、`audio_start_ms`、`user_speech_end_detected_ms`、`commit_ms`。

---

## 6. 标注数据落盘设计

### 6.1 turns.json

```json
{
  "session_id": "session_20260709_000001",
  "turns": [
    {"turn_id": "turn_001", "role": "user", "start_ms": 1200, "end_ms": 2600,
     "text": "往前走一点", "audio_source": "audio/mic_proc_16k.wav"},
    {"turn_id": "turn_002", "role": "assistant_tts", "tts_id": "tts_001",
     "start_ms": 3200, "end_ms": 6910, "text": "好的，我现在向前走一点",
     "audio_source": "audio/tts_clips/tts_001.wav"},
    {"turn_id": "turn_003", "role": "user", "start_ms": 5100, "end_ms": 6500,
     "text": "停下", "audio_source": "audio/mic_proc_16k.wav",
     "barge_in": true, "overlap_tts_id": "tts_001"}
  ]
}
```

### 6.2 vad_label.json

```json
{
  "session_id": "session_20260709_000001",
  "speech_segments": [
    {"label_segment_id": "gt_seg_001", "speaker": "user", "start_ms": 1200,
     "end_ms": 2600, "text": "往前走一点", "turn_id": "turn_001"},
    {"label_segment_id": "gt_seg_002", "speaker": "user", "start_ms": 5100,
     "end_ms": 6500, "text": "停下", "turn_id": "turn_003", "overlap_tts_id": "tts_001"}
  ]
}
```

### 6.3 asr_label.json

```json
{
  "session_id": "session_20260709_000001",
  "turns": [
    {"turn_id": "turn_001", "start_ms": 1200, "end_ms": 2600,
     "raw_text": "往前走一点", "normalized_text": "往前走一点"},
    {"turn_id": "turn_003", "start_ms": 5100, "end_ms": 6500,
     "raw_text": "停下", "normalized_text": "停下"}
  ]
}
```

### 6.4 tts_label.json

```json
{
  "session_id": "session_20260709_000001",
  "tts_segments": [
    {"tts_id": "tts_001", "text": "好的，我现在向前走一点",
     "play_start_ms": 3200, "expected_play_end_ms": 9200,
     "actual_stop_ms": 6910, "interrupted": true, "stop_reason": "barge_in_commit"}
  ]
}
```

### 6.5 bargein_label.json

```json
{
  "session_id": "session_20260709_000001",
  "bargein_cases": [
    {"case_id": "bargein_001", "tts_id": "tts_001", "user_turn_id": "turn_003",
     "user_speech_start_ms": 5100, "user_speech_end_ms": 6500, "user_text": "停下",
     "should_interrupt": true,
     "expected_interrupt_commit_after_ms": 6500,
     "expected_interrupt_commit_before_ms": 7000}
  ]
}
```

判定:runtime `bargein.commit = 6900ms`,落在 [6500, 7000] 窗口内 → 打断合格。
(修订注:原稿 commit 示例为 7350ms,超出自身定义的窗口,已修正为 6900ms。)

---

## 7. manifest.json 落盘设计

```json
{
  "schema_version": "1.1",
  "session_id": "session_20260709_000001",
  "created_at": "2026-07-09T10:20:30+08:00",
  "robot_id": "walle_001",
  "duration_ms": 10000,
  "capture_point": "browser_processed",
  "proc_same_as_raw": true,
  "audio": {
    "sample_rate": 16000,
    "channels": 1,
    "format": "wav_pcm_s16le",
    "chunk_ms": 32
  },
  "scene": {
    "environment": "indoor_room",
    "noise_level": "low",
    "robot_moving": true,
    "tts_playing": true,
    "interaction_type": "multi_turn_voice_control"
  },
  "versions": {
    "vad": "silero_streaming",
    "asr": "sensevoice_xxx",
    "audio_pipeline": "audio_interact"
  },
  "tags": ["multi_turn", "tts_overlap", "barge_in", "short_command"]
}
```

建议 tags:

```text
single_turn multi_turn short_command long_command low_volume far_field noise
robot_moving tts_playing tts_overlap barge_in vad_miss vad_false_alarm
asr_prefix_drop asr_suffix_drop cross_turn_contamination tts_echo
early_interrupt late_interrupt
```

---

## 8. 派生片段落盘设计

派生片段是从连续 session 音频导出的辅助数据,用于单轮训练/人工复核/问题归档。

`clips_index.json`:

```json
{
  "clips": [
    {"clip_id": "turn_001_user", "source_session_id": "session_20260709_000001",
     "source_audio": "audio/mic_proc_16k.wav", "start_ms": 1200, "end_ms": 2600,
     "pre_roll_ms": 300, "tail_ms": 300, "text": "往前走一点", "type": "user_turn"},
    {"clip_id": "turn_003_bargein", "source_session_id": "session_20260709_000001",
     "source_audio": "audio/mic_proc_16k.wav", "start_ms": 5100, "end_ms": 6500,
     "pre_roll_ms": 600, "tail_ms": 400, "text": "停下",
     "type": "barge_in_user_turn", "overlap_tts_id": "tts_001"}
  ]
}
```

要求:必须保留 `source_session_id`、`source_audio`、`start_ms / end_ms`,可随时删除再生。

---

## 9. 落盘完整性要求

按 §2.5 阶段执行。阶段 A 的完整性要求:

```text
1. manifest.json 存在(含 capture_point);
2. mic_proc_16k.wav 存在(proc_same_as_raw 时允许与 raw 同一文件);
3. vad_runtime.jsonl / asr_runtime.jsonl / runtime_events.jsonl 存在;
4. 所有事件 ts_ms 均在 session duration 范围内;
5. turn_id / segment_id / tts_id 不重复;
6. ASR final 能关联到 segment_id 和 turn_id;
7. label 中引用的 audio_source 必须存在;
8. label 中的 start_ms / end_ms 必须在音频范围内。
```

阶段 B 追加:TTS stop 能关联 tts_id;阶段 C 追加:Barge-in commit 能关联 tts_id 与 vad_segment_id。

校验:

```bash
python tools/validate_session.py --session_dir data/sessions/<day>/<session_id>
```

---

## 10. 存储与追踪(第一版从简)

当前数据总量不大,**不做分层清理、不做自动过期、不做压缩迁移**,全量保留。
第一原则是**可追踪(trace)**:任何一次交互,给定 session_id(或日期)必须能一条命令
拉出全链路时间线,支撑问题定位、复现与复盘:

```bash
# 列出最近 session(一行摘要:时间/时长/句数/文本)
python tools/trace_session.py --data-root <root> --list

# 给定 session_id 输出全链路时间线:
#   音频文件 → VAD 起止 → ASR 文本/唤醒状态 → 指令(robot.command) → TTS
python tools/trace_session.py --data-root <root> --session <session_id>
```

trace 工具同时覆盖标准包(`sessions/`)与 legacy 目录(`recordings/`),
保证历史数据同样可复盘。数据量增长到需要清理时,再引入保留策略(届时另行设计)。

---

## 10.1 金标数据与仿真输入

金标数据同样遵循"一次完整 session 为最小单元"的原则,目录结构为:

```text
audio_interact/tests/golden/sessions/<session_id>/
├── manifest.json
├── audio/
│   └── mic_proc_16k.wav
└── labels/
    └── turns.json
```

`turns.json` 只描述 session 内每一轮用户语料的期望结果,不把每句用户语音拆成独立主音频。
CI、回放和仿真可以按 turn 展开断言,但归档和复盘仍以完整 session 为单位。

`/audio_interact/api/golden` 会优先读取上述 `sessions/*/manifest.json`,再转换成
`/dashboard/vad_asr` 现有前端可消费的兼容 JSON。`/api/golden/audio/{id}` 同时接受
`case_id` 与 `session_id`,并返回 manifest 中声明的 session 音频。

旧的扁平结构:

```text
tests/golden/<case_id>.json
tests/golden/audio/<case_id>.wav
```

仅作为服务端兼容兜底保留读取逻辑,新增金标必须使用 `tests/golden/sessions/<session_id>/`。
完整规范见 `../docs/audio-interact-golden-sessions.md`。

---

## 11. 第一版落盘实现范围

```text
1. session_id 生成;
2. session 目录创建;
3. manifest.json 写入(含 capture_point / proc_same_as_raw);
4. mic_raw / mic_proc 保存(相同时单份);
5. tts clip 保存;
6. 五个 *_runtime.jsonl 写入(阶段 A 字段);
7. labels 五个文件格式定义(turns.json + 四类 label);
8. validate_session.py 校验脚本;
9. trace_session.py 全链路追踪脚本。
```

暂不展开:离线回放执行逻辑、仿真调度、评测算法、CI 门禁、训练集构造、可视化看板、
存储清理策略。

---

## 12. 最终落盘结论

```text
原始落盘保留真实长时序,逻辑切分依赖时间戳,派生小片段只作为辅助数据;
采集点如实记录,必须字段按阶段执行,任何 session 一条命令可全链路复盘。
```
