"""Real, deterministic tests for PoseEstimator dead-reckoning + IMU fusion.

Run from the pose_tracker/ directory:  python -m pytest tests/ -q
Every case asserts against math that can be verified by hand — no fabricated
fixtures. The cmd_vel tests inject _last_vel_t manually so the integration
step uses a known dt instead of wall-clock time.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pose_estimator import PoseEstimator, PosePoint  # noqa: E402


# ── IMU yaw extraction ────────────────────────────────────────────────────

def test_imu_quaternion_90deg_yaw():
    """A pure +90° rotation about Z is qz=qw=sin/cos(45°)=0.70710678.
    atan2(2*qw*qz, 1-2*qz^2) must recover +pi/2."""
    est = PoseEstimator()
    s = math.sqrt(0.5)  # 0.70710678
    est.update_imu(0.0, 0.0, s, s)
    assert math.isclose(est.yaw, math.pi / 2, abs_tol=1e-9)
    assert est.snapshot()["yaw_deg"] == 90.0


def test_imu_identity_quaternion_zero_yaw():
    """Identity quaternion (0,0,0,1) => yaw 0 and imu_active flips True."""
    est = PoseEstimator()
    assert est._imu_active is False
    est.update_imu(0.0, 0.0, 0.0, 1.0)
    assert math.isclose(est.yaw, 0.0, abs_tol=1e-12)
    assert est.snapshot()["imu_active"] is True


# ── cmd_vel dead-reckoning ────────────────────────────────────────────────

def test_cmd_vel_forward_integration_known_dt():
    """First call only latches velocity; second call integrates prev velocity
    over dt. With yaw=0, vx=0.5 m/s, dt=0.2s => x advances 0.1 m."""
    est = PoseEstimator()
    est.update_cmd_vel(0.5, 0.0, 0.0)          # latch 0.5 m/s forward
    est._last_vel_t -= 0.2                       # force dt ~= 0.2 s
    est.update_cmd_vel(0.5, 0.0, 0.0)           # integrate previous velocity
    # dt carries a few microseconds of real wall-clock on top of the injected
    # 0.2s, so allow sub-millimetre slack rather than bit-exactness.
    assert math.isclose(est.x, 0.1, abs_tol=1e-3)
    assert math.isclose(est.y, 0.0, abs_tol=1e-6)


def test_cmd_vel_angular_integrates_yaw_without_imu():
    """Without IMU, angular_z integrates into yaw. w=1.0 rad/s, dt=0.3s
    => yaw += 0.3 rad."""
    est = PoseEstimator()
    est.update_cmd_vel(0.0, 0.0, 1.0)
    est._last_vel_t -= 0.3
    est.update_cmd_vel(0.0, 0.0, 1.0)
    # A few microseconds of real elapsed time ride on top of the injected 0.3s.
    assert math.isclose(est.yaw, 0.3, abs_tol=1e-3)


def test_cmd_vel_dt_clamped_to_half_second():
    """dt is clamped at 0.5s to prevent huge jumps after a stall. vx=1 m/s,
    injected gap 5s => x must be 0.5 m (clamped), not 5 m."""
    est = PoseEstimator()
    est.update_cmd_vel(1.0, 0.0, 0.0)
    est._last_vel_t -= 5.0
    est.update_cmd_vel(1.0, 0.0, 0.0)
    assert math.isclose(est.x, 0.5, abs_tol=1e-6)


# ── snapshot / describe / reset ───────────────────────────────────────────

def test_snapshot_distance_and_describe_at_origin():
    est = PoseEstimator()
    snap = est.snapshot()
    assert snap["dist_cm"] == 0.0
    assert "原地静止" in snap["description"]


def test_reset_clears_state_and_trail():
    est = PoseEstimator()
    est.update_cmd_vel(0.5, 0.0, 0.0)
    est._last_vel_t -= 0.2
    est.update_cmd_vel(0.5, 0.0, 0.0)
    assert est.x != 0.0
    est.reset()
    assert (est.x, est.y, est.yaw) == (0.0, 0.0, 0.0)
    assert len(est.trail) == 1
    assert isinstance(est.trail[0], PosePoint)


def test_trail_capped_at_max():
    est = PoseEstimator()
    for _ in range(PoseEstimator.MAX_TRAIL + 50):
        est.update_imu(0.0, 0.0, 0.0, 1.0)
    assert len(est.trail) <= PoseEstimator.MAX_TRAIL
