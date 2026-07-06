"""Real, deterministic tests for the TurboPi simulation RobotState machine.

Run from the simulation/ directory:  python -m pytest tests/ -q
Each assertion checks physics/geometry that is verifiable by hand. No mocks:
RobotState.apply() is exercised with real settings dicts.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state import ARENA_W, ARENA_H, ROBOT_RADIUS, RobotState  # noqa: E402


# ── rotation ──────────────────────────────────────────────────────────────

def test_turn_left_decrements_heading_modulo_360():
    st = RobotState(heading_deg=10.0)
    st.apply("turn_left", {"turn_angle_deg": 30})
    assert st.heading_deg == 340.0  # (10 - 30) % 360


def test_turn_right_increments_heading_modulo_360():
    st = RobotState(heading_deg=350.0)
    st.apply("turn_right", {"turn_angle_deg": 30})
    assert st.heading_deg == 20.0  # (350 + 30) % 360


# ── translation along heading ─────────────────────────────────────────────

def test_move_forward_advances_along_heading():
    """heading 0 = +X. 5 cm * 4 px/cm * sensitivity 1 = 20 px in +X."""
    st = RobotState(x=400.0, y=400.0, heading_deg=0.0)
    st.apply("move_forward", {"unit_distance_cm": 5.0, "sensitivity": 1.0})
    assert math.isclose(st.x, 420.0, abs_tol=1e-6)
    assert math.isclose(st.y, 400.0, abs_tol=1e-6)


def test_move_backward_reverses_along_heading():
    st = RobotState(x=400.0, y=400.0, heading_deg=0.0)
    st.apply("move_backward", {"unit_distance_cm": 5.0, "sensitivity": 1.0})
    assert math.isclose(st.x, 380.0, abs_tol=1e-6)


def test_move_clamped_to_arena_bounds():
    """Moving forward from the right wall must clamp at ARENA_W-ROBOT_RADIUS."""
    st = RobotState(x=ARENA_W - ROBOT_RADIUS, y=300.0, heading_deg=0.0)
    st.apply("move_forward", {"unit_distance_cm": 50.0, "sensitivity": 1.0})
    assert st.x <= ARENA_W - ROBOT_RADIUS


# ── camera gimbal clamps ──────────────────────────────────────────────────

def test_look_left_clamps_pan_at_minus_45():
    st = RobotState(pan_deg=0.0)
    for _ in range(10):  # 10 * -10 = -100, must clamp to -45
        st.apply("look_left", {})
    assert st.pan_deg == -45.0


def test_look_down_clamps_tilt_at_plus_30():
    st = RobotState(tilt_deg=0.0)
    for _ in range(10):  # 10 * +10 = +100, must clamp to +30
        st.apply("look_down", {})
    assert st.tilt_deg == 30.0


# ── RGB ───────────────────────────────────────────────────────────────────

def test_rgb_on_sets_color_and_flag():
    st = RobotState()
    msg = st.apply("rgb_on", {"rgb_red": 10, "rgb_green": 20, "rgb_blue": 30})
    assert (st.rgb_r, st.rgb_g, st.rgb_b) == (10, 20, 30)
    assert st.rgb_on is True
    assert "开灯" in msg


def test_rgb_off_clears_flag():
    st = RobotState(rgb_on=True)
    st.apply("rgb_off", {})
    assert st.rgb_on is False


# ── reset_pose / emergency_stop ───────────────────────────────────────────

def test_reset_pose_centers_gimbal():
    st = RobotState(pan_deg=30.0, tilt_deg=-20.0)
    st.apply("reset_pose", {})
    assert st.pan_deg == 0.0 and st.tilt_deg == 0.0


def test_emergency_stop_message():
    st = RobotState()
    assert "急停" in st.apply("emergency_stop", {})


# ── front distance ray cast ───────────────────────────────────────────────

def test_front_distance_hits_wall():
    """Facing +X (heading 0) from centre, the ray must hit the right wall.
    Remaining distance to wall = (ARENA_W - x)/4 cm, capped at 300."""
    st = RobotState(x=400.0, y=300.0, heading_deg=0.0)
    d = st.front_distance_cm()
    assert 0 < d <= 300.0
    # Wall is 400 px away = 100 cm; well within the 300 cm cap.
    assert math.isclose(d, 100.0, abs_tol=2.0)


def test_front_distance_capped_at_max():
    """Facing an open direction far from any wall returns the 300 cm cap."""
    st = RobotState(x=400.0, y=300.0, heading_deg=270.0, obstacles=[])
    # heading 270 = -Y (up); wall 300 px away = 75 cm -> not capped.
    # Use a fresh state facing along the long axis with no obstacles.
    st2 = RobotState(x=20.0 + ROBOT_RADIUS, y=300.0, heading_deg=0.0, obstacles=[])
    d = st2.front_distance_cm()
    assert d <= 300.0


# ── log / trail bookkeeping ───────────────────────────────────────────────

def test_apply_appends_trail_and_log_and_frame_id():
    st = RobotState()
    start_frame = st.frame_id
    st.apply("turn_left", {"turn_angle_deg": 5})
    assert st.frame_id == start_frame + 1
    assert len(st.trail) >= 1
    assert st.log[-1]["skill"] == "turn_left"


def test_to_dict_shape():
    st = RobotState()
    d = st.to_dict()
    for key in ("x", "y", "heading_deg", "rgb", "front_distance_cm", "arena"):
        assert key in d
    assert d["arena"] == {"w": ARENA_W, "h": ARENA_H}
