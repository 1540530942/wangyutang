from __future__ import annotations

import json
import math
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

API_URL = "http://127.0.0.1:8096/api/state"
FB_PATH = Path("/dev/fb0")
WIDTH = 1024
HEIGHT = 768
FPS = 18


@dataclass
class FaceState:
    emotion: str = "neutral"
    intensity: float = 0.65
    speaking_until: float = 0
    mouth_open_until: float = 0
    blink_nonce: int = 0
    message: str = ""
    now: float = 0


PRESETS = {
    "neutral": {"open": 0.92, "brow": 0.0, "mouth": 0.05, "warm": 0.38, "glow": 0.8},
    "happy": {"open": 0.82, "brow": 0.10, "mouth": 0.58, "warm": 0.72, "glow": 0.95},
    "joy": {"open": 1.0, "brow": 0.16, "mouth": 0.86, "warm": 1.0, "glow": 1.0},
    "sad": {"open": 0.62, "brow": -0.26, "mouth": -0.62, "warm": 0.12, "glow": 0.5},
    "angry": {"open": 0.46, "brow": -0.58, "mouth": -0.08, "warm": 0.04, "glow": 0.9},
}


def read_state(previous: FaceState) -> FaceState:
    try:
        with urllib.request.urlopen(API_URL, timeout=0.25) as response:
            data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return FaceState(
            emotion=str(data.get("emotion", previous.emotion)),
            intensity=float(data.get("intensity", previous.intensity)),
            speaking_until=float(data.get("speaking_until", 0)),
            mouth_open_until=float(data.get("mouth_open_until", 0)),
            blink_nonce=int(data.get("blink_nonce", previous.blink_nonce)),
            message=str(data.get("message", "")),
            now=float(data.get("now", time.time())),
        )
    except Exception:
        previous.now = time.time()
        return previous


def blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rgb565_bytes(image: Image.Image) -> bytes:
    pixels = image.convert("RGB").tobytes()
    out = bytearray(WIDTH * HEIGHT * 2)
    j = 0
    for i in range(0, len(pixels), 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j] = value & 0xFF
        out[j + 1] = value >> 8
        j += 2
    return bytes(out)


def rounded(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], radius: float, fill: tuple[int, int, int], outline: tuple[int, int, int] | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(tuple(round(v) for v in box), radius=round(radius), fill=fill, outline=outline, width=width)


def eye(draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, side: int, openness: float, brow: float, accent: tuple[int, int, int], t: float) -> None:
    eye_w = 170 * scale
    eye_h = 138 * scale
    visible_h = max(8 * scale, eye_h * openness)
    box = (cx - eye_w / 2, cy - eye_h / 2, cx + eye_w / 2, cy + eye_h / 2)
    draw.ellipse(box, fill=(2, 13, 15), outline=blend(accent, (255, 255, 255), 0.18), width=max(2, round(4 * scale)))

    iris_box = (cx - eye_w * 0.31, cy - visible_h * 0.36, cx + eye_w * 0.31, cy + visible_h * 0.36)
    draw.ellipse(iris_box, fill=blend(accent, (10, 55, 62), 0.34))
    draw.ellipse((cx - eye_w * 0.12, cy - visible_h * 0.16, cx + eye_w * 0.12, cy + visible_h * 0.16), fill=(0, 5, 6))
    draw.ellipse((cx - eye_w * 0.18, cy - visible_h * 0.24, cx - eye_w * 0.08, cy - visible_h * 0.10), fill=(235, 255, 250))

    lid_h = eye_h * (1 - openness) * 0.55
    if lid_h > 1:
        rounded(draw, (cx - eye_w * 0.47, cy - eye_h * 0.54, cx + eye_w * 0.47, cy - eye_h * 0.54 + lid_h), 18 * scale, (2, 10, 12))
        rounded(draw, (cx - eye_w * 0.47, cy + eye_h * 0.54 - lid_h, cx + eye_w * 0.47, cy + eye_h * 0.54), 18 * scale, (2, 10, 12))

    brow_y = cy - 100 * scale - abs(brow) * 10 * scale
    tilt = brow * side * 42 * scale
    draw.line((cx - side * 58 * scale, brow_y - tilt, cx + side * 44 * scale, brow_y + tilt), fill=accent, width=max(5, round(10 * scale)))


