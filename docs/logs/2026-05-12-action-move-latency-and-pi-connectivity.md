# 2026-05-12 Action Move Latency And Pi Connectivity

## Context

User reported that `https://www.wangyutang.cn/action/` reacted slowly when controlling TurboPi actions such as camera look and movement.

The related module is:

```text
action_move/
```

The public service is:

```text
https://www.wangyutang.cn/action/
```

## Observed Problems

### Slow UI Feedback

The browser page refreshed every 5 seconds. Even when tasks were already claimed or complete, the UI could still appear stale.

### Queue Claim Delay

The Raspberry Pi edge poller sent an idle heartbeat before checking for new tasks. That added an unnecessary network round trip before task claim.

### Claimed Task Could Hang

A `move_forward` latency probe was claimed but did not complete. Cloud health showed:

```text
current_task_id = 1778547485616-735615
status = running
online = false
```

This means the edge process was blocked or the Pi lost connectivity while a task was running.

### Raspberry Pi Briefly Became Unreachable

During the stuck `move_forward` probe, SSH and ping to `192.168.137.2` failed for a period of time.

Later SSH recovered and the Pi showed it had rebooted:

```text
hostname: raspberrypi
uptime: about 7 minutes
```

This suggests the robot/network environment is sensitive during live movement tests. Movement can physically alter Wi-Fi quality, power draw, or hotspot stability.

### ROS2 CLI Execution Overhead

Task output showed repeated:

```text
Waiting for at least 1 matching subscription(s)...
```

The executor used per-action shell commands:

```text
docker exec -u ubuntu turbopi bash -lc "ros2 topic pub ..."
```

This has fixed startup overhead and can wait before publishing.

### Camera Capture Blocked Servo Completion

Camera look commands requested a camera snapshot after moving the servo. The task could be physically complete while result reporting waited for the camera HTTP request.

## Measurements

Before fixes:

```text
/action/api/health: about 204 ms
/action/api/skills: about 98 ms
/action/api/tasks: about 129 ms

look_left:
  claim latency: 0.787 s
  completion latency: 3.696 s

move_forward:
  became stuck in claimed/running state
```

After cloud/front-end fixes:

```text
look_right:
  claim latency: 0.218 s
  completion latency: 5.091 s
```

After Pi edge/executor fixes:

```text
look_left:
  claim latency: 0.758 s
  completion latency: 3.317 s

look_right:
  claim latency: 0.280 s
  completion latency: 2.565 s

direct Pi executor timing:
  python3 action_move_executor.py look_right: 1.969 s
```

## Fixes Applied

### Tencent Cloud

Updated:

```text
/root/control_platform/action_move/server.py
/root/control_platform/action_move/static/index.html
/root/control_platform/action_move/static/app.js
/root/control_platform/action_move/static/style.css
```

Restarted:

```text
docker restart action-move
```

Changes:

- Added cloud-side timeout for long-claimed tasks.
- Added `claim_latency_seconds` and `completion_latency_seconds`.
- Changed browser refresh to 1 second.
- Added immediate button busy feedback.
- Restored readable Chinese button text.
- Added task status styling.

### Raspberry Pi

Updated:

```text
/home/pi/action_move/edge_action_poller.py
/home/pi/action_move/action_move_executor.py
/home/pi/action_move/latency_analysis_20260512.md
```

Updated systemd service:

```text
ExecStart=/usr/bin/python3 /home/pi/action_move/edge_action_poller.py --server https://www.wangyutang.cn/action --device-id turbopi-01 --interval 0.35
```

Restarted:

```text
sudo systemctl restart action-move-poller.service
```

Changes:

- Poll tasks before sending idle heartbeat.
- Reduce poll interval from 1.5 seconds to 0.35 seconds.
- Add edge action timeout.
- Add executor subprocess timeout.
- Add `--wait-matching-subscriptions 0` to ROS2 publish commands.
- Make camera snapshot request fire-and-forget for servo commands.

## Current Status

At the end of the fix:

```text
https://www.wangyutang.cn/action/api/health
  status = ok
  device_id = turbopi-01
  online = true
  pending_tasks = 0
```

The `/action/` page and static assets returned HTTP 200.

## Remaining Limitation

The largest remaining latency source is local process startup:

```text
edge poller -> python executor -> docker exec -> bash -> ROS2 CLI -> publish
```

The direct executor still takes about 2 seconds for a camera-look command.

## Recommended Next Improvement

Build a persistent Pi-side ROS2 action bridge:

```text
edge poller -> local bridge API/IPC -> persistent rclpy publishers -> ROS2
```

Expected benefits:

- Avoid per-action `docker exec`.
- Avoid per-action `ros2 topic pub` startup.
- Keep publishers warm.
- Reduce camera-look latency toward sub-second response.

## Safety Notes

Avoid repeated live `move_forward` tests unless the robot is physically safe and network stability is confirmed. The Pi briefly became unreachable during a movement probe, and the robot/hotspot setup is sensitive to movement, power, and Wi-Fi signal quality.

The detailed module-local analysis is also recorded at:

```text
docs/action_move/latency_analysis_20260512.md
```
