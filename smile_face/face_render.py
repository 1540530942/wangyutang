from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1024
HEIGHT = 768


@dataclass
class FaceState:
    emotion: str = "neutral"
    style: str = "mochi"
    intensity: float = 0.65
    speaking_until: float = 0
    mouth_open_until: float = 0
    blink_nonce: int = 0
    message: str = ""
    now: float = 0


EMOTIONS = {
    "neutral": {"eye_open": 0.95, "smile": 0.18, "mouth_open": 0.04, "brow": 0.0, "blush": 0.45, "sparkle": 0.25, "wobble": 0.25},
    "happy": {"eye_open": 0.78, "smile": 0.74, "mouth_open": 0.12, "brow": 0.14, "blush": 0.85, "sparkle": 0.55, "wobble": 0.55},
    "joy": {"eye_open": 1.05, "smile": 0.92, "mouth_open": 0.42, "brow": 0.22, "blush": 1.0, "sparkle": 1.0, "wobble": 0.9},
    "sad": {"eye_open": 0.58, "smile": -0.58, "mouth_open": 0.06, "brow": -0.34, "blush": 0.25, "sparkle": 0.1, "wobble": 0.15},
    "angry": {"eye_open": 0.66, "smile": -0.16, "mouth_open": 0.03, "brow": -0.68, "blush": 0.7, "sparkle": 0.25, "wobble": 0.45},
}

