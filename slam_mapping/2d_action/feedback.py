from __future__ import annotations

"""Command + measurement adapters for the closed-loop controller.

``ClosedLoopController`` needs two things: a way to *command* a motion burst
and a way to *measure* how far the robot actually moved. This module provides:

- ``SimulatedFeedback`` — a deterministic model (true scale + noise + backlash)
  used by the unit tests and for dry-runs without hardware.
- ``ActionMoveCommander`` — issues a real motion burst through the action_move
  queue (the same endpoint the /slam/ control buttons use).

The *measurement* side for the real car is intentionally a single injection
point (``measure_fn``): once the edge controller publishes IMU / wheel-odometry
actuals, wire that reading here. Until then the operator can supply measured
values (ruler / IMU log) to calibrate — the control law is identical.
"""

import json
import math
import random
import urllib.request
from typing import Any, Callable

from calibration import MotionKind


class SimulatedFeedback:
    """Ground-truth-with-noise model for tests and dry-runs.

    ``true_scale`` is the real commanded→actual ratio the controller must learn
    (e.g. 1.2 means a commanded 10 cm really travels 12 cm).
    """

    def __init__(
        self,
        *,
        true_scale_translate: float = 1.2,
        true_scale_rotate: float = 0.9,
        noise: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self._scale = {"translate": true_scale_translate, "rotate": true_scale_rotate}
        self._noise = noise
        self._rng = random.Random(seed)

    def command(self, commanded: float, kind: MotionKind) -> None:  # noqa: D401 - no-op in sim
        return None

    def measure(self, commanded: float, kind: MotionKind) -> float:
        actual = commanded * self._scale[kind]
        if self._noise:
            actual += self._rng.uniform(-self._noise, self._noise) * abs(commanded)
        return actual


# Maps a (kind, sign) to the action_move skill that produces it.
_TRANSLATE_SKILL = {1: "move_forward", -1: "move_backward"}
_ROTATE_SKILL = {1: "turn_left", -1: "turn_right"}


class ActionMoveCommander:
    """Issues a single motion burst of a requested magnitude via action_move.

    Translation magnitude is in metres, rotation in radians; both are converted
    to the per-command ``settings_override`` (unit_distance_cm / turn_angle_deg)
    that action_move now honours.
    """

    def __init__(
        self,
        action_base: str = "https://www.wangyutang.cn/action",
        *,
        source: str = "2d_action-closed-loop",
        timeout: float = 40.0,
    ) -> None:
        self._base = action_base.rstrip("/")
        self._source = source
        self._timeout = timeout

    def command(self, commanded: float, kind: MotionKind) -> dict[str, Any]:
        sign = 1 if commanded >= 0 else -1
        magnitude = abs(commanded)
        if kind == "translate":
            skill = _TRANSLATE_SKILL[sign]
            override = {"unit_distance_cm": round(magnitude * 100.0, 2)}
        else:
            skill = _ROTATE_SKILL[sign]
            override = {"turn_angle_deg": round(math.degrees(magnitude), 2)}
        body = {"action": skill, "source": self._source, "ttl_seconds": 30, "settings_override": override}
        req = urllib.request.Request(
            f"{self._base}/api/tasks",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode())


class SlamPoseFeedback:
    """Command via action_move; measure actual motion from the slam pose delta.

    slam integrates the edge's IMU-measured actuals when the edge reports them
    (else commanded), so the pose delta between before/after a burst is the
    real executed motion. This closes the loop fully automatically once the
    edge emits ``[IMU] actual_*`` — no separate measurement wiring needed.

    Stateful to fit the controller's command-then-measure call order: ``command``
    snapshots the pre-move pose and issues the burst; ``measure`` waits for the
    pose to advance and returns the achieved magnitude.
    """

    def __init__(
        self,
        commander: "ActionMoveCommander",
        *,
        slam_base: str = "https://www.wangyutang.cn/slam",
        settle_timeout: float = 8.0,
        poll_interval: float = 0.3,
    ) -> None:
        self._commander = commander
        self._slam = slam_base.rstrip("/")
        self._settle_timeout = settle_timeout
        self._poll = poll_interval
        self._before: dict[str, float] = {}

    def _pose(self) -> dict[str, float]:
        with urllib.request.urlopen(self._slam + "/api/state", timeout=8) as resp:
            return json.loads(resp.read().decode()).get("pose", {})

    def command(self, commanded: float, kind: MotionKind) -> None:
        self._before = self._pose()
        self._commander.command(commanded, kind)

    def measure(self, commanded: float, kind: MotionKind) -> float:
        import math as _math
        import time as _time

        before = self._before or self._pose()
        deadline = _time.time() + self._settle_timeout
        last = before
        while _time.time() < deadline:
            _time.sleep(self._poll)
            cur = self._pose()
            if cur.get("updated_at") != before.get("updated_at"):
                last = cur
                break
            last = cur
        if kind == "translate":
            dx = float(last.get("x_m", 0)) - float(before.get("x_m", 0))
            dy = float(last.get("y_m", 0)) - float(before.get("y_m", 0))
            magnitude = _math.hypot(dx, dy)
            return magnitude if commanded >= 0 else -magnitude
        dyaw = _math.radians(float(last.get("yaw_deg", 0)) - float(before.get("yaw_deg", 0)))
        return dyaw


def make_measure_placeholder(measured_by: Callable[[float, MotionKind], float] | None = None):
    """Return a ``measure_fn`` for the controller.

    Pass ``measured_by`` once the edge reports IMU / odometry actuals. Without
    it, this raises so a real closed-loop run cannot silently trust commanded
    values as if they were measured — the caller must supply a real measurement
    source (or use ``SimulatedFeedback`` for dry-runs).
    """

    if measured_by is not None:
        return measured_by

    def _unavailable(commanded: float, kind: MotionKind) -> float:
        raise RuntimeError(
            "no actual-motion measurement source wired: the edge controller does "
            "not yet publish IMU/odometry. Provide measured_by=... or use "
            "SimulatedFeedback for a dry-run."
        )

    return _unavailable
