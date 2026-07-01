from __future__ import annotations

"""Motion calibration model — the feed-forward half of the closed loop.

The robot is commanded open-loop (a cmd_vel burst for a duration), so a
commanded 10 cm may actually travel 12 cm. This model learns, per motion
kind, the scale factor ``k = actual / commanded`` from observed samples and
pre-compensates future commands so the *actual* motion matches the target:

    command = target / k

``k`` is tracked with an exponential moving average so it adapts to drift
(battery level, surface friction) without overreacting to a single noisy
sample. Samples and residuals are retained for inspection and RL training.
"""

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


MotionKind = Literal["translate", "rotate"]

# Below these magnitudes a sample is ignored for scale learning: tiny moves
# are dominated by start/stop transients and would corrupt the ratio.
MIN_TRANSLATE_M = 0.02
MIN_ROTATE_RAD = math.radians(3.0)
# Guard rails so one bad reading can never drive commands to absurd values.
MIN_SCALE = 0.4
MAX_SCALE = 2.5


@dataclass
class KindCalibration:
    scale: float = 1.0
    samples: int = 0
    last_ratio: float | None = None
    last_error: float | None = None
    ema_alpha: float = 0.3

    def observe(self, commanded: float, actual: float) -> None:
        if abs(commanded) < 1e-9 or abs(actual) < 1e-9:
            return
        ratio = actual / commanded
        if ratio <= 0:
            return
        ratio = max(MIN_SCALE, min(MAX_SCALE, ratio))
        if self.samples == 0:
            self.scale = ratio
        else:
            self.scale = (1 - self.ema_alpha) * self.scale + self.ema_alpha * ratio
        self.scale = max(MIN_SCALE, min(MAX_SCALE, self.scale))
        self.last_ratio = round(ratio, 4)
        self.samples += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "scale": round(self.scale, 4),
            "samples": self.samples,
            "last_ratio": self.last_ratio,
            "last_error": self.last_error,
        }


class MotionCalibration:
    """Per-kind commanded↔actual scale model with optional disk persistence."""

    def __init__(self, store_path: str | Path | None = None) -> None:
        self._store_path = Path(store_path) if store_path else None
        self._kinds: dict[str, KindCalibration] = {
            "translate": KindCalibration(),
            "rotate": KindCalibration(),
        }
        self._history: list[dict[str, Any]] = []
        self._load()

    def _min_magnitude(self, kind: MotionKind) -> float:
        return MIN_TRANSLATE_M if kind == "translate" else MIN_ROTATE_RAD

    def precompensate(self, target: float, kind: MotionKind) -> float:
        """Return the command magnitude that should yield ``target`` actual motion."""
        cal = self._kinds[kind]
        if cal.scale <= 0:
            return target
        return target / cal.scale

    def observe(self, commanded: float, actual: float, kind: MotionKind) -> dict[str, Any]:
        cal = self._kinds[kind]
        if abs(commanded) >= self._min_magnitude(kind):
            cal.observe(commanded, actual)
        cal.last_error = round(actual - commanded, 5)
        sample = {
            "kind": kind,
            "commanded": round(commanded, 5),
            "actual": round(actual, 5),
            "ratio": round(actual / commanded, 4) if abs(commanded) > 1e-9 else None,
            "scale_after": round(cal.scale, 4),
            "t": time.time(),
        }
        self._history.append(sample)
        if len(self._history) > 500:
            self._history = self._history[-500:]
        self._save()
        return sample

    def scale(self, kind: MotionKind) -> float:
        return self._kinds[kind].scale

    def snapshot(self) -> dict[str, Any]:
        return {
            "kinds": {name: cal.snapshot() for name, cal in self._kinds.items()},
            "history_len": len(self._history),
        }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._history[-limit:]

    def _load(self) -> None:
        if not self._store_path or not self._store_path.exists():
            return
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for name, cal in self._kinds.items():
            kd = data.get("kinds", {}).get(name, {})
            cal.scale = max(MIN_SCALE, min(MAX_SCALE, float(kd.get("scale", 1.0))))
            cal.samples = int(kd.get("samples", 0))
        self._history = list(data.get("history", []))[-500:]

    def _save(self) -> None:
        if not self._store_path:
            return
        payload = {
            "kinds": {name: cal.snapshot() for name, cal in self._kinds.items()},
            "history": self._history[-500:],
        }
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._store_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
