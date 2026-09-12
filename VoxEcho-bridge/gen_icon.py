# -*- coding: utf-8 -*-
"""按 icon/VoxEcho.svg 渲染 VoxEcho 全套图标（ICO 多尺寸 + 各尺寸 PNG）。

输出三处（与代码引用路径一一对应）：
  - VoxEcho-bridge/VoxEcho.ico            窗口/任务栏/exe 内嵌（主）
  - VoxEcho-bridge/icon/                  托盘备用 ico + 16/32/48/128/256 PNG + 512 源图
  - VoxEcho-extension/icon/               浏览器扩展图标（manifest 引用 16/32/48/128）+ 512 源图
会删除无引用的遗留大图 VoxEcho.png（1.1MB）。
"""
import math
import numpy as np
from PIL import Image, ImageDraw
from pathlib import Path

ROOT = Path(r"D:\Documents\VoxEcho")
BRIDGE = ROOT / "VoxEcho-bridge"
EXT = ROOT / "VoxEcho-extension"

S = 512
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
PNG_SIZES = (16, 32, 48, 128, 256)

# ============ 新 SVG（VoxEcho.svg v2，墨绿/翡翠绿主题）参数 ============
# 1. 背景：暗墨绿黑圆角矩形（对角线渐变 x1=0,y1=0 -> x2=512,y2=512）
BG_TL = (0x0B, 0x1E, 0x14)          # #0B1E14
BG_BR = (0x03, 0x0A, 0x06)          # #030A06
BG_RADIUS = 115
BORDER_COLOR = (0x34, 0xD3, 0x99)   # #34D399
BORDER_ALPHA = 64                   # stroke-opacity 0.25 → 0.25*255 ≈ 64
BORDER_WIDTH = 2
BORDER_RADIUS = 114

# 2. 外围 Echo 环：电光青绿 -> 翡翠绿
#    userSpaceOnUse 对角线渐变 (36,36) -> (476,476)，上半圆半径 220，线宽 20，round cap
ECHO_STOPS = ((0.00, (0xA7, 0xF3, 0xD0)),   # #A7F3D0
              (0.50, (0x34, 0xD3, 0x99)),   # #34D399
              (1.00, (0x05, 0x96, 0x69)))   # #059669
ECHO_CX, ECHO_CY, ECHO_R = 256, 256, 220
ECHO_W = 20
ECHO_GX0, ECHO_GY0, ECHO_GX1, ECHO_GY1 = 36, 36, 476, 476

# 3. 中央 3 根薄荷荧光绿声波（userSpaceOnUse 垂直渐变 256,51 -> 256,461）
WAVE_STOPS = ((0.00, (0xA7, 0xF3, 0xD0)),   # #A7F3D0
              (0.30, (0x34, 0xD3, 0x99)),   # #34D399
              (1.00, (0x05, 0x96, 0x69)))   # #059669
WAVE_Y0, WAVE_Y1 = 51, 461
WAVES = [   # (x, y, w, h, rx) —— 与 SVG rect 一一对应
    (120, 151, 64, 210, 32),   # 左声波
    (220, 51, 72, 410, 36),    # 中主声波
    (328, 151, 64, 210, 32),   # 右声波
]


def lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def grad_color(stops, t):
    """多档线性渐变取色（t 为 0..1）。"""
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            return lerp(c0, c1, (t - t0) / (t1 - t0))
    return stops[-1][1]


def diag_gradient(h, w, tl, br):
    """左上 -> 右下 对角线渐变（对应 SVG userSpaceOnUse 对角渐变）。"""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (xx + yy) / float(w + h)
    c = (np.array(tl, dtype=np.float32) * (1.0 - t[..., None])
         + np.array(br, dtype=np.float32) * t[..., None])
    arr = np.zeros((h, w, 4), dtype=np.float32)
    arr[:, :, :3] = c
    arr[:, :, 3] = 255.0
    return arr


def rounded_mask(h, w, r):
    """圆角 alpha 蒙版（带 1px 抗锯齿）。"""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    m = np.ones((h, w), dtype=np.float32)
    for cx, cy in ((r, r), (w - 1 - r, r), (r, h - 1 - r), (w - 1 - r, h - 1 - r)):
        if cx < w // 2 and cy < h // 2:          # 左上
            in_quad = (xx < cx) & (yy < cy)
        elif cx > w // 2 and cy < h // 2:        # 右上
            in_quad = (xx > cx) & (yy < cy)
        elif cx < w // 2 and cy > h // 2:        # 左下
            in_quad = (xx < cx) & (yy > cy)
        else:                                    # 右下
            in_quad = (xx > cx) & (yy > cy)
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        alpha = np.clip(r + 0.5 - d, 0.0, 1.0)
        m = np.where(in_quad, np.minimum(m, alpha), m)
    return m