STYLES: dict[str, dict[str, Any]] = {
    "mochi": {"bg1": (255, 247, 224), "bg2": (246, 235, 255), "bg3": (231, 255, 246), "body": (255, 252, 239), "shadow": (236, 200, 218), "cheek": (255, 142, 185), "accent": (255, 186, 112), "ink": (86, 61, 86), "trim": (255, 255, 255), "ears": "nubs", "motif": "dango"},
    "bunny": {"bg1": (255, 239, 249), "bg2": (232, 244, 255), "bg3": (255, 246, 218), "body": (255, 245, 249), "shadow": (218, 184, 220), "cheek": (255, 135, 179), "accent": (255, 165, 205), "ink": (85, 58, 92), "trim": (255, 255, 255), "ears": "bunny", "motif": "hearts"},
    "star": {"bg1": (247, 239, 255), "bg2": (220, 247, 255), "bg3": (255, 239, 190), "body": (248, 245, 255), "shadow": (183, 193, 238), "cheek": (255, 168, 202), "accent": (255, 214, 96), "ink": (65, 63, 108), "trim": (255, 255, 245), "ears": "star", "motif": "stars"},
    "panda": {"bg1": (239, 255, 246), "bg2": (255, 247, 228), "bg3": (232, 238, 255), "body": (255, 253, 242), "shadow": (189, 203, 198), "cheek": (255, 148, 162), "accent": (125, 218, 172), "ink": (48, 61, 58), "trim": (255, 255, 255), "ears": "panda", "motif": "leaves"},
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def mix(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = clamp(t, 0.0, 1.0)
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rgba(color: tuple[int, int, int], alpha: int = 255) -> tuple[int, int, int, int]:
    return color[0], color[1], color[2], alpha


def star_points(cx: float, cy: float, outer: float, inner: float, points: int = 5) -> list[tuple[float, float]]:
    result = []
    for i in range(points * 2):
        radius = inner if i % 2 else outer
        angle = -math.pi / 2 + (i * math.pi) / points
        result.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return result


def rounded(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], radius: float, fill: tuple[int, ...], outline: tuple[int, ...] | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(tuple(round(v) for v in box), radius=round(radius), fill=fill, outline=outline, width=max(1, round(width)))


def draw_vertical_gradient(image: Image.Image, top: tuple[int, int, int], middle: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    pixels = image.load()
    width, height = image.size
    for y in range(height):
        pos = y / max(1, height - 1)
        if pos < 0.5:
            color = blend(top, middle, pos / 0.5)
        else:
            color = blend(middle, bottom, (pos - 0.5) / 0.5)
        for x in range(width):
            pixels[x, y] = color


def draw_motif(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, palette: dict[str, Any], index: int) -> None:
    fill = rgba(palette["cheek"] if index % 2 else palette["accent"], 95)
    motif = palette["motif"]
    if motif == "stars":
        draw.polygon(star_points(x, y, size, size * 0.45), fill=fill)
    elif motif == "leaves":
        draw.ellipse((x - size * 0.75, y - size * 0.35, x + size * 0.75, y + size * 0.35), fill=fill)
    elif motif == "hearts":
        draw.ellipse((x - size * 0.8, y - size * 0.8, x, y), fill=fill)
        draw.ellipse((x, y - size * 0.8, x + size * 0.8, y), fill=fill)
        draw.polygon([(x - size * 0.85, y - size * 0.25), (x + size * 0.85, y - size * 0.25), (x, y + size * 0.85)], fill=fill)
    else:
        draw.ellipse((x - size * 0.45, y - size * 0.45, x + size * 0.45, y + size * 0.45), fill=fill)


def draw_background(image: Image.Image, draw: ImageDraw.ImageDraw, palette: dict[str, Any], t: float) -> None:
    draw_vertical_gradient(image, palette["bg1"], palette["bg2"], palette["bg3"])
    for i in range(34):
        x = (i * 137 + t * 16) % (WIDTH + 80) - 40
        y = (i * 83 + math.sin(t + i) * 14) % (HEIGHT + 60) - 30
        draw_motif(draw, x, y, 8 + (i % 4) * 4, palette, i)

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (255, 255, 255, 0))
    od = ImageDraw.Draw(overlay)
    for radius, alpha in ((420, 38), (300, 44), (180, 42)):
        od.ellipse((WIDTH / 2 - radius, HEIGHT * 0.55 - radius, WIDTH / 2 + radius, HEIGHT * 0.55 + radius), fill=(255, 255, 255, alpha))
    image.alpha_composite(overlay)


def draw_accessories(draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, palette: dict[str, Any], t: float) -> None:
    ink_outline = rgba(palette["ink"], 45)
    if palette["ears"] == "bunny":
        for side in (-1, 1):
            x = cx + side * 128 * scale
            rounded(draw, (x - 28 * scale, cy - 252 * scale, x + 28 * scale, cy - 120 * scale), 28 * scale, rgba(palette["body"]), ink_outline, 4 * scale)
            rounded(draw, (x - 14 * scale, cy - 226 * scale, x + 14 * scale, cy - 140 * scale), 14 * scale, rgba(palette["cheek"], 115))
    elif palette["ears"] == "panda":
        for side in (-1, 1):
            draw.ellipse((cx + side * 145 * scale - 44 * scale, cy - 100 * scale - 44 * scale, cx + side * 145 * scale + 44 * scale, cy - 100 * scale + 44 * scale), fill=rgba(palette["ink"], 235))
    elif palette["ears"] == "star":
        for side in (-1, 1):
            draw.polygon(star_points(cx + side * 150 * scale, cy - 120 * scale + math.sin(t * 2) * 4, 34 * scale, 15 * scale), fill=rgba(palette["accent"], 235))
    else:
        for side in (-1, 1):
            draw.ellipse((cx + side * 140 * scale - 28 * scale, cy - 112 * scale - 28 * scale, cx + side * 140 * scale + 28 * scale, cy - 112 * scale + 28 * scale), fill=rgba(palette["body"]), outline=ink_outline, width=max(2, round(4 * scale)))


def draw_body(image: Image.Image, draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, palette: dict[str, Any], t: float, wobble: float) -> None:
    draw_accessories(draw, cx, cy, scale, palette, t)
    shadow = Image.new("RGBA", (WIDTH, HEIGHT), (255, 255, 255, 0))
    sd = ImageDraw.Draw(shadow)
    body_box = (cx - 190 * scale, cy - 143 * scale, cx + 190 * scale, cy + 143 * scale)
    shadow_box = (body_box[0], body_box[1] + 14 * scale, body_box[2], body_box[3] + 14 * scale)
    rounded(sd, shadow_box, 82 * scale, rgba(palette["shadow"], 95))
    image.alpha_composite(shadow)

    rounded(draw, body_box, 82 * scale, rgba(palette["trim"]), rgba(palette["ink"], 42), 4 * scale)
    inner = (body_box[0] + 5 * scale, body_box[1] + 5 * scale, body_box[2] - 5 * scale, body_box[3] - 5 * scale)
    rounded(draw, inner, 78 * scale, rgba(blend(palette["body"], palette["shadow"], 0.07), 245))


def draw_sparkles(draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, state: dict[str, float], palette: dict[str, Any], t: float) -> None:
    alpha = round(60 + state["sparkle"] * 150)
    for i in range(7):
        angle = i * 1.9 + t * 0.8
        radius = (180 + (i % 3) * 34) * scale
        x = cx + math.cos(angle) * radius
        y = cy + math.sin(angle * 0.8) * radius * 0.55
        draw.polygon(star_points(x, y, (7 + i % 3 * 3) * scale, (3 + i % 2 * 2) * scale), fill=rgba(palette["accent"], alpha))


def draw_eyes(draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, state: dict[str, float], palette: dict[str, Any], blink: float, emotion: str) -> None:
    eye_open = clamp(state["eye_open"] * (1 - blink * 0.98), 0.04, 1.08)
    eye_y = cy - 24 * scale
    for side in (-1, 1):
        x = cx + side * 82 * scale
        eye_w = 78 * scale
        eye_h = 88 * scale * eye_open
        draw.ellipse((x - eye_w * 0.42, eye_y - eye_h * 0.5, x + eye_w * 0.42, eye_y + eye_h * 0.5), fill=rgba(palette["ink"], 245))
        draw.ellipse((x - eye_w * 0.23, eye_y - eye_h * 0.29, x - eye_w * 0.01, eye_y - eye_h * 0.03), fill=(255, 255, 255, 235))
        if emotion == "joy":
            draw.polygon(star_points(x + eye_w * 0.13, eye_y - eye_h * 0.03, 9 * scale, 4 * scale), fill=rgba(palette["accent"], 230))

    for side in (-1, 1):
        x = cx + side * 82 * scale
        brow_y = cy - 92 * scale + state["brow"] * -16 * scale
        draw.line((x - side * 34 * scale, brow_y - side * state["brow"] * 18 * scale, x + side * 26 * scale, brow_y + side * state["brow"] * 18 * scale), fill=rgba(palette["ink"], 190), width=max(5, round(7 * scale)))


def draw_cheeks(draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, state: dict[str, float], palette: dict[str, Any]) -> None:
    alpha = round(70 + state["blush"] * 105)
    for side in (-1, 1):
        draw.ellipse((cx + side * 130 * scale - 34 * scale, cy + 38 * scale - 17 * scale, cx + side * 130 * scale + 34 * scale, cy + 38 * scale + 17 * scale), fill=rgba(palette["cheek"], alpha))


def draw_mouth(draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, state: dict[str, float], palette: dict[str, Any], mouth_pulse: float) -> None:
    y = cy + 66 * scale
    width = 120 * scale
    curve = state["smile"]
    open_amount = clamp(state["mouth_open"] + mouth_pulse, 0.0, 1.0)
    ink = rgba(palette["ink"], 230)
    if open_amount > 0.18:
        draw.ellipse((cx - width * 0.32, y + curve * 8 * scale - (18 + open_amount * 38) * scale / 2, cx + width * 0.32, y + curve * 8 * scale + (18 + open_amount * 38) * scale / 2), fill=ink)
        draw.ellipse((cx - width * 0.18, y + (18 + open_amount * 24) * scale - 10 * scale, cx + width * 0.18, y + (18 + open_amount * 24) * scale + 10 * scale), fill=rgba(palette["cheek"], 225))
    else:
        points = []
        for i in range(36):
            t = i / 35
            x = cx - width * 0.42 + width * 0.84 * t
            y_curve = (1 - t) * (1 - t) * y + 2 * (1 - t) * t * (y + curve * 58 * scale) + t * t * y
            points.append((x, y_curve))
        draw.line(points, fill=ink, width=max(6, round(8 * scale)), joint="curve")


def draw_message(draw: ImageDraw.ImageDraw, state: FaceState, palette: dict[str, Any]) -> None:
    if not state.message:
        return
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    text = state.message[:30]
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((WIDTH - (bbox[2] - bbox[0])) / 2, HEIGHT - 54), text, fill=rgba(palette["ink"], 145), font=font)


def draw_face(state: FaceState, frame_started: float, blink: float) -> Image.Image:
    render = dict(EMOTIONS.get(state.emotion, EMOTIONS["neutral"]))
    palette = STYLES.get(state.style, STYLES["mochi"])
    image = Image.new("RGBA", (WIDTH, HEIGHT), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    t = frame_started
    cx = WIDTH / 2
    cy = HEIGHT * 0.52
    scale = min(WIDTH / 640, HEIGHT / 520) * 1.02

    now_value = state.now or time.time()
    speaking = state.speaking_until > now_value
    forced_mouth = state.mouth_open_until > now_value
    if speaking:
        mouth_pulse = 0.18 + 0.34 * abs(math.sin(t * 13.5)) + 0.08 * abs(math.sin(t * 22))
    elif forced_mouth:
        mouth_pulse = 0.26
    else:
        mouth_pulse = 0.0

    draw_background(image, draw, palette, t)
    draw_sparkles(draw, cx, cy, scale, render, palette, t)
    draw_body(image, draw, cx, cy, scale, palette, t, render["wobble"])
    draw_eyes(draw, cx, cy, scale, render, palette, blink, state.emotion)
    draw_cheeks(draw, cx, cy, scale, render, palette)
    draw_mouth(draw, cx, cy, scale, render, palette, mouth_pulse)
    draw_message(draw, state, palette)
    return image.convert("RGB")
