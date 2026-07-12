# Golden Sessions

Golden audio data is stored as full multi-turn sessions. Do not add one WAV per
utterance as the primary data source.

Canonical policy lives in:

```text
../../../docs/audio-interact-golden-sessions.md
```

## Layout

```text
tests/golden/sessions/<session_id>/
  manifest.json
  audio/
    mic_proc_16k.wav
  labels/
    turns.json
```

`manifest.json` describes the session-level recording, including `case_id`,
`session_id`, capture point, source, device, audio file, tiers, and guard tags.

`labels/turns.json` contains per-turn expectations. A single session may contain
many user utterances. CI expands those turns into subcases for assertions, but
the audio remains one continuous session.

## Dashboard Compatibility

`/audio_interact/api/golden` reads `sessions/*/manifest.json` first and returns a
legacy-compatible JSON shape for `/dashboard/vad_asr`.

`/audio_interact/api/golden/audio/{id}` accepts either:

```text
case_id     -> vad_asr_simplex_001
session_id  -> e34689be-8d7
```

Both resolve to the session audio declared by `manifest.json`, such as:

```text
tests/golden/sessions/e34689be-8d7/audio/mic_proc_16k.wav
```

## Adding A New Golden Session

1. Create `tests/golden/sessions/<session_id>/`.
2. Put the full session WAV at `audio/mic_proc_16k.wav`.
3. Add `manifest.json` with an `audio.file` path relative to the session dir.
4. Add `labels/turns.json` with every user turn and expected wake/route fields.
5. Use `guards` to opt turns into layered CI checks:

```text
vad_asr         VAD timing, ASR text, wake state
wake_state      cross-turn wake progression
react_route     robot_sandbox ReAct semantic routing
distance_param  movement distance must be preserved
movement        action_move distance-to-duration mapping
```

The older flat layout is deprecated:

```text
tests/golden/<case_id>.json
tests/golden/audio/<case_id>.wav
```
