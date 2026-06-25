# pose_tracker

Real-time 2D pose tracking for TurboPi. Fuses IMU orientation (yaw) from the robot controller board with `/cmd_vel` dead-reckoning to estimate position, and streams the result to a browser via WebSocket.

## Architecture

```
Raspberry Pi (turbopi container)
  /ros_robot_controller/imu_raw  ─┐
  /cmd_vel                        ├─ pi_imu_sender.py ──WebSocket /ws/pi──▶ server.py
                                                                                 │
                                                                          WebSocket /ws
                                                                                 │
                                                                            browser
                                                                          (index.html)
```

- Pi connects to `/ws/pi` and waits for a `start` command before sending any data.
- Browser controls streaming via **开始采集 / 停止采集** buttons.
- Server pushes incremental pose updates to browser at ≤ 20 Hz.

## Files

| File | Role |
|------|------|
| `pose_estimator.py` | Fuses IMU quaternion (yaw) + cmd_vel integration → x/y/yaw |
| `server.py` | FastAPI: Pi WebSocket channel, browser WebSocket feed, control API |
| `pi_imu_sender.py` | Runs on Pi inside turbopi container; streams at 20 Hz on demand |
| `static/index.html` | 2D top-down trajectory canvas + motion description panel |

## Run

### Server side

```bash
cd pose_tracker
pip install -r requirements.txt
python3 -m uvicorn server:app --host 0.0.0.0 --port 8300
```

Open `http://<server-ip>:8300/`

### Pi side (inside turbopi container)

```bash
docker exec turbopi bash -c "
  source /opt/ros/humble/setup.bash &&
  source /home/ubuntu/ros2_ws/install/setup.bash &&
  pip install websocket-client &&
  python3 /path/to/pi_imu_sender.py --server ws://<server-ip>:8300
"
```

Once the Pi is connected, click **开始采集** in the browser to start streaming.

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/start` | POST | Command Pi to start streaming |
| `/api/stop` | POST | Command Pi to stop streaming |
| `/api/reset` | POST | Reset pose to origin |
| `/api/status` | GET | Pi connection + streaming state |
| `/api/pose` | GET | Current pose snapshot |
| `/ws` | WebSocket | Browser real-time feed |
| `/ws/pi` | WebSocket | Pi data channel |

## Pose estimation notes

- **Yaw**: from IMU quaternion (reliable). Falls back to integrating `angular_z` from cmd_vel when IMU is inactive.
- **x/y**: dead-reckoning from cmd_vel linear velocity × elapsed time. Accumulates drift over long runs; use `/api/reset` to reset origin as needed.
- Trail is capped at 2000 points. Browser maintains its own copy and only receives incremental new points after the initial load.
