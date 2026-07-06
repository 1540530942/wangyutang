# function_center migration

Decision:

```text
/common/function_center -> /common/robot-skills
```

`function_center` historically mixed two ideas:

| Old responsibility | New owner |
|---|---|
| Public browser entry for robot operations | `common_api_manager/static/robot_skills*` |
| Atomic skill cloud task execution | `action_move` |
| Pi-local direct debug server on port 8088 | should be removed or migrated into `action_move` tooling |
| Python convenience wrapper imported as `function_center.robot` | should be replaced by direct calls to `edge_ros_controller` or `action_move` |

Current safe migration rule:

1. Public URL names live only in `common_api_manager`.
2. Robot atomic skill definitions and execution live only in `action_move`.
3. The old `function_center` folder has been removed from the tracked project
   files. The previous `action_move/action_move_executor.py`
   `function_center_host` fallback has been replaced with direct ROS2
   `/ros_robot_controller/set_motor_speeds` publishing.

Preferred cleanup before deleting the folder:

| Item | Action |
|---|---|
| `function_center/server.py` | removed; public page is now `/common/robot-skills` |
| `function_center/robot.py` | removed; use direct edge controller calls or `action_move` |
| `function_center/ros2_command_examples.md` | removed; use `action_move/atomic_skills.md` for ROS2 examples |
| `action_move/action_move_executor.py` fallback | done: native ROS2 motor publishing |

After cleanup, run:

```bash
rg -n "function_center|/function_center|/common/function_center" .
```

The only acceptable remaining matches should be historical migration notes.
