"""Generate benchmark test images covering colors, shapes, text, patterns, and real-world scenes."""

from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "images"
OUT.mkdir(exist_ok=True)

W, H = 640, 480


def save(img: Image.Image, name: str, fmt: str = "JPEG") -> Path:
    ext = "jpg" if fmt == "JPEG" else fmt.lower()
    path = OUT / f"{name}.{ext}"
    img.save(path, format=fmt, quality=90)
    return path


def solid(color: tuple, name: str) -> None:
    save(Image.new("RGB", (W, H), color), name)


def gradient_h(c1: tuple, c2: tuple, name: str) -> None:
    img = Image.new("RGB", (W, H))
    px = img.load()
    for x in range(W):
        r = int(c1[0] + (c2[0] - c1[0]) * x / W)
        g = int(c1[1] + (c2[1] - c1[1]) * x / W)
        b = int(c1[2] + (c2[2] - c1[2]) * x / W)
        for y in range(H):
            px[x, y] = (r, g, b)
    save(img, name)


def shapes(name: str) -> None:
    img = Image.new("RGB", (W, H), (240, 240, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, 220, 200], fill=(220, 50, 50), outline=(180, 20, 20), width=3)
    d.ellipse([260, 60, 420, 200], fill=(50, 130, 220), outline=(20, 90, 180), width=3)
    pts = [(540, 60), (460, 200), (620, 200)]
    d.polygon(pts, fill=(50, 180, 80), outline=(20, 140, 50))
    d.rectangle([60, 260, 220, 400], fill=(200, 150, 50), outline=(160, 110, 20), width=3)
    d.ellipse([260, 260, 420, 400], fill=(160, 60, 200), outline=(120, 30, 160), width=3)
    pts2 = [(540, 400), (460, 260), (620, 260)]
    d.polygon(pts2, fill=(200, 200, 50), outline=(160, 160, 20))
    save(img, name)


