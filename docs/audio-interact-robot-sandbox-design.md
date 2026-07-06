# audio_interact and robot_sandbox Design

## Purpose

This document defines the target boundary between voice interaction and robot command execution.

The target split is:

```text
audio_interact = audio input/output adapter
robot_sandbox  = text command planning, validation, execution, trace, and replay
```

`robot_sandbox` may support real robot execution. In this project, "sandbox" means a controlled, observable, replayable execution environment, not an offline-only mock.

## Target Flow

```text
WonderEchoPro / USB Mic / Web Mic / VAD stream
        |
        v
audio_interact
  - capture audio
  - VAD when needed
  - ASR
  - wake-state gate
  - TTS synthesis and playback
        |
        v
text + metadata
        |
        v
robot_sandbox
  - understand text
  - produce tool calls
  - validate parameters
  - run safety guard
  - dry-run or real execution
  - return diagnostics and tts_text
        |
        v
action_move / camera_snapshot / face / slam_mapping
```

## audio_interact Boundary

`audio_interact` owns all audio-related responsibilities:

- Audio capture from WonderEchoPro, USB microphone, browser microphone, or VAD stream.
- VAD-based speech segmenting for streaming microphone mode.
- ASR calls through `common_api_manager`.
- Wake-word and sleep-state handling.
- Calling `common_api_manager` TTS with downstream `tts_text`.
- Playback through browser WebSocket audio frames or Raspberry Pi local audio player.
- Input-mode settings for `wonderechopro`, `vad_asr`, and web audio.

It should not plan robot actions or decide robot tool calls.

## WonderEchoPro Mode

Current WonderEchoPro behavior is:

```text
Web page selects WonderEchoPro
-> /audio/api/settings sets input_mode=wonderechopro
-> web page starts manual recording
-> Raspberry Pi edge listener polls settings
-> arecord records a fixed WAV segment from plughw:CARD=Device,DEV=0
-> common_api ASR returns text
-> text is routed to audio_recognition
```

Target WonderEchoPro behavior:

```text
Web page selects WonderEchoPro
-> audio_interact stores input_mode=wonderechopro
-> Raspberry Pi audio_interact edge listener records fixed WAV segments when requested
-> audio_interact calls common_api ASR
-> audio_interact applies wake-state policy if enabled
-> audio_interact sends text to robot_sandbox
-> robot_sandbox returns tool calls, execution results, diagnostics, and tts_text
-> audio_interact calls common_api TTS and plays audio
```

WonderEchoPro should be treated as an audio input device, not as part of the command sandbox.

## VAD_ASR Mode

VAD_ASR mode means:

```text
Microphone continuously captures audio
-> VAD detects speech start/end
-> speech segment is converted to WAV/PCM
-> ASR converts speech to text
-> text enters the same downstream path as WonderEchoPro
```

Target location:

- Pi-side streamer: `audio_interact/edge/vad_streamer.py`
- Cloud/WebSocket receiver: `audio_interact/server.py`
- Wake state: `audio_interact/wake_state.py`
- TTS: `audio_interact/tts.py`

## robot_sandbox Boundary

`robot_sandbox` owns text-to-robot control:

- Text command input.
- Context input, including ASR metadata, wake metadata, vision metadata, and optional SLAM metadata.
- LLM ReAct planning.
- Tool-call generation.
- Tool-call validation.
- Safety guard.
- Dispatch mode selection:
  - `dry_run`: plan and validate only.
  - `cloud_queue`: enqueue tasks to cloud services such as `action_move`.
  - `local_first`: prefer a local action server when available.
- Execution result collection.
- Diagnostics for problem analysis and localization.
- Envelope persistence and replay.
- `tts_text` generation for downstream playback.

It should not synthesize or play TTS audio.

## robot_sandbox Response Contract

The sandbox response should make debugging and real-car verification explicit:

