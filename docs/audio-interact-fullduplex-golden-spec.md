# Audio Interact Full-Duplex Golden Session Spec

This document defines the minimum data requirements for validating WonderEchoPro
full-duplex voice interaction and barge-in behavior.

The existing simplex golden sessions validate VAD, ASR, wake state, route
selection, and action parameters. They do not validate full-duplex behavior. A
full-duplex golden session must prove that the microphone continues streaming
while TTS is playing, that real user speech during TTS can interrupt playback,
and that TTS echo alone does not falsely trigger barge-in.

## Validation Goals

A full-duplex golden session must support these checks:

- Mic upstream remains continuous during TTS playback.
- User speech overlapping TTS is detected as `speech_start`.
- Barge-in confirmation sends `tts_cancel` within the target latency window.
- TTS playback stops after cancel and old TTS chunks are not replayed.
- TTS echo without user speech does not trigger false `speech_start` or
  `tts_cancel`.
- Processed mic audio keeps user barge-in speech while suppressing residual TTS
  echo enough for server VAD.

## Required Layout

Store full-duplex data as session-shaped golden data under the existing golden
root:

```text
audio_interact/tests/golden/sessions/<session_id>/
  manifest.json
  audio/
    mic_proc_16k.wav
    tts_ref_16k.wav
    mic_raw_16k.wav
  events/
    runtime_events.jsonl
    vad_runtime.jsonl
    asr_runtime.jsonl
    tts_runtime.jsonl
    bargein_runtime.jsonl
  labels/
    vad_label.json
    asr_label.json
    tts_label.json
    bargein_label.json
```

`mic_raw_16k.wav` is recommended for every real hardware capture. It may be
omitted only for synthetic CI fixtures when the manifest explicitly sets
`source: "synthetic"` and explains the synthesis recipe.

## Audio Requirements

All WAV files must use:

```text
sample_rate: 16000
channels: 1
format: wav_pcm_s16le
frame_size: 512 samples
frame_duration_ms: 32
```

Audio files have these meanings:

- `mic_raw_16k.wav`: raw microphone capture before AEC or muting, containing
  user speech, room noise, and speaker echo.
- `mic_proc_16k.wav`: exact PCM stream sent to the cloud VAD after Pi-side AEC,
  hardware-AEC passthrough, or half-duplex muting.
- `tts_ref_16k.wav`: exact TTS audio emitted by the speaker, time-aligned to the
  session clock.

`mic_proc_16k.wav` must not be reconstructed after the fact unless the session is
marked synthetic. For real captures it must be recorded from the runtime stream
that was actually sent over `/ws/audio`.

## Manifest Requirements

`manifest.json` must include the normal golden-session fields plus full-duplex
metadata:

```json
{
  "schema_version": "1.1",
  "session_id": "fdx-20260723-001",
  "case_id": "wonderecho_fullduplex_bargein_001",
  "label": "WonderEchoPro full-duplex barge-in",
  "mode": "full_duplex",
  "duration_ms": 18000,
  "capture_point": "pi_wonderecho_proc",
  "source": "hardware_capture",
  "device_id": "turbopi-01",
  "audio": {
    "mic_proc": "audio/mic_proc_16k.wav",
    "mic_raw": "audio/mic_raw_16k.wav",
    "tts_ref": "audio/tts_ref_16k.wav",
    "sample_rate": 16000,
    "channels": 1,
    "format": "wav_pcm_s16le",
    "chunk_ms": 32,
    "duration_ms": 18000
  },
  "labels": {
    "vad": "labels/vad_label.json",
    "asr": "labels/asr_label.json",
    "tts": "labels/tts_label.json",
    "bargein": "labels/bargein_label.json"
  },
  "events": {
    "runtime": "events/runtime_events.jsonl",
    "vad": "events/vad_runtime.jsonl",
    "asr": "events/asr_runtime.jsonl",
    "tts": "events/tts_runtime.jsonl",
    "bargein": "events/bargein_runtime.jsonl"
  },
  "guards": [
    "vad_asr",
    "full_duplex",
    "bargein",
    "echo_false_positive"
  ],
  "full_duplex": {
    "pi_listener_proto": 2,
    "hardware_aec": false,
    "software_aec": "pipewire_webrtc",
    "aec_source": "ec_source",
    "aec_sink": "ec_sink",
    "half_duplex": false,
    "bargein_rms": 3200,
    "bargein_frames": 5,
    "tts_volume_pct": 80
  }
}
```

For backward compatibility, `audio.file` may still point to `mic_proc_16k.wav`,
but full-duplex validators must prefer `audio.mic_proc`, `audio.mic_raw`, and
`audio.tts_ref` when present.

## Runtime Event Requirements

Runtime events must use session-relative timestamps in milliseconds. JSONL files
must contain one JSON object per line.

`events/vad_runtime.jsonl` should include:

```json
{"ts_ms": 2048, "type": "vad.speech_start", "segment_id": "seg_002", "probability": 0.88, "during_tts": true}
{"ts_ms": 2688, "type": "vad.speech_end", "segment_id": "seg_002", "start_ms": 2048, "end_ms": 2688}
```

`events/tts_runtime.jsonl` should include:

```json
{"ts_ms": 1200, "type": "tts.begin", "tts_id": "tts_001", "turn_id": "turn_001"}
{"ts_ms": 1320, "type": "tts.play_start", "tts_id": "tts_001"}
{"ts_ms": 2310, "type": "tts.cancel", "tts_id": "tts_001", "reason": "barge_in"}
{"ts_ms": 2350, "type": "tts.play_stop", "tts_id": "tts_001", "reason": "barge_in"}
```

