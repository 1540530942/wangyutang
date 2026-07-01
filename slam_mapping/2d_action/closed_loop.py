from __future__ import annotations

"""Closed-loop 2D motion controller.

Combines two corrections so the *actual* robot motion converges to the target:

1. Feed-forward — :class:`MotionCalibration` pre-scales the command by the
   learned commanded↔actual ratio (``command = target / scale``).
2. Feedback — after each burst the measured actual motion is compared to the
   target; if the residual exceeds tolerance a corrective burst is issued for
   the remaining amount, up to ``max_iterations``.

The controller is transport-agnostic: it is given a ``command_fn`` that drives
the robot for a requested magnitude and a ``measure_fn`` that returns the
actual magnitude moved (from IMU / wheel odometry once the edge reports it).
This keeps the control law unit-testable without hardware and lets the same
logic run against a real car or a simulator.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from calibration import MotionCalibration, MotionKind


# Convergence tolerances: stop correcting once the residual is within these.
DEFAULT_TRANSLATE_TOL_M = 0.01   # 1 cm
DEFAULT_ROTATE_TOL_RAD = 0.035   # ~2 deg


@dataclass
class MoveResult:
    kind: MotionKind
    target: float
    total_actual: float
    error: float
    iterations: int
    converged: bool
    bursts: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": round(self.target, 5),
            "total_actual": round(self.total_actual, 5),
            "error": round(self.error, 5),
            "iterations": self.iterations,
            "converged": self.converged,
            "bursts": self.bursts,
        }


class ClosedLoopController:
    def __init__(
        self,
        *,
        command_fn: Callable[[float, MotionKind], None],
        measure_fn: Callable[[float, MotionKind], float],
        calibration: MotionCalibration | None = None,
        translate_tol_m: float = DEFAULT_TRANSLATE_TOL_M,
        rotate_tol_rad: float = DEFAULT_ROTATE_TOL_RAD,
        max_iterations: int = 3,
    ) -> None:
        self._command = command_fn
        self._measure = measure_fn
        self.calibration = calibration or MotionCalibration()
        self._translate_tol = translate_tol_m
        self._rotate_tol = rotate_tol_rad
        self._max_iterations = max(1, int(max_iterations))

    def _tolerance(self, kind: MotionKind) -> float:
        return self._translate_tol if kind == "translate" else self._rotate_tol

    def move(self, target: float, kind: MotionKind) -> MoveResult:
        """Drive ``target`` metres/radians of ``kind`` motion under closed loop."""
        tol = self._tolerance(kind)
        remaining = target
        total_actual = 0.0
        bursts: list[dict[str, Any]] = []

        for i in range(self._max_iterations):
            commanded = self.calibration.precompensate(remaining, kind)
            self._command(commanded, kind)
            actual = self._measure(commanded, kind)
            self.calibration.observe(commanded, actual, kind)

            total_actual += actual
            error = target - total_actual
            bursts.append(
                {
                    "iteration": i,
                    "remaining_before": round(remaining, 5),
                    "commanded": round(commanded, 5),
                    "actual": round(actual, 5),
                    "cumulative": round(total_actual, 5),
                    "error": round(error, 5),
                    "scale": round(self.calibration.scale(kind), 4),
                }
            )
            remaining = error
            if abs(error) <= tol:
                return MoveResult(kind, target, total_actual, error, i + 1, True, bursts)

        error = target - total_actual
        return MoveResult(kind, target, total_actual, error, self._max_iterations, abs(error) <= tol, bursts)