def paste_rgba(img, arr, ox, oy):
    a = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    img.alpha_composite(a, (ox, oy))


def wave(img, x, y, w, h, r):
    """按图标全局坐标的薄荷渐变绘制一根声波柱（对应 SVG mint-wave userSpaceOnUse）。"""
    yy, _ = np.mgrid[0:h, 0:w].astype(np.float32)
    t = np.clip((y + yy - WAVE_Y0) / float(WAVE_Y1 - WAVE_Y0), 0.0, 1.0)
    c0 = np.array(WAVE_STOPS[0][1], dtype=np.float32)
    c1 = np.array(WAVE_STOPS[1][1], dtype=np.float32)
    c2 = np.array(WAVE_STOPS[2][1], dtype=np.float32)
    t1 = WAVE_STOPS[1][0]
    col = np.where(t[..., None] <= t1,
                   c0 + (c1 - c0) * (t / t1)[..., None],
                   c1 + (c2 - c1) * ((t - t1) / (1.0 - t1))[..., None])
    arr = np.zeros((h, w, 4), dtype=np.float32)
    arr[:, :, :3] = col
    arr[:, :, 3] = rounded_mask(h, w, r) * 255.0
    paste_rgba(img, arr, x, y)


def echo_ring(img):
    """上半个 Echo 环，按 SVG 对角线渐变逐段取色；端点画圆模拟 round cap。"""
    cx, cy, R, W = ECHO_CX, ECHO_CY, ECHO_R, ECHO_W
    bbox = (cx - R, cy - R, cx + R, cy + R)
    draw = ImageDraw.Draw(img)
    n = 60
    step = 180.0 / n
    glen = float((ECHO_GX1 - ECHO_GX0) + (ECHO_GY1 - ECHO_GY0))
    for i in range(n):
        a0 = 180.0 + i * step
        a1 = a0 + step + 0.8          # 小重叠，衔接平滑
        rad = math.radians((a0 + a1) / 2.0)
        # PIL 角度顺时针、y 轴向下：θ=270° 对应圆顶部 (cx, cy-R)
        px = cx + R * math.cos(rad)
        py = cy + R * math.sin(rad)
        t = ((px - ECHO_GX0) + (py - ECHO_GY0)) / glen
        col = grad_color(ECHO_STOPS, t)
        draw.arc(bbox, start=a0, end=a1, fill=col + (255,), width=W)
    rr = W / 2.0
    for ang in (180.0, 0.0):          # 左右两端 round cap
        rad = math.radians(ang)
        px = cx + R * math.cos(rad)
        py = cy + R * math.sin(rad)
        t = ((px - ECHO_GX0) + (py - ECHO_GY0)) / glen
        col = grad_color(ECHO_STOPS, t)
        draw.ellipse((px - rr, py - rr, px + rr, py + rr), fill=col + (255,))


def build():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bg = diag_gradient(S, S, BG_TL, BG_BR)
    bg[:, :, 3] = rounded_mask(S, S, BG_RADIUS) * 255.0
    paste_rgba(img, bg, 0, 0)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((1, 1, S - 2, S - 2), radius=BORDER_RADIUS,
                           outline=BORDER_COLOR + (BORDER_ALPHA,), width=BORDER_WIDTH)
    echo_ring(img)
    for x, y, w, h, r in WAVES:
        wave(img, x, y, w, h, r)
    return img


def emit(img, base: Path, ico: bool, png: bool):
    """输出全套到指定目录；删除无引用的遗留 VoxEcho.png。"""
    base.mkdir(parents=True, exist_ok=True)
    if ico:
        img.save(base / "VoxEcho.ico", format="ICO", sizes=ICO_SIZES)
    if png:
        for px in PNG_SIZES:
            img.resize((px, px), Image.Resampling.LANCZOS).save(base / f"VoxEcho-{px}.png")
        img.save(base / "VoxEcho-512.png")
    legacy = base / "VoxEcho.png"
    if legacy.exists():
        legacy.unlink()
        print(f"  删除遗留大图 {legacy}")


def main():
    img = build()
    print("输出 VoxEcho 图标（512 源图渲染，墨绿/翡翠绿新主题）:")
    emit(img, BRIDGE, ico=True, png=False)          # 根：仅 ico
    print("  [bridge 根] VoxEcho.ico (7 尺寸)")
    emit(img, BRIDGE / "icon", ico=True, png=True)  # icon/：ico + 全部 PNG
    print("  [bridge/icon] VoxEcho.ico + 16/32/48/128/256.png + 512.png")
    emit(img, EXT / "icon", ico=True, png=True)     # extension/icon/：全部
    print("  [extension/icon] VoxEcho.ico + 16/32/48/128/256.png + 512.png")
    print("OK 全套图标已更新")


if __name__ == "__main__":
    main()
