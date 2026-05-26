# Audio Real Verification Before Shutdown

## Verification Time

2026-05-14

## Verified

- Tencent Cloud audio service is healthy:
  - `https://www.wangyutang.cn/audio/api/health`
  - status `ok`
  - no historical results or events after mock cleanup
- Local ASR wrapper is connected to the real Qwen3-ASR backend:
  - `http://127.0.0.1:8097/health`
  - `backend_ok=true`
  - `model=qwen3-asr`
- Raspberry Pi is reachable at `192.168.137.2`.
- WonderEchoPro audio hardware is visible as:
  - USB `0c76:161f JMTek, LLC. USB PnP Audio Device`
  - ALSA `card 2: Device [USB PnP Audio Device], device 0`
- TurboPi control stack is running in the `turbopi` container.
- ROS control topics are present:
  - `/cmd_vel`
  - `/ros_robot_controller/pwm_servo/set_state`
  - `/ros_robot_controller/bus_servo/set_position`
  - `/ros_robot_controller/set_motor_speeds`
- `action_move` reports `turbopi-01` online and idle.

## Real Audio Test

A real 4-second WAV was recorded from the Raspberry Pi audio device:

```text
/tmp/audio_recognition_real_verify.wav
16000 Hz, mono, signed 16-bit PCM
128044 bytes
```

The file was copied to the ASR project and submitted to:

```text
POST http://127.0.0.1:8097/v1/audio/transcriptions
```

ASR returned successfully but with an empty transcript:

```json
{"text": ""}
```

This is treated as a non-successful end-to-end voice-command verification, because no real spoken command was recognized and therefore no servo command should be routed.

## Shutdown Decision

The Raspberry Pi was not shut down because the user's condition was "after final verification succeeds". Infrastructure and hardware checks passed, but the real audio-to-text-to-servo command was not completed with a non-empty real transcript.
