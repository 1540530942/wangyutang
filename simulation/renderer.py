"""服务端摄像头视图渲染器（Pillow）。

生成机器人第一人称视角图像：
- 透视地板（瓷砖格纹）
- 远处墙壁
- 障碍物（箱子投影）
- 摄像头 pan/tilt 偏移视角
- 左上角状态叠加（距离、RGB、时间戳）
"""
from __future__ import annotations

import io
import math
import time

from PIL import Image, ImageDraw, ImageFont

# 输出尺寸
W, H = 640, 480
HALF_W, HALF_H = W // 2, H // 2

# 颜色
SKY = (60, 70, 90)
WALL_FAR = (100, 110, 130)
WALL_MID = (130, 140, 160)
FLOOR_A = (180, 175, 165)
FLOOR_B = (160, 155, 145)
BOX_FACE = (180, 120, 60)
BOX_TOP = (210, 160, 90)
BOX_SIDE = (140, 90, 40)


def _perspective_y(world_z: float, horizon_y: int, fov_scale: float = 200.0) -> int:
    if world_z <= 0.01:
        world_z = 0.01
    screen_y = horizon_y + int(fov_scale / world_z)
    return min(H, max(0, screen_y))


def _tile_shade(col: int, row: int) -> tuple[int, int, int]:
    return FLOOR_A if (col + row) % 2 == 0 else FLOOR_B


def render_camera_view(
    front_distance_cm: float,
    pan_deg: float,
    tilt_deg: float,
    rgb_on: bool,
    rgb_r: int,
    rgb_g: int,
    rgb_b: int,
    obstacles_screen: list[dict],  # 障碍物在画面中的投影信息
) -> bytes:
    """返回 JPEG bytes。"""
    img = Image.new("RGB", (W, H), SKY)
    draw = ImageDraw.Draw(img)

    horizon_y = HALF_H + int(tilt_deg * 3)  # tilt 影响地平线

    # ── 天花板 ──────────────────────────────────────────────────────────
    draw.rectangle([(0, 0), (W, horizon_y)], fill=SKY)

    # ── 地板透视格纹 ─────────────────────────────────────────────────────
    tile_size_world = 30.0
    num_cols = 16
    num_rows = 20
    col_w = W / num_cols
    for row in range(num_rows):
        z_near = (row + 1) * tile_size_world * 0.5
        z_far = (row + 2) * tile_size_world * 0.5
        y_near = _perspective_y(z_near, horizon_y)
        y_far = _perspective_y(z_far, horizon_y)
        if y_near >= H:
            break
        for col in range(num_cols):
            x_left = int(col * col_w)
            x_right = int((col + 1) * col_w)
            shade = _tile_shade(col, row)
            draw.polygon(
                [(x_left, y_far), (x_right, y_far), (x_right, y_near), (x_left, y_near)],
                fill=shade,
            )

    # ── 远墙 ─────────────────────────────────────────────────────────────
    wall_y = horizon_y - 2
    far_shade = _blend(WALL_FAR, SKY, 0.4)
    draw.rectangle([(0, wall_y - 60), (W, wall_y)], fill=WALL_FAR)
    draw.rectangle([(0, wall_y - 120), (W, wall_y - 60)], fill=far_shade)

    # ── 障碍物投影（按距离渲染箱子） ───────────────────────────────────────
    for obs in sorted(obstacles_screen, key=lambda o: -o.get("z", 0)):
        z = max(0.5, obs.get("z", 5.0))
        if z > 250:
            continue
        scale = max(0.05, min(3.0, 60.0 / z))
        cx_screen = int(obs.get("sx", HALF_W))
        box_w = int(50 * scale)
        box_h = int(60 * scale)
        base_y = _perspective_y(z, horizon_y) - 2
        top_y = base_y - box_h
        left_x = cx_screen - box_w // 2
        right_x = cx_screen + box_w // 2
        # 正面
        alpha = max(40, min(255, int(255 - z * 0.6)))
        face_col = _darken(BOX_FACE, alpha / 255)
        draw.rectangle([(left_x, top_y), (right_x, base_y)], fill=face_col)
        # 顶面（梯形）
        top_off = int(box_w * 0.3)
        draw.polygon(
            [(left_x, top_y), (right_x, top_y),
             (right_x - top_off, top_y - top_off), (left_x + top_off, top_y - top_off)],
            fill=_darken(BOX_TOP, alpha / 255),
        )
        # 侧面
        draw.polygon(
            [(right_x, top_y), (right_x, base_y),
             (right_x + top_off // 2, base_y - 4), (right_x - top_off + box_w // 2, top_y - top_off)],
            fill=_darken(BOX_SIDE, alpha / 255),
        )

    # ── RGB 灯光晕影 ──────────────────────────────────────────────────────
    if rgb_on:
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        glow_r = max(rgb_r // 3, 0)
        glow_g = max(rgb_g // 3, 0)
        glow_b = max(rgb_b // 3, 0)
        od.ellipse([(W - 120, H - 100), (W + 40, H + 60)], fill=(glow_r, glow_g, glow_b, 120))
        od.ellipse([(W - 80, H - 70), (W, H + 20)], fill=(glow_r, glow_g, glow_b, 80))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

    # ── 状态叠加（左上角 HUD） ─────────────────────────────────────────────
    hud_lines = [
        f"距离: {front_distance_cm:.1f} cm",
        f"云台: pan={pan_deg:+.0f}° tilt={tilt_deg:+.0f}°",
        f"RGB: {'ON' if rgb_on else 'OFF'}  ({rgb_r},{rgb_g},{rgb_b})",
        time.strftime("%H:%M:%S"),
    ]
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    y_off = 8
    for line in hud_lines:
        # 描边
        for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            draw.text((8 + dx, y_off + dy), line, fill=(0, 0, 0), font=font)
        draw.text((8, y_off), line, fill=(220, 255, 220), font=font)
        y_off += 18

    # ── 距离警告 ──────────────────────────────────────────────────────────
    if front_distance_cm < 20:
        draw.rectangle([(0, 0), (W, H)], outline=(255, 50, 50), width=6)
        draw.text((W // 2 - 60, H - 30), "⚠ 障碍物过近", fill=(255, 80, 80), font=font)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def compute_obstacle_projections(
    rx: float, ry: float, heading_deg: float, pan_deg: float,
    obstacles: list[dict],
) -> list[dict]:
    """将 arena 中的障碍物投影到摄像头画面坐标。"""
    result = []
    view_rad = math.radians(heading_deg + pan_deg)
    fov_half = math.radians(50)

    for obs in obstacles:
        dx = obs["cx"] - rx
        dy = obs["cy"] - ry

        # 世界坐标转相机本地坐标（前方 = z 轴正方向）
        cos_h = math.cos(view_rad)
        sin_h = math.sin(view_rad)
        local_z = dx * cos_h + dy * sin_h
        local_x = -dx * sin_h + dy * cos_h

        if local_z < 1.0:
            continue  # 在身后

        dist = math.sqrt(dx * dx + dy * dy)
        angle = math.atan2(local_x, local_z)
        if abs(angle) > fov_half + 0.3:
            continue

        # 屏幕 x 坐标
        sx = int(HALF_W + math.tan(angle) * (W / (2 * math.tan(fov_half))))
        result.append({"sx": sx, "z": dist, "r": obs["r"]})

    return result


def _blend(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))


def _darken(c: tuple, f: float) -> tuple:
    return tuple(int(x * f) for x in c)
