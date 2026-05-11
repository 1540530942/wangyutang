# Motion Image Verification 2026-05-11

This report records the image-based verification for TurboPi base movement and camera pan servo movement.

## Result

All verified actions use before/after camera captures from `https://www.wangyutang.cn/camera/`.
The control command is considered valid only when the ROS command is published and the image difference is large.

| Action | Command | Before/After Images | Changed Pixels | Result |
| --- | --- | --- | --- | --- |
| `move_forward` | `/cmd_vel linear.x=0.35` | `movement_verify/20260511_223212/move_forward/` | `98.57%` | Passed |
| `move_backward` | `/cmd_vel linear.x=-0.35` | `movement_verify/20260511_223212/move_backward/` | `96.49%` | Passed |
| `look_left` | `servo1=1000` from center | `movement_verify/20260511_223812_servo_centered/` | `95.84%` | Passed |
| `look_right` | `servo1=1700` from center | `servo_scan/20260511_223949_right_refine/` | `92.32%` | Passed |

## Direction Check

Forward and backward were verified against the visual scene:

- Forward moved the robot toward the near foreground object. The after image became a close-up, which matches `linear.x > 0`.
- Backward moved the robot away from that foreground object. The after image returned to a wider room view, which matches `linear.x < 0`.

Camera pan was verified from a neutral center position:

- `look_left` moves PWM servo 1 to `1000` and changes the scene from center to the left room view.
- `look_right` moves PWM servo 1 to `1700` and changes the scene to the right-side view.
- `servo1=2000` was rejected because it can point too close to nearby objects and produce less useful images.

## Implementation

The skill catalog now uses:

```json
{
  "move_forward": {"linear_x": 0.35, "linear_y": 0.0, "angular_z": 0.0},
  "move_backward": {"linear_x": -0.35, "linear_y": 0.0, "angular_z": 0.0},
  "look_left": {"id": 1, "position": 1000, "axis": "pan"},
  "look_right": {"id": 1, "position": 1700, "axis": "pan"}
}
```

The executor runs ROS2 commands inside the `turbopi` container as user `ubuntu`, then publishes a stop command after every base movement.

