"""Data-driven smile_face render-primitive tests from JSON input/expected.

Run from smile_face/:  python -m pytest tests/test_render_cases.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from face_render import EMOTIONS, STYLES, blend, clamp, mix, star_points  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "render_cases.json")
with open(DATA, encoding="utf-8") as fh:
    DOC = json.load(fh)


@pytest.mark.parametrize("c", DOC["clamp_cases"], ids=[f"clamp{c['input']}" for c in DOC["clamp_cases"]])
def test_clamp(c):
    assert clamp(*c["input"]) == c["expected"]


@pytest.mark.parametrize("c", DOC["mix_cases"], ids=[f"mix{c['input']}" for c in DOC["mix_cases"]])
def test_mix(c):
    assert mix(*c["input"]) == c["expected"]


@pytest.mark.parametrize("c", DOC["blend_cases"], ids=[f"blend{c['input'][2]}" for c in DOC["blend_cases"]])
def test_blend(c):
    a, b, t = c["input"]
    assert list(blend(tuple(a), tuple(b), t)) == c["expected"]


@pytest.mark.parametrize("c", DOC["star_points_count_cases"], ids=[f"star{c['input_points']}" for c in DOC["star_points_count_cases"]])
def test_star_points_count(c):
    assert len(star_points(0, 0, 10, 5, points=c["input_points"])) == c["expected_len"]


def test_emotion_table_shape():
    required = set(DOC["emotion_required_keys"])
    for name, params in EMOTIONS.items():
        assert required <= set(params), f"{name} missing keys"


def test_style_table_shape():
    for name, pal in STYLES.items():
        for key in DOC["style_required_keys"]:
            assert key in pal, f"{name} missing {key}"