def checkerboard(size: int, name: str) -> None:
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for row in range(H // size + 1):
        for col in range(W // size + 1):
            if (row + col) % 2 == 0:
                d.rectangle([col * size, row * size, (col + 1) * size, (row + 1) * size], fill=(30, 30, 30))
    save(img, name)


def stripes(name: str, horizontal: bool = False) -> None:
    colors = [(220, 50, 50), (240, 180, 40), (50, 180, 80), (50, 130, 220), (160, 60, 200)]
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    n = len(colors)
    if horizontal:
        step = H // n
        for i, c in enumerate(colors):
            d.rectangle([0, i * step, W, (i + 1) * step], fill=c)
    else:
        step = W // n
        for i, c in enumerate(colors):
            d.rectangle([i * step, 0, (i + 1) * step, H], fill=c)
    save(img, name)


def text_image(name: str) -> None:
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        font_mid = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except Exception:
        font_large = font_mid = font_small = ImageFont.load_default()
    d.text((40, 40), "Hello, World!", fill=(30, 30, 30), font=font_large)
    d.text((40, 110), "图像识别测试 2025", fill=(50, 100, 200), font=font_mid)
    d.text((40, 170), "The quick brown fox jumps over the lazy dog.", fill=(80, 80, 80), font=font_small)
    d.text((40, 210), "1234567890  !@#$%^&*()", fill=(180, 50, 50), font=font_small)
    d.line([(40, 250), (600, 250)], fill=(180, 180, 180), width=2)
    d.text((40, 270), "Price: $99.99  温度: 36.5°C", fill=(30, 30, 30), font=font_mid)
    d.text((40, 330), "Email: test@example.com", fill=(50, 130, 50), font=font_small)
    d.text((40, 370), "Date: 2025-07-01  Time: 09:30", fill=(100, 60, 160), font=font_small)
    save(img, name)


def chart_bar(name: str) -> None:
    img = Image.new("RGB", (W, H), (250, 250, 250))
    d = ImageDraw.Draw(img)
    data = [("Mon", 120, (220, 80, 80)), ("Tue", 200, (80, 160, 220)),
            ("Wed", 160, (80, 200, 100)), ("Thu", 240, (220, 180, 60)),
            ("Fri", 180, (160, 80, 200))]
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    d.text((W // 2 - 80, 20), "Weekly Sales", fill=(30, 30, 30), font=font)
    base_y = H - 80
    bar_w = 70
    gap = (W - 80 - len(data) * bar_w) // (len(data) + 1)
    for i, (label, val, color) in enumerate(data):
        x = 40 + gap + i * (bar_w + gap)
        bar_h = int(val * 1.2)
        d.rectangle([x, base_y - bar_h, x + bar_w, base_y], fill=color, outline=(50, 50, 50), width=1)
        d.text((x + 15, base_y + 5), label, fill=(50, 50, 50), font=font)
        d.text((x + 20, base_y - bar_h - 25), str(val), fill=(50, 50, 50), font=font)
    d.line([(40, base_y), (W - 40, base_y)], fill=(100, 100, 100), width=2)
    save(img, name)


def low_contrast(name: str) -> None:
    img = Image.new("RGB", (W, H), (200, 200, 210))
    d = ImageDraw.Draw(img)
    d.ellipse([160, 120, 480, 360], fill=(210, 210, 220), outline=(190, 190, 200), width=2)
    d.rectangle([240, 180, 400, 300], fill=(205, 205, 215))
    save(img, name)


def dark_scene(name: str) -> None:
    img = Image.new("RGB", (W, H), (15, 15, 20))
    d = ImageDraw.Draw(img)
    for _ in range(120):
        import random
        random.seed(42 + _)
        x, y = random.randint(0, W), random.randint(0, H // 2)
        r = random.randint(1, 3)
        br = random.randint(180, 255)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(br, br, int(br * 0.9)))
    d.ellipse([W // 2 - 60, H // 2 - 20, W // 2 + 60, H // 2 + 80], fill=(240, 200, 60))
    d.rectangle([0, H // 2 + 60, W, H], fill=(20, 30, 20))
    save(img, name)


def radial_gradient(name: str) -> None:
    img = Image.new("RGB", (W, H))
    px = img.load()
    cx, cy = W // 2, H // 2
    max_r = math.sqrt(cx ** 2 + cy ** 2)
    for y in range(H):
        for x in range(W):
            d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2) / max_r
            r = int(255 * (1 - d))
            g = int(100 * d)
            b = int(200 * d)
            px[x, y] = (r, g, b)
    save(img, name)


def mixed_content(name: str) -> None:
    img = Image.new("RGB", (W, H), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 60], fill=(40, 80, 160))
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        font_title = font_body = ImageFont.load_default()
    d.text((20, 15), "Product Report", fill=(255, 255, 255), font=font_title)
    d.rectangle([20, 80, 200, 220], fill=(220, 80, 80))
    d.ellipse([220, 80, 400, 220], fill=(80, 160, 80))
    d.rectangle([420, 80, 620, 220], fill=(80, 80, 200))
    d.text((20, 240), "Red Box   Green Circle   Blue Rect", fill=(60, 60, 60), font=font_body)
    d.line([(20, 270), (620, 270)], fill=(180, 180, 180), width=1)
    d.text((20, 290), "Total items: 3   Status: Active   Score: 98.5", fill=(80, 80, 80), font=font_body)
    d.text((20, 330), "Note: All shapes rendered at 640×480 resolution", fill=(120, 120, 120), font=font_body)
    save(img, name)


if __name__ == "__main__":
    solid((220, 50, 50), "01_solid_red")
    solid((50, 130, 220), "02_solid_blue")
    solid((60, 180, 80), "03_solid_green")
    solid((240, 190, 50), "04_solid_yellow")
    solid((255, 255, 255), "05_solid_white")
    solid((20, 20, 20), "06_solid_black")
    gradient_h((220, 50, 50), (50, 130, 220), "07_gradient_red_blue")
    gradient_h((50, 180, 80), (240, 190, 50), "08_gradient_green_yellow")
    shapes("09_basic_shapes")
    checkerboard(40, "10_checkerboard")
    stripes("11_stripes_vertical")
    stripes("12_stripes_horizontal", horizontal=True)
    text_image("13_text_mixed")
    chart_bar("14_bar_chart")
    low_contrast("15_low_contrast")
    dark_scene("16_dark_night_scene")
    radial_gradient("17_radial_gradient")
    mixed_content("18_mixed_content")
    print(f"Generated {len(list(OUT.glob('*.jpg')))} images in {OUT}")