```json
{
  "ok": true,
  "command_id": "cmd-xxx",
  "skill_id": "move_forward",
  "tool_calls": [
    {
      "tool": "front_distance",
      "args": {},
      "reason": "check obstacle distance before moving forward"
    },
    {
      "tool": "dispatch_action",
      "args": {
        "skill_id": "move_forward",
        "distance_cm": 10
      }
    }
  ],
  "execution_results": [
    {
      "tool": "front_distance",
      "status": "completed",
      "result": {
        "front_distance_cm": 80
      }
    },
    {
      "tool": "dispatch_action",
      "status": "completed",
      "result": {
        "action_task_id": "task-xxx",
        "vehicle_execution": {
          "actual_distance_cm": 9.6
        }
      }
    }
  ],
  "diagnostics": {
    "planner": "react_llm",
    "safety": "passed",
    "errors": [],
    "latency_ms": {
      "planning": 820,
      "dispatch": 1300
    }
  },
  "tts_text": "好的，前进10厘米",
  "envelope": {}
}
```

`tts_text` is plain text only. Audio synthesis and playback belong to `audio_interact`.

## Existing Code To Move

Move from `audio_recognition` to `audio_interact`:

- `audio_recognition/transport/edge_listener.py`
  - Target: `audio_interact/edge/wonderecho_listener.py`
- `audio_recognition/transport/recorder.py`
  - Target: `audio_interact/edge/recorder.py`
- WonderEchoPro `input_mode` and manual recording settings
  - Target: `audio_interact/settings.py`
- Pi-side TTS fetch and `aplay`/`paplay` playback
  - Target: `audio_interact/tts.py`
- Audio upload and ASR orchestration
  - Target: `audio_interact/asr.py`

Keep in `robot_sandbox`:

- `audio_recognition/harness/react_loop.py`
- `audio_recognition/agent/react_agent.py`
- `audio_recognition/tools/*`
- `audio_recognition/safety/*`
- `audio_recognition/skills/*`
- `audio_recognition/storage/envelope_store.py`
- `audio_recognition/storage/replay.py`
- `audio_recognition/storage/case_store.py`

The current directory may stay as `audio_recognition` during migration, but its service role should become `robot_sandbox`.

## Related Modules

`common_api_manager`:

- Provides ASR, LLM, Vision, and TTS.
- `audio_interact` should call ASR and TTS.
- `robot_sandbox` should call LLM and Vision.

`action_move`:

- Executes validated robot motion tasks.
- Should not understand natural language.

`camera_snapshot`:

- Provides observation tools for `robot_sandbox`.
- Should not be owned by `audio_interact`, except for optional UI preview.

`slam_mapping`:

- Optional context and observation provider.
- Should provide pose, odometry delta, local map, reset map, and motion reporting.
- Absence of SLAM must not block basic motion execution.

## Migration Plan

1. Add `robot_sandbox` API semantics while keeping the current `audio_recognition` package path for compatibility.
2. Add WonderEchoPro support to `audio_interact` by moving the edge listener and recorder there.
3. Route WonderEchoPro as:

```text
Pi -> audio_interact -> robot_sandbox
```

instead of:

```text
Pi -> audio_recognition /api/results
```

4. Move TTS synthesis and playback to `audio_interact`.
5. Make `robot_sandbox` return `tool_calls`, `execution_results`, `diagnostics`, `envelope`, and `tts_text`.
6. Move input-mode UI/API from `audio_recognition` to `audio_interact`.
7. Add compatibility shims for old `/audio/api/*` routes during rollout.
8. Rename the package or service only after the API boundary is stable.

## Design Decision

The preferred boundary is:

```text
audio_interact:
  audio -> ASR text
  tts_text -> TTS audio -> playback

robot_sandbox:
  text -> tool_calls -> validation -> execution -> diagnostics -> tts_text
```

This keeps real robot execution possible while making the system easier to test, replay, debug, and extend.
