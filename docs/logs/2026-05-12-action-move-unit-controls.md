# 2026-05-12 Action Move Unit Controls

## Request

Add emergency stop, reset, forward/back/left/right movement buttons, heading buttons, and online unit settings to `action_move`. Each click should execute one configured unit:

- Movement unit distance: default 5 cm.
- Turn angle unit: default 5 degrees.
- Sensitivity: default 1.0x.

Deploy the result to Tencent Cloud.

## Implementation

- Added cloud settings endpoints:
  - `GET /action/api/settings`
  - `POST /action/api/settings`
- Persisted settings to `action_move/data/settings.json`.
- Attached a settings snapshot to every created task so the Raspberry Pi executes the unit shown when the button was clicked.
- Added queue priority for `emergency_stop`.
- Added skills in `skill_catalog.json`:
  - `emergency_stop`
  - `reset_pose`
  - `turn_left`
  - `turn_right`
  - existing look and movement skills retained.
- Updated `edge_action_poller.py` to pass task settings into the executor.
- Updated `action_move_executor.py` to:
  - publish repeated zero Twist for emergency stop.
  - stop base and reset PWM servo 1/2 to 1500 for Reset.
  - convert movement distance units to bounded timed `/cmd_vel` bursts.
  - convert heading angle units to bounded timed `/cmd_vel` turns.
- Rebuilt the web UI as UTF-8 Chinese text after finding corrupted static content.

## Verification

Local verification:

- `python -m py_compile action_move/server.py action_move/action_move_executor.py action_move/edge_action_poller.py`
- `python -m json.tool action_move/skill_catalog.json`
- FastAPI TestClient confirmed settings save and task creation with settings snapshot.
- Executor dry-run confirmed `emergency_stop`, `reset_pose`, and `turn_left` command generation.

Deployment verification:

- `https://www.wangyutang.cn/action/api/settings` returns the default settings.
- `https://www.wangyutang.cn/action/` shows the new controls.
- `https://www.wangyutang.cn/action/api/skills` returns 12 skills including `emergency_stop`, `reset_pose`, `turn_left`, and `turn_right`.
- Tencent Cloud `action-move` container was restarted and stayed running.
- Raspberry Pi `/home/pi/action_move` was updated and `action-move-poller.service` restarted successfully.
- End-to-end safe tasks completed:
  - `emergency_stop`: complete, zero Twist published.
  - `reset_pose`: complete, zero Twist published and PWM servo 1/2 reset to 1500.
- Raspberry Pi dry-run confirmed command generation for `move_forward` and `turn_right` with the default 5 cm / 5 degree settings snapshot.

## Follow-up: Repeated Movement Queue

After web testing, multiple `move_forward` / `move_backward` tasks appeared with nearly identical timestamps. The poller was not repeating one task; the cloud queue had accepted multiple movement tasks and executed them one by one.

Mitigation:

- Stopped the Raspberry Pi poller.
- Killed any active action executor process.
- Published zero Twist to `/cmd_vel` five times.
- Restarted the Tencent Cloud `action-move` container to clear the in-memory queue.
- Added backend protection: if a base movement or turn task is already pending/claimed/running, new movement/turn tasks return HTTP 409 instead of entering the queue.
- Added frontend protection: movement and turn buttons are disabled while a movement/turn task is active; emergency stop remains available.

## Follow-up: High Response Latency

Observed cloud task timings:

- Task claim latency: about `0.16s` to `0.39s`.
- Emergency stop completion latency: about `2.7s` to `2.9s`.
- Movement completion latency: about `4.8s` to `4.9s`.

Raspberry Pi local timing:

- `docker exec -u ubuntu turbopi bash -lc 'true'`: about `0.06s`.
- Loading ROS2 setup files inside the container: about `0.65s`.
- One `ros2 topic pub --once /cmd_vel ...`: about `1.96s`.
- Current `action_move_executor.py emergency_stop`: about `2.42s`.

Conclusion:

The high latency is not mainly the cloud queue or poll interval. The dominant cost is starting a fresh `docker exec` and `ros2 topic pub` process for every button click. Base movement adds its own timed burst duration, so a 5 cm movement currently spends about 2 seconds starting the ROS2 CLI plus about 0.8 seconds publishing movement, then publishes stop and reports the result.

Best fix:

Run a persistent edge control process inside the `turbopi` ROS2 environment. That process should keep ROS2 publishers open and accept local commands over a lightweight socket or HTTP endpoint from the poller. This removes the repeated ROS2 CLI startup path and should reduce command start latency from seconds to tens or hundreds of milliseconds.
