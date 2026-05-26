from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from PyQt5.QtCore import QPointF, QRectF, QTimer, Qt
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt5.QtWidgets import QApplication, QWidget

API_URL = os.getenv("FACE_API_URL", "https://www.wangyutang.cn/face/api/state")
FPS_MS = 33


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
    "neutral": {"open": 0.95, "smile": 0.18, "mouth": 0.04, "brow": 0.0, "blush": 0.45, "sparkle": 0.25},
    "happy": {"open": 0.78, "smile": 0.74, "mouth": 0.12, "brow": 0.14, "blush": 0.85, "sparkle": 0.55},
    "joy": {"open": 1.05, "smile": 0.92, "mouth": 0.42, "brow": 0.22, "blush": 1.0, "sparkle": 1.0},
    "sad": {"open": 0.58, "smile": -0.58, "mouth": 0.06, "brow": -0.34, "blush": 0.25, "sparkle": 0.1},
    "angry": {"open": 0.66, "smile": -0.16, "mouth": 0.03, "brow": -0.68, "blush": 0.7, "sparkle": 0.25},
}

STYLES = {
    "mochi": {"bg1": (255, 247, 224), "bg2": (246, 235, 255), "bg3": (231, 255, 246), "body": (255, 252, 239), "shadow": (236, 200, 218), "cheek": (255, 142, 185), "accent": (255, 186, 112), "ink": (86, 61, 86), "ears": "nubs"},
    "bunny": {"bg1": (255, 239, 249), "bg2": (232, 244, 255), "bg3": (255, 246, 218), "body": (255, 245, 249), "shadow": (218, 184, 220), "cheek": (255, 135, 179), "accent": (255, 165, 205), "ink": (85, 58, 92), "ears": "bunny"},
    "star": {"bg1": (247, 239, 255), "bg2": (220, 247, 255), "bg3": (255, 239, 190), "body": (248, 245, 255), "shadow": (183, 193, 238), "cheek": (255, 168, 202), "accent": (255, 214, 96), "ink": (65, 63, 108), "ears": "star"},
    "panda": {"bg1": (239, 255, 246), "bg2": (255, 247, 228), "bg3": (232, 238, 255), "body": (255, 253, 242), "shadow": (189, 203, 198), "cheek": (255, 148, 162), "accent": (125, 218, 172), "ink": (48, 61, 58), "ears": "panda"},
}


def mix(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def qcolor(rgb: tuple[int, int, int], alpha: int = 255) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], alpha)


def blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


class FaceWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Smile Face Cute LCD")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setCursor(Qt.BlankCursor)
        self.state = FaceState(now=time.time())
        self.render = dict(EMOTIONS["neutral"])
        self.last_poll = 0.0
        self.last_nonce = 0
        self.next_blink = time.monotonic() + 1.8
        self.blink_started = -999.0
        self.blink_duration = 0.14
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(FPS_MS)

    def tick(self) -> None:
        now = time.monotonic()
        if now - self.last_poll > 0.35:
            self.poll_state()
            self.last_poll = now
        if self.state.blink_nonce != self.last_nonce:
            self.last_nonce = self.state.blink_nonce
            self.blink_started = now
        if now >= self.next_blink:
            self.blink_started = now
            self.next_blink = now + 2.0 + (now % 4.0)
        self.update()

    def poll_state(self) -> None:
        try:
            with urllib.request.urlopen(API_URL, timeout=0.2) as response:
                data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
            self.state = FaceState(
                emotion=str(data.get("emotion", self.state.emotion)),
                style=str(data.get("style", self.state.style)),
                intensity=float(data.get("intensity", self.state.intensity)),
                speaking_until=float(data.get("speaking_until", 0)),
                mouth_open_until=float(data.get("mouth_open_until", 0)),
                blink_nonce=int(data.get("blink_nonce", self.state.blink_nonce)),
                message=str(data.get("message", "")),
                now=float(data.get("now", time.time())),
            )
        except Exception:
            self.state.now = time.time()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        t = time.monotonic()
        target = EMOTIONS.get(self.state.emotion, EMOTIONS["neutral"])
        for key, value in target.items():
            self.render[key] = mix(self.render[key], value, 0.095)
        palette = STYLES.get(self.state.style, STYLES["mochi"])
        self.draw_background(painter, w, h, palette, t)
        scale = min(w / 640, h / 520) * 1.06
        cx, cy = w / 2, h * 0.52
        self.draw_sparkles(painter, cx, cy, scale, palette, t)
        self.draw_body(painter, cx, cy, scale, palette, t)
        blink_age = t - self.blink_started
        blink = math.sin((blink_age / self.blink_duration) * math.pi) if 0 <= blink_age <= self.blink_duration else 0
        self.draw_eyes(painter, cx, cy, scale, palette, blink)
        self.draw_cheeks(painter, cx, cy, scale, palette)
        self.draw_mouth(painter, cx, cy, scale, palette, t)
        self.draw_message(painter, w, h, palette)

    def draw_background(self, painter: QPainter, w: int, h: int, p: dict[str, Any], t: float) -> None:
        gradient = QLinearGradient(0, 0, w, h)
        gradient.setColorAt(0, qcolor(p["bg1"]))
        gradient.setColorAt(0.5, qcolor(p["bg2"]))
        gradient.setColorAt(1, qcolor(p["bg3"]))
        painter.fillRect(0, 0, w, h, gradient)
        painter.setPen(Qt.NoPen)
        for i in range(30):
            x = (i * 137 + t * 16) % (w + 80) - 40
            y = (i * 83 + math.sin(t + i) * 12) % (h + 60) - 30
            painter.setBrush(qcolor(p["cheek"] if i % 2 else p["accent"], 70))
            painter.drawEllipse(QPointF(x, y), 5 + (i % 4) * 3, 5 + (i % 4) * 3)
        glow = QRadialGradient(QPointF(w / 2, h * 0.55), max(w, h) * 0.52)
        glow.setColorAt(0, QColor(255, 255, 255, 118))
        glow.setColorAt(1, QColor(255, 255, 255, 0))
        painter.fillRect(0, 0, w, h, glow)

    def draw_body(self, painter: QPainter, cx: float, cy: float, s: float, p: dict[str, Any], t: float) -> None:
        self.draw_accessories(painter, cx, cy, s, p, t)
        rect = QRectF(cx - 190 * s, cy - 143 * s, 380 * s, 286 * s)
        painter.setPen(QPen(qcolor(p["ink"], 42), max(3, round(4 * s))))
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, QColor(255, 255, 255))
        gradient.setColorAt(0.56, qcolor(p["body"]))
        gradient.setColorAt(1, qcolor(blend(p["body"], p["shadow"], 0.18)))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 82 * s, 82 * s)

    def draw_accessories(self, painter: QPainter, cx: float, cy: float, s: float, p: dict[str, Any], t: float) -> None:
        painter.setPen(QPen(qcolor(p["ink"], 38), max(2, round(3 * s))))
        painter.setBrush(qcolor(p["body"]))
        if p["ears"] == "bunny":
            for side in (-1, 1):
                painter.save()
                painter.translate(cx + side * 126 * s, cy - 142 * s)
                painter.rotate(side * 11)
                painter.drawRoundedRect(QRectF(-28 * s, -112 * s, 56 * s, 132 * s), 28 * s, 28 * s)
                painter.setBrush(qcolor(p["cheek"], 90))
                painter.drawRoundedRect(QRectF(-13 * s, -84 * s, 26 * s, 84 * s), 13 * s, 13 * s)
                painter.restore()
        elif p["ears"] == "panda":
            painter.setBrush(qcolor(p["ink"], 235))
            for side in (-1, 1):
                painter.drawEllipse(QPointF(cx + side * 145 * s, cy - 100 * s), 44 * s, 44 * s)
        else:
            for side in (-1, 1):
                painter.drawEllipse(QPointF(cx + side * 145 * s, cy - 108 * s), 30 * s, 30 * s)

    def draw_sparkles(self, painter: QPainter, cx: float, cy: float, s: float, p: dict[str, Any], t: float) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor(p["accent"], round(110 + self.render["sparkle"] * 95)))
        for i in range(7):
            a = i * 1.9 + t * 0.8
            painter.drawEllipse(QPointF(cx + math.cos(a) * (185 + i * 8) * s, cy + math.sin(a * 0.8) * 95 * s), (5 + i % 3 * 2) * s, (5 + i % 3 * 2) * s)

    def draw_eyes(self, painter: QPainter, cx: float, cy: float, s: float, p: dict[str, Any], blink: float) -> None:
        eye_open = max(0.04, self.render["open"] * (1 - blink * 0.98))
        ink = qcolor(p["ink"], 245)
        for side in (-1, 1):
            painter.save()
            painter.translate(cx + side * 82 * s, cy - 24 * s)
            painter.rotate(side * self.render["brow"] * 8)
            painter.setBrush(ink)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(-34 * s, -42 * s * eye_open, 68 * s, 84 * s * eye_open))
            painter.setBrush(QColor(255, 255, 255, 235))
            painter.drawEllipse(QRectF(-18 * s, -28 * s * eye_open, 16 * s, max(4, 18 * s * eye_open)))
            if self.state.emotion == "joy":
                painter.setBrush(qcolor(p["accent"], 235))
                painter.drawEllipse(QPointF(12 * s, -4 * s), 8 * s, 8 * s)
            painter.restore()
        painter.setPen(QPen(qcolor(p["ink"], 188), max(5, round(7 * s)), Qt.SolidLine, Qt.RoundCap))
        for side in (-1, 1):
            x = cx + side * 82 * s
            y = cy - 94 * s
            painter.drawLine(QPointF(x - side * 30 * s, y - side * self.render["brow"] * 18 * s), QPointF(x + side * 24 * s, y + side * self.render["brow"] * 18 * s))

    def draw_cheeks(self, painter: QPainter, cx: float, cy: float, s: float, p: dict[str, Any]) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor(p["cheek"], round(70 + self.render["blush"] * 105)))
        for side in (-1, 1):
            painter.drawEllipse(QRectF(cx + side * 130 * s - 34 * s, cy + 38 * s - 17 * s, 68 * s, 34 * s))

    def draw_mouth(self, painter: QPainter, cx: float, cy: float, s: float, p: dict[str, Any], t: float) -> None:
        speaking = self.state.speaking_until > (self.state.now or time.time())
        forced = self.state.mouth_open_until > (self.state.now or time.time())
        pulse = 0.0
        if speaking:
            pulse = 0.18 + 0.34 * abs(math.sin(t * 13.5)) + 0.08 * abs(math.sin(t * 22))
        elif forced:
            pulse = 0.26
        y = cy + 66 * s
        open_amount = max(0.0, min(1.0, self.render["mouth"] + pulse))
        painter.setPen(QPen(qcolor(p["ink"], 225), max(6, round(8 * s)), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        if open_amount > 0.18:
            painter.setPen(Qt.NoPen)
            painter.setBrush(qcolor(p["ink"], 230))
            painter.drawEllipse(QRectF(cx - 38 * s, y - 12 * s, 76 * s, (34 + open_amount * 54) * s))
            painter.setBrush(qcolor(p["cheek"], 220))
            painter.drawEllipse(QRectF(cx - 24 * s, y + (20 + open_amount * 26) * s, 48 * s, 18 * s))
        else:
            path = QPainterPath(QPointF(cx - 52 * s, y))
            path.quadTo(QPointF(cx, y + self.render["smile"] * 58 * s), QPointF(cx + 52 * s, y))
            painter.drawPath(path)

    def draw_message(self, painter: QPainter, w: int, h: int, p: dict[str, Any]) -> None:
        if not self.state.message:
            return
        painter.setPen(qcolor(p["ink"], 135))
        painter.setFont(QFont("DejaVu Sans", 20))
        painter.drawText(QRectF(0, h - 64, w, 40), Qt.AlignCenter, self.state.message[:30])

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Escape, Qt.Key_Q):
            self.close()


def main() -> None:
    app = QApplication(sys.argv)
    window = FaceWindow()
    window.showFullScreen()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
