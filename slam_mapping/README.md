# SLAM Mapping

Optional SLAM/pose module for the robot platform. The first version is a small FastAPI service that stores robot pose, accepts odometry deltas, and can build a simple occupancy grid when range scan data is available.

It is deliberately pluggable:

- Motion control can run without this service.
- The service can run without a map and still report forward/backward travelled distance from odometry.
- When scan data is available, enable the map and post scans to update an occupancy grid.
- Later it can be replaced by a ROS 2 SLAM Toolbox/Nav2 bridge without changing the cloud-facing API shape.

## Port

```text
8301
```

Gateway route:

```text
/slam/
```

Browser UI:

```text
https://www.wangyutang.cn/slam/
```

The page renders the occupancy grid, robot pose, heading arrow, travelled distance, data source, update time, and trail from live `/api/state?include_map=true` data.

## Run

```bash
cd slam_mapping
pip install -r requirements.txt
python3 -m uvicorn server:app --host 0.0.0.0 --port 8301
```

## API

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check and current pose summary |
| `/api/state?include_map=false` | GET | Current pose/config; optional occupancy grid |
| `/api/config` | POST | Toggle map and adjust grid settings |
| `/api/reset` | POST | Reset pose and map |
| `/api/odom` | POST | Add an odometry delta |
| `/api/scan` | POST | Add one range scan |

Odometry example:

```bash
curl -X POST http://127.0.0.1:8301/api/odom \
  -H 'Content-Type: application/json' \
  -d '{"dx_m":0.2,"dy_m":0,"dyaw_rad":0,"source":"cmd_vel"}'
```

Enable map and add one scan:

```bash
curl -X POST http://127.0.0.1:8301/api/config \
  -H 'Content-Type: application/json' \
  -d '{"map_enabled":true,"resolution_m":0.05,"size_m":6.0}'

curl -X POST http://127.0.0.1:8301/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"ranges_m":[0.8,0.9,1.1],"angle_min_rad":-0.1,"angle_increment_rad":0.1}'
```

## Classic Verifiable Examples

These are the three first checks to run before wiring the module into real motion control.

1. Forward 20 cm, then confirm actual pose:

```bash
curl -X POST http://127.0.0.1:8301/api/reset
curl -X POST http://127.0.0.1:8301/api/odom \
  -H 'Content-Type: application/json' \
  -d '{"dx_m":0.2,"dy_m":0,"dyaw_rad":0,"source":"move_forward_20cm"}'
curl http://127.0.0.1:8301/api/state
```

Expected pose fields:

```json
{"x_m":0.2,"y_m":0.0,"yaw_deg":0.0,"distance_travelled_cm":20.0}
```

2. Turn left 90 degrees, move forward, then confirm the coordinate axis changed:

```bash
curl -X POST http://127.0.0.1:8301/api/reset
curl -X POST http://127.0.0.1:8301/api/odom \
  -H 'Content-Type: application/json' \
  -d '{"dx_m":0,"dy_m":0,"dyaw_rad":1.5707963268,"source":"turn_left_90deg"}'
curl -X POST http://127.0.0.1:8301/api/odom \
  -H 'Content-Type: application/json' \
  -d '{"dx_m":0.2,"dy_m":0,"dyaw_rad":0,"source":"forward_after_left_turn"}'
curl http://127.0.0.1:8301/api/state
```

Expected pose fields:

```json
{"x_m":0.0,"y_m":0.2,"yaw_deg":90.0,"distance_travelled_cm":20.0}
```

3. Enable the map, send one 80 cm front scan point, then confirm the occupancy grid has an obstacle:

```bash
curl -X POST http://127.0.0.1:8301/api/reset
curl -X POST http://127.0.0.1:8301/api/config \
  -H 'Content-Type: application/json' \
  -d '{"map_enabled":true,"resolution_m":0.1,"size_m":2.0}'
curl -X POST http://127.0.0.1:8301/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"ranges_m":[0.8],"angle_min_rad":0,"angle_increment_rad":0.0174532925}'
curl 'http://127.0.0.1:8301/api/state?include_map=true'
```

Expected map evidence:

```text
map_available=true and one or more map.values cells equal 100
```

## Integration Notes

`pose_tracker` already estimates 2D pose from IMU and `/cmd_vel`. This module should sit one level above it as a stable pose/map API. A Pi-side bridge can forward:

- `/cmd_vel` integration or wheel odometry to `/api/odom`
- IMU yaw corrections as `dyaw_rad` updates or a future absolute-pose endpoint
- LiDAR/ultrasonic/depth scan data to `/api/scan`

`action_move` should remain safe when SLAM is offline. The recommended control flow is:

1. Execute the existing timed movement command.
2. If SLAM is reachable, query `/api/state` before/after the motion.
3. Use the pose delta to report or calibrate how far the robot actually moved.
4. If SLAM is unreachable, keep the current duration-based fallback.

## Validation

```bash
python3 -m compileall -q slam_mapping
PYTHONPATH=slam_mapping python3 -m unittest discover -s slam_mapping/tests
```

After cloud deployment, run the strict real-car verification from the repository root:

```bash
python3 slam_mapping/real_car_verify.py \
  --action-url https://www.wangyutang.cn/action \
  --slam-url https://www.wangyutang.cn/slam
```

This command physically moves the robot: forward 20 cm, left turn 90 degrees, then forward 20 cm again. It restores the previous `action_move` settings before exiting.
