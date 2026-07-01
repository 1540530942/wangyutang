# 2d_action — 闭环 2D 运动控制

让实车的**实际**移动量收敛到**目标**量。机器人底层是开环的（`/cmd_vel` 定时发布一段），令 10 cm 可能实际走 12 cm。本模块用两层校正闭环：

1. **前馈标定**（`calibration.py`）：按运动类型学习 `scale = 实际/指令`（EMA 平滑），发指令时预补偿：`指令 = 目标 / scale`。
2. **反馈校正**（`closed_loop.py`）：每段执行后比较实测与目标，残差超容差就补发一段，直到收敛或达到 `max_iterations`。

## 结构

| 文件 | 作用 |
|---|---|
| `calibration.py` | `MotionCalibration`：per-kind 比例模型 + 采样历史（可持久化，供回溯/RL） |
| `closed_loop.py` | `ClosedLoopController.move(target, kind)`：前馈+反馈闭环，返回逐段 `bursts` 可追溯 |
| `feedback.py` | `SimulatedFeedback`（测试/dry-run）+ `ActionMoveCommander`（经 action_move 发真机指令） |
| `tests/` | 闭环收敛/标定/钳制/可追溯性单测（5 个，全过） |

## 关键集成点（下一步真机）

控制律与硬件解耦：控制器需要一个 **command_fn**（发一段运动）和一个 **measure_fn**（返回实际移动量）。

- `command_fn` 已就绪：`ActionMoveCommander` 把目标磁量转成 `settings_override`（`unit_distance_cm`/`turn_angle_deg`）发给 action_move。
- **`measure_fn` 待接**：车端 `edge_ros_controller` 目前**不上报实测位移**（只有声呐+耗时）。需让边缘订阅 **IMU（陀螺测转角）/ 轮式里程计（测距离）** 并回报实际量，再接到 `feedback.make_measure_placeholder(measured_by=...)`。在此之前可由操作者提供实测值（尺子/IMU 日志）做人工标定，控制律不变。

## 用法

```python
from closed_loop import ClosedLoopController
from feedback import ActionMoveCommander, make_measure_placeholder
from calibration import MotionCalibration

cmd = ActionMoveCommander("https://www.wangyutang.cn/action")
measure = make_measure_placeholder(measured_by=imu_reader)  # 车端 IMU 接入后
ctrl = ClosedLoopController(
    command_fn=lambda m, k: cmd.command(m, k),
    measure_fn=measure,
    calibration=MotionCalibration(store_path="data/calibration.json"),
)
result = ctrl.move(0.10, "translate")   # 目标前进 10cm，闭环校正到实际≈10cm
print(result.as_dict())                 # target/total_actual/error/bursts 全可追溯
```