`events/bargein_runtime.jsonl` should include:

```json
{"ts_ms": 2110, "type": "bargein.local_duck", "tts_id": "tts_001", "segment_id": "seg_002"}
{"ts_ms": 2310, "type": "bargein.commit", "tts_id": "tts_001", "segment_id": "seg_002"}
```

If the runtime emits wire-level names such as `tts_begin`, `tts_state`,
`speech_start`, and `tts_cancel`, the importer may normalize them to the dotted
names above. The raw event payloads should be preserved when practical.

## Label Requirements

`labels/vad_label.json` records the per-user-turn speech boundaries. For speech
that happens during TTS, include overlap metadata:

```json
{
  "turn_id": "turn_002",
  "index": 2,
  "vad_start_ms": 2048,
  "vad_end_ms": 2688,
  "expected_text": "停下。",
  "expected_wake_status": "awake",
  "expected_status": "ok",
  "expected_route": "action",
  "expected_skill_id": "stop",
  "overlap_tts_id": "tts_001",
  "guards": ["vad_asr", "full_duplex", "bargein"]
}
```

`labels/asr_label.json` records the expected ASR turns, including `turn_id`,
`start_ms`, `end_ms`, `raw_text`, and `normalized_text`.

`labels/tts_label.json` must describe every audible TTS segment:

```json
{
  "schema_version": "1.0",
  "tts_segments": [
    {
      "tts_id": "tts_001",
      "turn_id": "turn_001",
      "text": "好的，我现在往前走。",
      "play_start_ms": 1320,
      "expected_play_end_ms": 4200,
      "actual_stop_ms": 2350,
      "interrupted": true,
      "stop_reason": "barge_in"
    }
  ]
}
```

`labels/bargein_label.json` must describe positive and negative barge-in cases:

```json
{
  "schema_version": "1.0",
  "bargein_cases": [
    {
      "case_id": "bargein_middle_001",
      "tts_id": "tts_001",
      "user_turn_id": "turn_002",
      "user_speech_start_ms": 2048,
      "user_speech_end_ms": 2688,
      "should_interrupt": true,
      "expected_interrupt_commit_after_ms": 2048,
      "expected_interrupt_commit_before_ms": 2348,
      "expected_post_cancel_tail_ms_max": 250
    },
    {
      "case_id": "echo_only_001",
      "tts_id": "tts_002",
      "user_turn_id": null,
      "user_speech_start_ms": null,
      "user_speech_end_ms": null,
      "should_interrupt": false
    }
  ]
}
```

## Required Scenario Coverage

The first full-duplex golden pack must include at least these sessions or cases:

- `echo_only`: TTS plays while no user speaks. Expected: no `tts_cancel`.
- `bargein_early`: user interrupts within 300-800 ms after TTS starts.
- `bargein_middle`: user interrupts in the middle of TTS.
- `bargein_late`: user interrupts near the end of TTS.
- `speech_after_tts`: user speaks after TTS ends. Expected: normal next turn,
  not barge-in.

Hardware captures should vary speaker volume, user distance, and background
noise after the first pack is stable. The first pack may keep those controlled
so the core pipeline is easy to debug.

## Acceptance Metrics

Full-duplex validation must compute these metrics from audio, labels, and
events:

```text
false_bargein_count == 0
bargein_speech_recall >= 0.90
duck_delay_ms <= 150
cancel_delay_ms <= 300 target, <= 400 maximum
post_cancel_tts_tail_ms <= 150 target, <= 250 maximum
during_tts_echo_false_speech_start_count == 0
```

Residual echo should be measured on `mic_proc_16k.wav` during echo-only TTS
windows:

```text
during_tts_residual_rms / idle_floor_rms < 2.0 target
```

If the ratio is higher but no false barge-in occurs, the session may pass the
functional gate but must be marked with a residual-echo warning.

## Synthetic Data Policy

Synthetic data is useful for CI coverage of parsers, timeline alignment, and
barge-in scoring. It is not sufficient to prove WonderEchoPro full-duplex
hardware behavior.

A synthetic session must set:

```json
{
  "source": "synthetic",
  "full_duplex": {
    "hardware_validated": false
  }
}
```

Synthetic `mic_raw` can be generated as:

```text
mic_raw = user_speech + delayed_attenuated(tts_ref) + noise
```

Synthetic `mic_proc` can be generated by an idealized AEC model or by the
session writer's deterministic fixture generator. The manifest must record the
method so regressions remain interpretable.

## Real Hardware Capture Requirements

A real WonderEchoPro full-duplex golden session must record:

- Pi listener version or git commit.
- `proto` value used by `/ws/audio`.
- `hardware_aec`, `half_duplex`, and PipeWire WebRTC AEC settings.
- TTS output device and mic capture device.
- Speaker volume.
- Whether `aec_debug` was enabled.
- Notes about user distance and room noise.

Real capture is required before marking the full-duplex feature stable. Passing
synthetic golden tests only means the software pipeline and validators are
internally consistent.

## CI Guard Names

Use these guard tags for full-duplex checks:

```text
full_duplex          mic continuity and TTS overlap timeline
bargein              positive interruption cases
echo_false_positive  echo-only negative cases
tts_cancel           cancel ordering and post-cancel playback tail
aec_residual         residual echo RMS threshold or warning
```

These guards are additive to existing tags such as `vad_asr`, `wake_state`, and
`react_route`.
