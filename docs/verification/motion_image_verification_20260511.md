# Motion Image Verification 2026-05-11

This report records the image-based verification for TurboPi base movement and camera pan servo movement.

## Result

All verified actions use before/after camera captures from `https://www.wangyutang.cn/camera/`.
The control command is considered valid only when the ROS command is published and the image difference is large.

| Action | Command | Before/After Images | Changed Pixels | Result |
| --- | --- | --- | --- | --- |
| `move_forward` | `/cmd_vel linear.x=0.35` | 2026-05-11 run, raw images pruned after review | `98.57%` | Passed |
| `move_backward` | `/cmd_vel linear.x=-0.35` | 2026-05-11 run, raw images pruned after review | `96.49%` | Passed |
| `look_left` | `servo2=1800` from center | 2026-05-11 corrected pan run, raw images pruned after review | `81.73%` | Passed |
| `look_right` | `servo2=1200` from center | 2026-05-11 corrected pan run, raw images pruned after review | `80.79%` | Passed |

## Direction Check

Forward and backward were verified against the visual scene:

- Forward moved the robot toward the near foreground object. The after image became a close-up, which matches `linear.x > 0`.
- Backward moved the robot away from that foreground object. The after image returned to a wider room view, which matches `linear.x < 0`.

Camera pan was corrected after image review. The earlier `servo1` mapping changed the vertical/pitch view, not the left/right pan view. The corrected horizontal pan axis is PWM servo 2:

- `look_left` moves PWM servo 2 to `1800` and changes the scene from center toward the room-left view.
- `look_right` moves PWM servo 2 to `1200` and changes the scene from center toward the door/right-wall view.
- PWM servo 1 is reserved for pitch/up-down calibration.

## Implementation

The skill catalog now uses:

```json
{
  "move_forward": {"linear_x": 0.35, "linear_y": 0.0, "angular_z": 0.0},
  "move_backward": {"linear_x": -0.35, "linear_y": 0.0, "angular_z": 0.0},
  "look_left": {"id": 2, "position": 1800, "axis": "pan"},
  "look_right": {"id": 2, "position": 1200, "axis": "pan"},
  "look_up": {"id": 1, "position": 1000, "axis": "tilt"},
  "look_down": {"id": 1, "position": 1700, "axis": "tilt"}
}
```

The executor runs ROS2 commands inside the `turbopi` container as user `ubuntu`, then publishes a stop command after every base movement.
