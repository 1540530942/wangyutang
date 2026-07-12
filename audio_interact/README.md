# audio_interact

Pi-side audio interaction service for TurboPi.

## Flow

```text
Pi microphone
  -> edge_streamer.py
  -> Silero VAD speech segment
  -> 16 kHz WAV
  -> WebSocket /audio_interact/ws/audio
  -> common ASR
  -> wake-state gate
  -> robot_sandbox /api/recognize-text
```

## Wake State

The cloud service keeps wake state per `device_id`.

- Sleeping by default.
- Wake word: `你好瓦力`, including common homophones such as `你好瓦利`, `你好哇力`, `你好挖力`, and `你好 walle`.
- After wake, every recognized utterance is routed continuously.
- `退下吧` or close variants put the device back to sleep.
- If the wake phrase includes a command, for example `你好瓦力 前进`, only the command part is routed.

## Files

| File | Where | Purpose |
|---|---|---|
| `edge_streamer.py` | Pi host | PyAudio capture, Silero VAD, WAV upload over WebSocket |
| `server.py` | Tencent Cloud container | WebSocket receiver, ASR call, wake-state gate, route forwarding |
| `wake_state.py` | Tencent Cloud container | Pure text wake/dismiss state machine |

## Pi Setup

```bash
python3 -m pip install -r requirements-edge.txt
python3 edge_streamer.py --config config.json --loop
```

Silero loads through `torch.hub` from `snakers4/silero-vad` on first run. To use the old energy fallback for emergency testing:

```bash
python3 edge_streamer.py --vad-mode energy --energy-threshold 5000
```

## Cloud Setup

The cloud service is deployed as the `audio-interact` Compose service and exposed by the gateway at:

```text
wss://www.wangyutang.cn/audio_interact/ws/audio
https://www.wangyutang.cn/audio_interact/api/health
```

## Session Replay / Eval

Runtime capture writes replayable packages under:

```text
<AUDIO_INTERACT_DATA_DIR>/sessions/<YYYY-MM-DD>/<session_id>/
```

Each package contains `manifest.json`, `audio/`, `events/`, `labels/`, `replay/`, and `reports/`.

Replay and evaluate a standard session:

```bash
python tools/replay_eval.py --session_dir /app/data/sessions/2026-07-08/<session_id>
```

Convert old recording packages first:

```bash
python tools/import_legacy_recording.py /app/data/recordings/2026-07-08/<session_id>
```

Run a batch of sessions:

```bash
python tools/run_regression.py /app/data/sessions
```

## Golden Sessions / Dashboard

Golden test data follows the same full-session principle as runtime capture:

```text
tests/golden/sessions/<session_id>/
  manifest.json
  audio/mic_proc_16k.wav
  labels/turns.json
```

Each golden session keeps one continuous multi-turn audio file. Tests may assert
per-turn behavior, but the primary data unit remains the session.

`/audio_interact/api/golden` reads this session layout and returns the
legacy-compatible shape consumed by `/dashboard/vad_asr`. The audio endpoint
accepts either the case id or session id:

```text
/audio_interact/api/golden/audio/vad_asr_simplex_001
/audio_interact/api/golden/audio/e34689be-8d7
```

`/dashboard/detail` uses runtime session APIs and is independent of golden data.

Canonical policy and add-new-session steps live in:

```text
../docs/audio-interact-golden-sessions.md
```
