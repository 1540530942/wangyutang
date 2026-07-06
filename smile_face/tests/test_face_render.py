"""Real, deterministic tests for smile_face rendering primitives + draw_face.

Run from the smile_face/ directory:  python -m pytest tests/ -q
The math helpers are checked by hand-verifiable values; draw_face performs a
REAL Pillow render and we assert the output image dimensions/mode and that
different emotions actually produce different pixels (no hard-coded badges).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from face_render import (  # noqa: E402
    EMOTIONS,
    HEIGHT,
    STYLES,
    WIDTH,
    FaceState,
    blend,
    clamp,
    draw_face,
    mix,
    star_points,
)


# ── math helpers ──────────────────────────────────────────────────────────

def test_clamp_bounds():
    assert clamp(-5, 0, 10) == 0
    assert clamp(15, 0, 10) == 10
    assert clamp(5, 0, 10) == 5


def test_mix_linear_interpolation():
    assert mix(0.0, 10.0, 0.0) == 0.0
    assert mix(0.0, 10.0, 1.0) == 10.0
    assert mix(0.0, 10.0, 0.5) == 5.0


def test_blend_colors_endpoints_and_midpoint():
    a, b = (0, 0, 0), (100, 200, 40)
    assert blend(a, b, 0.0) == (0, 0, 0)
    assert blend(a, b, 1.0) == (100, 200, 40)
    assert blend(a, b, 0.5) == (50, 100, 20)


def test_blend_clamps_out_of_range_t():
    a, b = (0, 0, 0), (100, 100, 100)
    assert blend(a, b, 2.0) == (100, 100, 100)
    assert blend(a, b, -1.0) == (0, 0, 0)


def test_star_points_count_is_double():
    pts = star_points(0, 0, 10, 5, points=5)
    assert len(pts) == 10  # points * 2
    pts6 = star_points(0, 0, 10, 5, points=6)
    assert len(pts6) == 12


# ── emotion / style tables ────────────────────────────────────────────────

def test_all_emotions_have_required_keys():
    required = {"eye_open", "smile", "mouth_open", "brow", "blush", "sparkle", "wobble"}
    for name, params in EMOTIONS.items():
        assert required <= set(params), f"{name} missing keys"


def test_styles_have_palette_keys():
    for name, pal in STYLES.items():
        for key in ("bg1", "bg2", "bg3", "body", "ink", "cheek", "ears", "motif"):
            assert key in pal, f"{name} missing {key}"


# ── real Pillow render ────────────────────────────────────────────────────

def test_draw_face_returns_rgb_image_correct_size():
    img = draw_face(FaceState(emotion="happy", style="mochi", now=1000.0), 0.0, 0.0)
    assert img.size == (WIDTH, HEIGHT)
    assert img.mode == "RGB"


def test_different_emotions_render_different_pixels():
    """happy vs sad must produce visibly different frames — proves the emotion
    actually drives the render rather than a static image."""
    happy = draw_face(FaceState(emotion="happy", now=1000.0), 0.0, 0.0)
    sad = draw_face(FaceState(emotion="sad", now=1000.0), 0.0, 0.0)
    assert list(happy.getdata()) != list(sad.getdata())


def test_unknown_emotion_falls_back_to_neutral():
    """Unknown emotion must render identically to 'neutral' (dict fallback)."""
    unknown = draw_face(FaceState(emotion="does_not_exist", now=1000.0), 0.0, 0.0)
    neutral = draw_face(FaceState(emotion="neutral", now=1000.0), 0.0, 0.0)
    assert list(unknown.getdata()) == list(neutral.getdata())


def test_unknown_style_falls_back_to_mochi():
    unknown = draw_face(FaceState(emotion="neutral", style="nope", now=1000.0), 0.0, 0.0)
    mochi = draw_face(FaceState(emotion="neutral", style="mochi", now=1000.0), 0.0, 0.0)
    assert list(unknown.getdata()) == list(mochi.getdata())