def draw_face(state: FaceState, frame_started: float, blink: float) -> Image.Image:
    preset = PRESETS.get(state.emotion, PRESETS["neutral"])
    intensity = max(0.0, min(1.0, state.intensity))
    cyan = (96, 244, 255)
    amber = (255, 185, 84)
    red = (255, 82, 64)
    blue = (105, 148, 255)
    accent = blend(cyan, amber, preset["warm"] * (0.5 + intensity * 0.55))
    if state.emotion == "angry":
        accent = blend(accent, red, 0.65)
    if state.emotion == "sad":
        accent = blend(accent, blue, 0.38)

    image = Image.new("RGB", (WIDTH, HEIGHT), (1, 4, 5))
    glow = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((180, 205, 844, 690), fill=tuple(max(0, min(255, round(c * 0.16 * preset["glow"]))) for c in accent))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    image = Image.blend(image, glow, 0.85)
    draw = ImageDraw.Draw(image)

    for y in range(0, HEIGHT, 6):
        shade = 7 + int(9 * math.sin(y * 0.03 + frame_started * 0.7))
        draw.line((0, y, WIDTH, y), fill=(shade, shade + 5, shade + 6))

    scale = 1.2
    cx = WIDTH / 2
    cy = HEIGHT / 2 + 8
    panel = (cx - 315, cy - 185, cx + 315, cy + 180)
    rounded(draw, panel, 52, (4, 16, 18), outline=blend(accent, (255, 255, 255), 0.08), width=3)
    rounded(draw, (panel[0] + 30, panel[1] + 22, panel[2] - 30, panel[1] + 46), 12, blend((5, 18, 20), accent, 0.12))

    openness = max(0.03, preset["open"] * (1 - blink * 0.97))
    eye(draw, cx - 142, cy - 38, scale, -1, openness, preset["brow"], accent, frame_started)
    eye(draw, cx + 142, cy - 38, scale, 1, openness, preset["brow"], accent, frame_started)

    now_value = state.now or time.time()
    speaking = state.speaking_until > now_value
    mouth_forced = state.mouth_open_until > now_value
    speech = 0.0
    if speaking:
        speech = 0.18 + 0.26 * abs(math.sin(frame_started * 12.0)) + 0.08 * abs(math.sin(frame_started * 21.0))
    elif mouth_forced:
        speech = 0.28

    mouth_w = 276
    mouth_h = 78
    mx0 = cx - mouth_w / 2
    my0 = cy + 104
    rounded(draw, (mx0, my0, mx0 + mouth_w, my0 + mouth_h), 20, (1, 8, 9), outline=blend(accent, (255, 255, 255), 0.04), width=2)
    curve = preset["mouth"]
    gap = 10 + 38 * max(0, curve) + 70 * speech
    base = my0 + mouth_h * 0.48
    left = mx0 + 42
    right = mx0 + mouth_w - 42
    mid_y = base + curve * 32
    mouth_points = [(left, base - gap * 0.25), (cx, mid_y + gap), (right, base - gap * 0.25), (cx, mid_y + gap * 0.22)]
    draw.polygon(mouth_points, fill=blend(accent, (255, 255, 255), 0.08))

    if state.message:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        except Exception:
            font = ImageFont.load_default()
        text = state.message[:28]
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((WIDTH - (bbox[2] - bbox[0])) / 2, HEIGHT - 54), text, fill=blend(accent, (255, 255, 255), 0.35), font=font)

    return image


def main() -> None:
    state = FaceState(now=time.time())
    next_blink = time.monotonic() + 2
    blink_started = -999.0
    blink_duration = 0.14
    last_nonce = 0

    with FB_PATH.open("wb", buffering=0) as fb:
        while True:
            started = time.monotonic()
            state = read_state(state)
            if state.blink_nonce != last_nonce:
                last_nonce = state.blink_nonce
                blink_started = started
            if started >= next_blink:
                blink_started = started
                next_blink = started + 2.2 + (started % 3.7)
            blink_age = started - blink_started
            blink = math.sin((blink_age / blink_duration) * math.pi) if 0 <= blink_age <= blink_duration else 0
            frame = draw_face(state, started, blink)
            fb.seek(0)
            fb.write(rgb565_bytes(frame))
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, (1 / FPS) - elapsed))


if __name__ == "__main__":
    main()
