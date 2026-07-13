# Audio Interact Golden Sessions

Golden audio data follows the same principle as runtime capture: one complete
multi-turn session is the primary data unit. Tests can expand a session into
per-turn assertions, but the archived audio must remain continuous.

## Layout

```text
audio_interact/tests/golden/sessions/<session_id>/
  manifest.json
  audio/
    mic_proc_16k.wav
  labels/
    turns.json
```

`manifest.json` describes the session-level recording:

- `session_id`
- `case_id`
- `mode`
- `capture_point`
- `source`
- `device_id`
- `audio.file`
- `tiers`
- session-level `guards`

`labels/turns.json` contains every user turn in the session. It is the place for
per-turn expected ASR text, wake state, route type, skill id, and structured
settings such as `unit_distance_cm`.

## Current Golden Sessions

### vad_asr_simplex_001

```text
session_id:    e34689be-8d7
case_id:       vad_asr_simplex_001
capture_point: browser_processed
device_id:     web-audio
audio:         audio_interact/tests/golden/sessions/e34689be-8d7/audio/mic_proc_16k.wav
```

Important guarded turns:

```text
turn_004: 往前走十五厘米。  -> move_forward,  unit_distance_cm=15.0
turn_005: 向右走十五厘米。  -> move_right,    unit_distance_cm=15.0
```

These turns protect against losing Chinese-number distance parameters in the LLM
ReAct route.

### pi_alsa_wake_distance_001

```text
session_id:    pi-1783880433141
case_id:       pi_alsa_wake_distance_001
capture_point: pi_alsa_raw
device_id:     turbopi-01
audio:         audio_interact/tests/golden/sessions/pi-1783880433141/audio/mic_proc_16k.wav
```

Important guarded turns:

```text
turn_001: 你好，瓦力。        -> wake_word
turn_002: 向右看。            -> look_right  (camera_servo, no user distance)
turn_003: 向后走五厘米啊！    -> move_backward, unit_distance_cm=5.0
```

Pi ALSA raw capture (no browser AEC). Covers the `distance_param` guard with a
user-specified distance from a real Pi device session.

## Dashboard Compatibility

`/audio_interact/api/golden` reads `sessions/*/manifest.json` first and returns a
legacy-compatible JSON shape for `/dashboard/vad_asr`.

`/audio_interact/api/golden/audio/{id}` accepts either:

```text
case_id     -> vad_asr_simplex_001
session_id  -> e34689be-8d7
```

Both resolve to the session audio declared by `manifest.json`, for example:

```text
audio_interact/tests/golden/sessions/e34689be-8d7/audio/mic_proc_16k.wav
```

`/dashboard/detail` is not backed by golden data. It continues to use runtime
session APIs:

```text
/audio_interact/api/sessions
/audio_interact/api/sessions/{session_id}
/audio_interact/api/sessions/{session_id}/audio/{filename}
```

## Deprecated Flat Layout

The older layout is deprecated:

```text
audio_interact/tests/golden/<case_id>.json
audio_interact/tests/golden/audio/<case_id>.wav
```

The service still keeps fallback read logic for legacy flat JSON files, but new
golden data must use `sessions/<session_id>/`.

## CI Layering

The session data is shared by layered CI checks:

```text
ci_tests/vad_asr/          full-session audio replay; VAD/ASR/wake assertions
ci_tests/robot_sandbox/    live LLM ReAct dry-run; semantic routing assertions
ci_tests/action_move/      no LLM; action parameter-to-duration mapping
ci_tests/audio_interact/   cross-module semantic pipeline and API compatibility
```

Useful guard tags in `turns.json`:

```text
vad_asr         VAD timing, ASR text, wake state
wake_state      cross-turn wake progression
react_route     robot_sandbox ReAct semantic routing
distance_param  movement distance must be preserved
movement        action_move distance-to-duration mapping
```

## Live Robot Execution Direction

Golden sessions are dry-run CI inputs by default. Real robot execution is a
separate, explicit pre-hardware-test verification step and must not run in the
ordinary deployment CI gate or from pytest.

The preferred product flow is through `/dashboard/vad_asr`:

```text
/dashboard/vad_asr
  select a golden session
  run dry-run validation first
  show action summary and target device
  require explicit human confirmation
  then execute route=true against the selected session
  display action_task status and final result
```

For the Pi ALSA case, the real actions would be limited to:

```text
case_id: pi_alsa_wake_distance_001
actions: look_right, move_backward with unit_distance_cm=5.0
target:  turbopi-01
```

The implementation should live behind an explicit dashboard API, not under
`ci_tests/`. The backend should read the selected golden session, replay it with
`route=true`, collect action_task ids, poll action_move for terminal status, and
return per-turn results to the page.

## Adding A New Golden Session

1. Create `audio_interact/tests/golden/sessions/<session_id>/`.
2. Put the full session WAV at `audio/mic_proc_16k.wav`.
3. Add `manifest.json` with `audio.file` relative to the session directory.
4. Add `labels/turns.json` with every user turn and expected wake/route fields.
5. Add `guards` to the turns that should enter CI checks.
6. Run local structural checks:

```bash
python -m pytest ci_tests/audio_interact/test_golden_session_api.py ci_tests/action_move/test_distance_mapping.py -q
```

Live LLM ReAct and remote audio replay checks run in CI, or locally when the
required services and environment variables are available. Real robot execution
should be triggered manually from the golden-session dashboard flow.
