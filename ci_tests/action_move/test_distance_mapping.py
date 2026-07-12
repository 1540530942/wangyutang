"""action_move distance-parameter execution mapping guards."""
from __future__ import annotations

import sys
from pathlib import Path

from ci_tests.golden_sessions import iter_golden_sessions


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTION_MOVE_DIR = REPO_ROOT / "action_move"
sys.path.insert(0, str(ACTION_MOVE_DIR))

import action_move_executor  # noqa: E402


def _movement_distance_expectations():
    items = []
    catalog = action_move_executor.load_catalog(ACTION_MOVE_DIR / "skill_catalog.json")
    for session in iter_golden_sessions(tier="smoke"):
        for turn in session.guard_turns("movement"):
            settings = dict(turn.get("expected_settings") or {})
            if "unit_distance_cm" not in settings:
                continue
            items.append((session.session_id, turn, catalog, settings))
    return items


def test_move_distance_setting_maps_to_duration():
    for session_id, turn, catalog, settings in _movement_distance_expectations():
        base_defaults = dict(catalog["defaults"])
        defaults = action_move_executor.merged_defaults(catalog, settings)
        duration_ms = action_move_executor.unit_duration_ms(defaults, "move")
        expected_ms = int(round(800 * (float(settings["unit_distance_cm"]) / 5.0) / float(defaults.get("sensitivity", 1.0))))
        expected_ms = max(int(base_defaults["min_move_duration_ms"]), min(int(base_defaults["max_move_duration_ms"]), expected_ms))

        assert defaults["unit_distance_cm"] == float(settings["unit_distance_cm"]), (
            f"{session_id}[{turn['turn_id']}] unit_distance_cm was not preserved"
        )
        assert duration_ms == expected_ms, (
            f"{session_id}[{turn['turn_id']}] duration mismatch: expected={expected_ms} actual={duration_ms}"
        )


def test_fifteen_cm_guard_is_not_default_ten_cm():
    catalog = action_move_executor.load_catalog(ACTION_MOVE_DIR / "skill_catalog.json")
    defaults_15 = action_move_executor.merged_defaults(catalog, {"unit_distance_cm": 15.0})
    defaults_10 = action_move_executor.merged_defaults(catalog, {"unit_distance_cm": 10.0})

    assert action_move_executor.unit_duration_ms(defaults_15, "move") == 2400
    assert action_move_executor.unit_duration_ms(defaults_10, "move") == 1600
    assert action_move_executor.unit_duration_ms(defaults_15, "move") != action_move_executor.unit_duration_ms(defaults_10, "move")
