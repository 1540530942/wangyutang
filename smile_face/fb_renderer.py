from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

from face_render import FaceState, HEIGHT, WIDTH, draw_face

API_URL = "http://127.0.0.1:8096/api/state"
FB_PATH = Path("/dev/fb0")
FPS = 18


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
