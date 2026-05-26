# Action Move Latency Analysis - 2026-05-12

## User Symptom

`https://www.wangyutang.cn/action/` feels slow after clicking movement or camera-look buttons.

## Data Flow

```text
Browser
  -> Tencent Cloud /action/api/tasks
  -> Raspberry Pi edge_action_poller.py
  -> action_move_executor.py
  -> docker exec -u ubuntu turbopi
  -> ROS2 topic pub
  -> optional camera snapshot request
  -> Tencent Cloud /action/api/tasks/result
  -> Browser refresh
```

## Measurements

Before the cloud/front-end fix:

- Public API latency from PC to Tencent Cloud:
  - `/action/api/health`: about 204 ms
  - `/action/api/skills`: about 98 ms
  - `/action/api/tasks`: about 129 ms
- `look_left` probe:
  - Task creation API: 144.5 ms
  - Claim latency: 0.787 s
  - Completion latency: 3.696 s
- A `move_forward` probe became stuck in `claimed`.
  - Cloud showed `current_task_id` as the stuck task.
  - Device became offline from the cloud perspective after heartbeat stopped.

After cloud/front-end fix:

- `look_right` probe:
  - Task creation API: 146.9 ms
  - Claim latency: 0.218 s
  - Completion latency: 5.091 s
- ROS2 output included:

```text
Waiting for at least 1 matching subscription(s)...
Waiting for at least 1 matching subscription(s)...
```

This means queue pickup is fast enough, but local execution is still slow.

After edge poller/executor deployment:

- `look_left` probe:
  - Claim latency: 0.758 s
  - Completion latency: 3.317 s
- `look_right` probe:
  - Claim latency: 0.280 s
  - Completion latency: 2.565 s
- Direct Raspberry Pi executor timing:
  - `python3 action_move_executor.py look_right`: 1.969 s

This confirms the remaining latency is mostly local process startup and ROS2 CLI overhead, not Tencent Cloud API latency.

## Root Causes

### 1. Browser Refreshed Too Slowly

The page refreshed every 5 seconds. Even if the robot had already claimed or completed a task, the UI could look stale until the next refresh.

Fix:

- Change page refresh to 1 second.
- Show claim/completion latency from the API.
- Show busy state immediately after a click.

### 2. Poller Did Extra Work Before Claiming

The edge poller previously sent an idle heartbeat before checking for work. This added an avoidable network round trip before task pickup.

Fix:

- Poll `/api/tasks/next` first.
- Send idle heartbeat only every 5 seconds when there is no task.
- Reduce default poll interval from 2.0 seconds to 0.35 seconds.

### 3. Claimed Tasks Could Stay Claimed Forever

If the Raspberry Pi process blocks or the network drops while executing a task, the cloud keeps the task as `claimed`.

Fix:

- Add cloud-side claimed-task timeout: 35 seconds.
- Add edge-side action timeout: 20 seconds.
- Add executor subprocess timeout: 12 seconds.

### 4. ROS2 CLI Startup Adds Delay

The executor shells out to:

```text
docker exec -u ubuntu turbopi bash -lc "ros2 topic pub ..."
```

This starts a new Python/ROS2 CLI process every action. It can wait for matching subscriptions before publishing, which was observed in the task output.

Fix:

- Add `--wait-matching-subscriptions 0` to ROS2 publish commands.

Longer-term improvement:

- Replace per-command `ros2 topic pub` shell calls with a persistent ROS2 bridge node.
- Keep publishers warm for `/cmd_vel` and `/ros_robot_controller/pwm_servo/set_state`.

### 5. Camera Capture Was Synchronous

Servo commands requested a camera snapshot after movement. The physical servo command may already be sent, but task completion waits for the camera capture HTTP request.

Fix:

- Trigger camera capture in the background after the servo command.
- Do not block task completion on camera snapshot latency.

## Changes Made

Cloud and browser:

- `server.py`
  - Adds `CLAIM_TIMEOUT_SECONDS`.
  - Adds `claim_latency_seconds` and `completion_latency_seconds` fields.
- `static/app.js`
  - Refreshes every 1 second.
  - Shows online/running status and latency.
  - Shows click busy state.
- `static/index.html`
  - Restores readable Chinese button text.
- `static/style.css`
  - Adds status styling for running/complete/failed states.

Edge and executor source:

- `edge_action_poller.py`
  - Polls task queue before idle heartbeat.
  - Reduces default interval to 0.35 seconds.
  - Adds action timeout handling.
- `action_move_executor.py`
  - Adds subprocess timeout.
  - Adds `--wait-matching-subscriptions 0`.
  - Makes camera capture fire-and-forget.

## Deployment Status

Applied on Tencent Cloud:

- `server.py`
- `static/index.html`
- `static/app.js`
- `static/style.css`
- Restarted `action-move`.

Not yet applied to Raspberry Pi:

- None. The edge files below have been applied:
  - `/home/pi/action_move/edge_action_poller.py`
  - `/home/pi/action_move/action_move_executor.py`

Reason:

- The Raspberry Pi briefly stopped responding to SSH on `192.168.137.2` during testing after a `move_forward` probe.
- SSH later recovered after the Pi rebooted.
- The systemd service was updated to use `--interval 0.35`.

## Next Steps

1. Re-run latency probes:
   - `look_left`
   - `look_right`
   - `move_forward` only when the robot is physically safe and network is stable.

Expected target after edge deployment:

- Claim latency: usually under 0.5 s.
- Camera-look completion: roughly 2 s to 3 s with the current shell/ROS2 CLI executor.
- Base movement completion: roughly movement duration plus process overhead, ideally under 3 s.

## Recommended Larger Fix

Build a persistent Pi-side ROS2 action bridge:

```text
edge poller -> local HTTP/IPC action bridge -> persistent rclpy publishers -> ROS2
```

This avoids repeatedly starting `docker exec` and `ros2 topic pub`, which is the largest remaining source of latency.
