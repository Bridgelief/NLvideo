#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PWA / 手机主屏幕图标（PNG）。

设计：深色圆角方块 + 橙色取景框四角 + 中心光圈（与 web/icon.svg 一致），
用途：Chrome 安装到主屏幕要求 ≥192px 的 PNG 图标；iOS 用 180px apple-touch-icon。

产物（web/，幂等）：
  icon-192.png、icon-512.png、icon-maskable-512.png（安全区内留白，Android 自适应图标）、icon-180.png

用法：
  python tools/make_app_icons.py
  python tools/make_app_icons.py --out D:/tmp/icons
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "web"

BG_TOP = (18, 21, 28)
BG_BOTTOM = (7, 8, 11)
ORANGE = (232, 117, 44)
ORANGE_HI = (255, 154, 77)
WHITE_LINE = (42, 48, 59)

SIZES = {
    "icon-192.png": (192, 1.0),
    "icon-512.png": (512, 1.0),
    "icon-maskable-512.png": (512, 0.74),  # maskable：图形缩进 26%，避免被裁
    "icon-180.png": (180, 1.0),            # iOS apple-touch-icon（系统自己加圆角）
}


def _rounded_gradient(size: int, scale: float) -> Image.Image:
    img = Image.new("RGB", (size, size), BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    # 垂直渐变
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)
    # 顶部橙色微光（radial 近似：几条同心椭圆）
    glow = Image.new("RGB", (size, size), (0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = int(size * 0.18), int(size * 0.06)
    for r in range(int(size * 0.75), 0, -max(1, size // 90)):
        alpha = max(0.0, 1.0 - r / (size * 0.78))
        gdraw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=tuple(int(c * alpha * 0.22) for c in ORANGE))
    img = Image.blend(img, Image.blend(img, glow, 0.0), 0.0) if False else img
    img = Image.composite(img, Image.new("RGB", (size, size), (0, 0, 0)), Image.new("L", (size, size), 255))
    return img


def draw_icon(size: int, inset_ratio: float = 1.0) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = int(size * 0.22)
    # 背景圆角块（maskable 也画满整块：安全区靠图形内缩，而不是缩小背景）
    bg = _rounded_gradient(size, inset_ratio)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.paste(bg, (0, 0), mask)

    # 内容区（maskable 时整体缩小到 74% 并居中）
    inner = int(size * inset_ratio)
    offset = (size - inner) // 2
    scale = inner / 1000.0

    def pt(x: float, y: float) -> tuple[int, int]:
        return (int(offset + x * scale), int(offset + y * scale))

    line_w = max(2, int(34 * scale))
    corner = 170 * scale          # 角括号长度
    margin = 120 * scale          # 距边距
    # 四个角括号（只画两条边）
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x0 = margin if sx > 0 else 1000 * scale - margin
        y0 = margin if sy > 0 else 1000 * scale - margin
        # 从角点出发的两条线
        if sx > 0:
            x_a, x_b = x0, x0 + corner
            y_a = y_b = y0
            x_c = x0
            y_c_a, y_c_b = y0, y0 + corner
        else:
            x_a, x_b = x0 - corner, x0
            y_a = y_b = y0
            x_c = x0
            y_c_a, y_c_b = y0, y0 + corner
        p1, p2 = pt(x_a / scale, y_a / scale), pt(x_b / scale, y_b / scale)
        p3, p4 = pt(x_c / scale, y_c_a / scale), pt(x_c / scale, y_c_b / scale)
        draw.line([p1, p2], fill=ORANGE, width=line_w)
        draw.line([p3, p4], fill=ORANGE, width=line_w)

    # 中心光圈 + 圆点
    c = pt(500, 500)
    r_outer = int(235 * scale)
    r_inner = int(88 * scale)
    draw.ellipse([c[0] - r_outer, c[1] - r_outer, c[0] + r_outer, c[1] + r_outer],
                 outline=ORANGE_HI, width=max(3, int(58 * scale)))
    draw.ellipse([c[0] - r_inner, c[1] - r_inner, c[0] + r_inner, c[1] + r_inner], fill=ORANGE)

    # 上下两个对焦点（细灰线）
    for y in (250, 750):
        p1 = pt(470, y)
        p2 = pt(530, y)
        draw.line([p1, p2], fill=WHITE_LINE, width=max(2, int(26 * scale)))

    # 圆角外裁掉（含 content 缩进时也保持整体形状）
    final_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(final_mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), final_mask)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 PWA / 主屏幕 PNG 图标")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 web/）")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for name, (size, inset) in SIZES.items():
        icon = draw_icon(size, inset)
        path = out / name
        icon.save(path, format="PNG", optimize=True)
        print(f"[icons] {path}（{size}x{size}，inset={inset}）")
    print("[icons] 完成：manifest.json 引用 192/512/maskable，index.html 引用 apple-touch-icon=icon-180.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
