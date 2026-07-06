# Robot Skills

Public page:

```text
https://www.wangyutang.cn/common/robot-skills
```

This page is the public workbench for robot atomic skills. It is not the runtime
executor. The executor remains in `action_move`.

Runtime path:

```text
natural language / button
  -> task command: {"action":"move_forward"}
  -> action_move/skill_catalog.json
  -> edge_action_poller.py
  -> edge_ros_controller.py
  -> ROS2 topic or SDK call
  -> hardware action
```

Cloud task command example:

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"move_forward","source":"manual"}'
```

Lowest-level ROS2 command example:

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.35, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

Stop:

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

Camera capture command example:

```bash
curl -X POST https://www.wangyutang.cn/camera/api/capture \
  -H "Content-Type: application/json" \
  -d '{"kind":"camera","mode":"single","query_gpio":26}'
```

Sonar/front-distance command example:

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"front_distance","source":"manual"}'
```

The precise sonar execution is in the edge controller and uses the Sonar SDK to
sample multiple raw millimeter readings, then returns:

```text
front_distance_estimate_cm
raw_mm_samples
confidence
```

Do not move `action_move` execution logic into `common_api_manager`. If a future
robot skill needs a public configuration API, add only the public adapter here
and keep the actual robot execution in `action_move`.
