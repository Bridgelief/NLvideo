# -*- coding: utf-8 -*-
"""生成 demo 帧（cv2 + numpy 合成"能一眼看懂"的简笔画式场景）+ scenarios.json。

用途：没有相机时前端可演示（GET /api/demo → 选场景 → 读 frame_url → POST /api/suggest）。
**产物是竖屏 720×1280（9:16）**，适配手机全屏取景（横屏帧在竖屏里 cover 裁切会毁构图）。
场景覆盖契约 §4.2 要求的四类构图差异 + 一个特写补充：
1. 逆光人像 · 主体偏小（亮背景 + 暗主体）
2. 背景杂乱人像（大量杂物边缘 → bg_complexity 高）
3. 产品静物 · 侧光（干净背景 + 左亮右暗）
4. 风光情绪 · 黄昏（暖色天空 + 地平线 + 剪影树）
5. 人像特写 · 背景虚化（主体占比大）

``expected_features`` 是**用 server.features.extract 对生成图实测**写进去的
（不是手填的期望值），所以它同时也是一份特征提取的回归基线。

用法：
  python tools/make_demo_frames.py
产物（幂等，重复执行结果一致）：
  server/static/demo/<id>.jpg + server/static/demo/scenarios.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

# 允许 `python tools/make_demo_frames.py` 直接运行（sys.path 加上仓库根）
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server import features  # noqa: E402

#: 竖屏 9:16（手机全屏）。改这个会改变所有场景构图，expected_features 需重算。
WIDTH, HEIGHT = 720, 1280
DEMO_DIR = REPO_ROOT / "server" / "static" / "demo"
SCENARIOS_PATH = DEMO_DIR / "scenarios.json"

#: 场景元数据（顺序即 /api/demo 返回顺序）
SCENARIOS: list[dict[str, str]] = [
    {
        "id": "backlit_portrait",
        "label": "逆光人像 · 主体偏小",
        "intent": "电影感人物特写，背景虚化",
    },
    {
        "id": "cluttered_portrait",
        "label": "背景杂乱人像",
        "intent": "人像中景，背景干净一点",
    },
    {
        "id": "product_still",
        "label": "产品静物 · 侧光",
        "intent": "产品静物特写，突出质感",
    },
    {
        "id": "landscape_mood",
        "label": "风光情绪 · 黄昏",
        "intent": "黄昏氛围感，情绪片",
    },
    {
        "id": "closeup_portrait",
        "label": "人像特写 · 背景虚化",
        "intent": "人物特写，背景虚化",
    },
]

# 常用颜色（OpenCV 是 BGR 顺序）
_SKIN = (150, 190, 235)
_HAIR = (45, 45, 55)
_CLOTH = (90, 130, 185)


# ---------------------------------------------------------------------------
# 基础画法
# ---------------------------------------------------------------------------
def _canvas() -> np.ndarray:
    return np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)


def _vgradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> np.ndarray:
    t = np.linspace(0.0, 1.0, HEIGHT, dtype=np.float32)[:, None, None]
    top_arr = np.array(top, dtype=np.float32)[None, None, :]
    bottom_arr = np.array(bottom, dtype=np.float32)[None, None, :]
    gradient = top_arr * (1.0 - t) + bottom_arr * t
    return np.repeat(gradient, WIDTH, axis=1).astype(np.uint8)


def _hgradient(left: tuple[int, int, int], right: tuple[int, int, int]) -> np.ndarray:
    t = np.linspace(0.0, 1.0, WIDTH, dtype=np.float32)[None, :, None]
    left_arr = np.array(left, dtype=np.float32)[None, None, :]
    right_arr = np.array(right, dtype=np.float32)[None, None, :]
    gradient = left_arr * (1.0 - t) + right_arr * t
    return np.repeat(gradient, HEIGHT, axis=0).astype(np.uint8)


def _add_noise(img: np.ndarray, sigma: float, seed: int) -> None:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, sigma, img.shape).astype(np.float32)
    img[:] = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _soft_glow(img: np.ndarray, center: tuple[int, int], radius: int, color, strength: float = 0.85) -> None:
    """叠加一个模糊光晕（模拟逆光/夕阳的光）。"""
    layer = np.zeros_like(img)
    cv2.circle(layer, center, int(radius), color, -1, lineType=cv2.LINE_AA)
    layer = cv2.GaussianBlur(layer, (0, 0), max(3, radius / 2.2))
    cv2.addWeighted(img, 1.0, layer, strength, 0, dst=img)


def _color(color: tuple[int, int, int], brightness: float) -> tuple[int, int, int]:
    return tuple(int(max(0, min(255, value * brightness))) for value in color)


def _draw_person(
    img: np.ndarray,
    cx: int,
    head_cy: int,
    head_r: int,
    *,
    skin: tuple[int, int, int] = _SKIN,
    hair: tuple[int, int, int] = _HAIR,
    cloth: tuple[int, int, int] = _CLOTH,
    brightness: float = 1.0,
    hair_style: str = "short",
    body_bottom: int | None = None,
) -> None:
    """简笔画式人像：椭圆头 + 头发 + 五官 + 脖子 + 肩膀/身体。

    ``body_bottom`` 控制身体下沿（默认画到画面底部；远景小人要传更小的值，
    否则身体会拉成"竹竿"）。
    """
    skin_c = _color(skin, brightness)
    hair_c = _color(hair, brightness)
    cloth_c = _color(cloth, brightness)
    cx = int(cx)
    head_cy = int(head_cy)
    head_r = max(10, int(head_r))
    bottom = int(body_bottom) if body_bottom is not None else HEIGHT
    bottom = max(head_cy + int(head_r * 2), min(HEIGHT, bottom))

    # 身体/肩膀
    torso_top = head_cy + int(head_r * 1.5)
    shoulder_w = int(head_r * 3.4)
    cv2.rectangle(
        img,
        (cx - shoulder_w // 2, torso_top),
        (cx + shoulder_w // 2, bottom),
        cloth_c,
        -1,
        lineType=cv2.LINE_AA,
    )
    cv2.ellipse(
        img,
        (cx, torso_top),
        (shoulder_w // 2, int(head_r * 0.85)),
        0, 180, 360, cloth_c, -1, lineType=cv2.LINE_AA,
    )
    # 手臂（两条斜线，让轮廓更像人）
    arm = max(3, head_r // 4)
    cv2.line(img, (cx - shoulder_w // 2, torso_top + head_r), (cx - shoulder_w // 2 - head_r // 2, bottom),
             cloth_c, arm, lineType=cv2.LINE_AA)
    cv2.line(img, (cx + shoulder_w // 2, torso_top + head_r), (cx + shoulder_w // 2 + head_r // 2, bottom),
             cloth_c, arm, lineType=cv2.LINE_AA)

    # 脖子
    neck_w = max(6, int(head_r * 0.6))
    cv2.rectangle(img, (cx - neck_w // 2, head_cy + head_r // 2), (cx + neck_w // 2, torso_top + 2),
                  skin_c, -1, lineType=cv2.LINE_AA)
    # 头
    cv2.ellipse(img, (cx, head_cy), (int(head_r * 0.92), int(head_r * 1.12)), 0, 0, 360,
                skin_c, -1, lineType=cv2.LINE_AA)
    # 头发（上半部）
    if hair_style == "long":
        cv2.ellipse(img, (cx, head_cy - int(head_r * 0.1)),
                    (int(head_r * 1.02), int(head_r * 1.05)), 0, 180, 360, hair_c, -1, lineType=cv2.LINE_AA)
        cv2.ellipse(img, (cx - int(head_r * 0.85), head_cy + int(head_r * 0.5)),
                    (int(head_r * 0.3), int(head_r * 1.0)), 0, 0, 360, hair_c, -1, lineType=cv2.LINE_AA)
        cv2.ellipse(img, (cx + int(head_r * 0.85), head_cy + int(head_r * 0.5)),
                    (int(head_r * 0.3), int(head_r * 1.0)), 0, 0, 360, hair_c, -1, lineType=cv2.LINE_AA)
    else:
        cv2.ellipse(img, (cx, head_cy - int(head_r * 0.18)),
                    (int(head_r * 0.99), int(head_r * 0.98)), 0, 180, 360, hair_c, -1, lineType=cv2.LINE_AA)
    # 五官（让"这是个人"一眼可辨）
    eye_r = max(2, int(head_r * 0.10))
    eye_y = head_cy - int(head_r * 0.12)
    eye_dx = int(head_r * 0.36)
    dark = _color((30, 30, 35), brightness)
    for sign in (-1, 1):
        cv2.circle(img, (cx + sign * eye_dx, eye_y), eye_r, dark, -1, lineType=cv2.LINE_AA)
        cv2.line(
            img,
            (cx + sign * eye_dx - eye_r * 2, eye_y - int(head_r * 0.32)),
            (cx + sign * eye_dx + eye_r * 2, eye_y - int(head_r * 0.34)),
            hair_c, max(1, head_r // 22), lineType=cv2.LINE_AA,
        )
    cv2.line(img, (cx, eye_y + int(head_r * 0.12)), (cx, eye_y + int(head_r * 0.42)),
             _color((110, 130, 160), brightness), max(1, head_r // 30), lineType=cv2.LINE_AA)
    cv2.ellipse(img, (cx, head_cy + int(head_r * 0.45)), (int(head_r * 0.26), int(head_r * 0.13)),
                0, 0, 180, _color((90, 90, 160), brightness), max(2, head_r // 18), lineType=cv2.LINE_AA)


def _draw_bottle(
    img: np.ndarray,
    cx: int,
    base_y: int,
    width: int,
    height: int,
    *,
    glass: tuple[int, int, int] = (150, 120, 70),
    label: tuple[int, int, int] = (210, 225, 240),
    cap: tuple[int, int, int] = (60, 60, 70),
) -> None:
    """简笔画式产品瓶：圆角瓶身 + 瓶颈 + 瓶盖 + 标签 + 高光。"""
    cx = int(cx)
    base_y = int(base_y)
    width = max(20, int(width))
    height = max(40, int(height))
    body_h = int(height * 0.62)
    shoulder_y = base_y - body_h
    neck_h = int(height * 0.16)
    neck_w = int(width * 0.28)
    x0 = cx - width // 2
    x1 = cx + width // 2
    # 瓶身
    cv2.rectangle(img, (x0, shoulder_y), (x1, base_y), glass, -1, lineType=cv2.LINE_AA)
    cv2.ellipse(img, (cx, base_y), (width // 2, max(4, width // 8)), 0, 0, 360, glass, -1, lineType=cv2.LINE_AA)
    cv2.ellipse(img, (cx, shoulder_y), (width // 2, max(6, width // 5)), 0, 180, 360, glass, -1, lineType=cv2.LINE_AA)
    # 瓶颈 + 瓶盖
    neck_top = shoulder_y - neck_h
    cv2.rectangle(img, (cx - neck_w // 2, neck_top), (cx + neck_w // 2, shoulder_y + 4), glass, -1,
                  lineType=cv2.LINE_AA)
    cv2.rectangle(
        img,
        (cx - int(neck_w * 0.65), neck_top - int(height * 0.08)),
        (cx + int(neck_w * 0.65), neck_top),
        cap, -1, lineType=cv2.LINE_AA,
    )
    # 标签
    label_y0 = shoulder_y + int(body_h * 0.28)
    label_y1 = shoulder_y + int(body_h * 0.72)
    cv2.rectangle(img, (x0 + int(width * 0.10), label_y0), (x1 - int(width * 0.10), label_y1),
                  label, -1, lineType=cv2.LINE_AA)
    cv2.putText(img, "V", (cx - int(width * 0.10), label_y1 - int(body_h * 0.10)),
                cv2.FONT_HERSHEY_SIMPLEX, max(0.5, width / 170.0), (70, 70, 90), max(2, width // 70),
                cv2.LINE_AA)
    # 高光条（体现侧光）
    cv2.rectangle(img, (x0 + int(width * 0.14), shoulder_y + int(body_h * 0.08)),
                  (x0 + int(width * 0.26), base_y - int(body_h * 0.10)),
                  (245, 245, 250), -1, lineType=cv2.LINE_AA)
    # 底部投影
    shadow = np.zeros_like(img)
    cv2.ellipse(shadow, (cx + int(width * 0.25), base_y + 6), (int(width * 0.75), max(6, width // 6)),
                0, 0, 360, (20, 20, 25), -1, lineType=cv2.LINE_AA)
    shadow = cv2.GaussianBlur(shadow, (0, 0), 9)
    cv2.addWeighted(img, 1.0, shadow, 0.45, 0, dst=img)


def _draw_tree(img: np.ndarray, root: tuple[int, int], scale: float, color: tuple[int, int, int]) -> None:
    """剪影树：主干 + 分叉 + 叶团。"""
    x, y = root
    trunk_h = int(200 * scale)
    trunk_w = max(4, int(16 * scale))
    top = (x, y - trunk_h)
    cv2.line(img, root, top, color, trunk_w, lineType=cv2.LINE_AA)
    for sign, fraction in ((-1, 0.55), (1, 0.62), (-1, 0.78), (1, 0.82)):
        branch_y = y - int(trunk_h * fraction)
        branch_end = (x + sign * int(95 * scale * (1.15 - fraction)), branch_y - int(60 * scale))
        cv2.line(img, (x, branch_y), branch_end, color, max(2, trunk_w // 2), lineType=cv2.LINE_AA)
        cv2.circle(img, branch_end, max(6, int(26 * scale)), color, -1, lineType=cv2.LINE_AA)
    cv2.circle(img, top, max(10, int(52 * scale)), color, -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (top[0] - int(38 * scale), top[1] + int(24 * scale)), max(8, int(34 * scale)),
               color, -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (top[0] + int(40 * scale), top[1] + int(20 * scale)), max(8, int(36 * scale)),
               color, -1, lineType=cv2.LINE_AA)


# ---------------------------------------------------------------------------
# 五个竖屏场景（720×1280）
# ---------------------------------------------------------------------------
def _scene_backlit_portrait() -> np.ndarray:
    """逆光人像：亮天空 + 白太阳 + 小主体（主体占比偏小、光线判 back）。"""
    img = _vgradient((250, 245, 225), (200, 222, 248))  # 上亮下略暗的冷白天空
    _soft_glow(img, (546, 196), 120, (255, 255, 255), 0.5)
    cv2.circle(img, (546, 196), 42, (252, 252, 252), -1, lineType=cv2.LINE_AA)
    # 云（模糊椭圆）
    clouds = np.zeros_like(img)
    for cx, cy, cw, ch in ((170, 260, 130, 34), (400, 380, 110, 26), (600, 500, 120, 28),
                           (210, 520, 90, 22), (520, 130, 100, 26)):
        cv2.ellipse(clouds, (cx, cy), (cw, ch), 0, 0, 360, (240, 244, 250), -1, lineType=cv2.LINE_AA)
    clouds = cv2.GaussianBlur(clouds, (0, 0), 12)
    cv2.addWeighted(img, 1.0, clouds, 0.75, 0, dst=img)
    # 远处山脊（暗）
    ridge = np.array(
        [[0, 872], [120, 815], [280, 850], [430, 805], [580, 840], [720, 818],
         [720, 1280], [0, 1280]], dtype=np.int32
    )
    cv2.fillPoly(img, [ridge], (110, 130, 150), lineType=cv2.LINE_AA)
    # 地面
    ground = np.array([[0, 940], [720, 918], [720, 1280], [0, 1280]], dtype=np.int32)
    cv2.fillPoly(img, [ground], (70, 82, 95), lineType=cv2.LINE_AA)
    # 主体：很小、偏暗（逆光人像），补轮廓光
    _draw_person(img, cx=286, head_cy=848, head_r=27, brightness=0.78, cloth=(60, 66, 82),
                 body_bottom=1050)
    cv2.ellipse(img, (286, 848), (int(27 * 0.98), int(27 * 1.15)), 0, 150, 260,
                (252, 244, 220), 3, lineType=cv2.LINE_AA)
    cv2.ellipse(img, (286, 890), (int(27 * 1.75), int(27 * 0.8)), 0, 190, 320,
                (240, 232, 210), 2, lineType=cv2.LINE_AA)
    # 地面投影
    cv2.ellipse(img, (286, 1052), (66, 14), 0, 0, 360, (45, 52, 62), -1, lineType=cv2.LINE_AA)
    _add_noise(img, 3.0, seed=11)
    return img


def _scene_cluttered_portrait() -> np.ndarray:
    """背景杂乱人像：满墙小物件/书架/植物 + 中等大小顺光主体。"""
    # 格子墙纸：制造密集边缘（背景复杂度高）
    pattern = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    cell = 34
    rng = np.random.default_rng(5)
    wall_colors = [(70, 76, 88), (96, 102, 116), (55, 60, 72), (84, 90, 104)]
    for gy in range(0, HEIGHT, cell):
        for gx in range(0, WIDTH, cell):
            color = wall_colors[int(rng.integers(0, len(wall_colors)))]
            cv2.rectangle(pattern, (gx, gy), (gx + cell - 2, gy + cell - 2), color, -1)
    img = cv2.addWeighted(_vgradient((105, 110, 118), (78, 82, 90)), 0.45, pattern, 0.55, 0)
    # 书架层板 + 书脊（细长竖条 → 更多边缘）
    book_colors = [
        (175, 175, 40), (60, 150, 70), (170, 90, 40), (150, 60, 120),
        (40, 120, 180), (90, 180, 175), (110, 110, 210), (140, 70, 60),
    ]
    for shelf_y in (250, 520, 800, 1080):
        cv2.line(img, (0, shelf_y), (WIDTH, shelf_y), (200, 200, 208), 7, lineType=cv2.LINE_AA)
        x = 16
        while x < WIDTH - 40:
            width = int(rng.integers(12, 30))
            height = int(rng.integers(56, 96))
            color = book_colors[int(rng.integers(0, len(book_colors)))]
            cv2.rectangle(img, (x, shelf_y - height), (x + width, shelf_y), color, -1, lineType=cv2.LINE_AA)
            cv2.line(img, (x + width // 2, shelf_y - height + 6), (x + width // 2, shelf_y - 6),
                     (235, 235, 240), 2, lineType=cv2.LINE_AA)
            x += width + int(rng.integers(4, 12))
    # 墙上相框/挂件
    for index in range(12):
        x = int(rng.integers(8, WIDTH - 110))
        y = int(rng.integers(8, HEIGHT - 120))
        w = int(rng.integers(34, 100))
        h = int(rng.integers(34, 110))
        color = book_colors[index % len(book_colors)]
        if index % 2 == 0:
            cv2.rectangle(img, (x, y), (x + w, y + h), color, -1, lineType=cv2.LINE_AA)
            cv2.rectangle(img, (x, y), (x + w, y + h), (230, 230, 235), 3, lineType=cv2.LINE_AA)
        else:
            cv2.circle(img, (x, y), w // 2, color, -1, lineType=cv2.LINE_AA)
    # 植物叶片（密集小椭圆）
    for _index in range(60):
        x = int(rng.integers(0, WIDTH))
        y = int(rng.integers(0, HEIGHT))
        cv2.ellipse(img, (x, y), (int(rng.integers(6, 18)), int(rng.integers(3, 8))),
                    int(rng.integers(0, 180)), 0, 360, (50, 130, 70), -1, lineType=cv2.LINE_AA)
    # 主体：中等大小、顺光（亮）
    _draw_person(img, cx=360, head_cy=540, head_r=54, brightness=1.08, hair_style="long",
                 cloth=(185, 130, 90))
    _add_noise(img, 3.0, seed=22)
    return img


def _scene_product_still() -> np.ndarray:
    """产品静物：干净背景 + 左侧主光（左亮右暗）+ 居中瓶子（竖屏居中偏下）。"""
    img = _hgradient((205, 210, 215), (48, 52, 58))  # 左亮右暗的影棚背景
    # 桌面（下方暗带）
    table = np.array([[0, 962], [720, 962], [720, 1280], [0, 1280]], dtype=np.int32)
    cv2.fillPoly(img, [table], (60, 62, 68), lineType=cv2.LINE_AA)
    cv2.line(img, (0, 962), (WIDTH, 962), (120, 122, 128), 3, lineType=cv2.LINE_AA)
    # 左上方主光的柔光感
    _soft_glow(img, (90, 220), 260, (235, 238, 242), 0.35)
    # 瓶子主体（居中偏右一点，留出左侧光照方向）
    _draw_bottle(img, cx=400, base_y=966, width=224, height=452,
                 glass=(120, 90, 60), label=(220, 230, 240), cap=(50, 50, 58))
    # 高光（左上方反光）
    cv2.line(img, (330, 700), (330, 880), (245, 245, 250), 6, lineType=cv2.LINE_AA)
    _add_noise(img, 1.6, seed=33)
    return img


def _scene_landscape_mood() -> np.ndarray:
    """风光情绪：黄昏暖色天空 + 地平线 + 剪影树 + 太阳（竖屏）。"""
    img = _vgradient((40, 120, 235), (95, 205, 252))  # 顶部深橙 → 地平线亮橙
    horizon = 800
    # 太阳（白色核心 + 暖光晕）
    _soft_glow(img, (500, 690), 100, (120, 215, 255), 0.9)
    cv2.circle(img, (500, 700), 32, (252, 252, 250), -1, lineType=cv2.LINE_AA)
    # 云带
    cloud_layer = np.zeros_like(img)
    for cx, cy, cw, ch in ((170, 240, 130, 20), (400, 360, 110, 18), (600, 180, 140, 22),
                           (300, 520, 100, 16)):
        cv2.ellipse(cloud_layer, (cx, cy), (cw, ch), 0, 0, 360, (250, 230, 200), -1, lineType=cv2.LINE_AA)
    cloud_layer = cv2.GaussianBlur(cloud_layer, (0, 0), 16)
    cv2.addWeighted(img, 1.0, cloud_layer, 0.45, 0, dst=img)
    # 地面（暗色）
    ground = np.array([[0, horizon], [720, horizon - 12], [720, 1280], [0, 1280]], dtype=np.int32)
    cv2.fillPoly(img, [ground], (28, 42, 70), lineType=cv2.LINE_AA)
    # 地平线亮线（便于 Canny/Hough 估计 horizon_y）
    cv2.line(img, (0, horizon), (WIDTH, horizon - 12), (190, 205, 220), 3, lineType=cv2.LINE_AA)
    # 剪影树 + 飞鸟
    _draw_tree(img, (170, 1000), scale=1.15, color=(18, 22, 32))
    _draw_tree(img, (600, 940), scale=0.65, color=(20, 24, 34))
    for bx, by in ((350, 230), (392, 205), (434, 240)):
        cv2.line(img, (bx - 12, by), (bx, by + 7), (35, 35, 45), 2, lineType=cv2.LINE_AA)
        cv2.line(img, (bx, by + 7), (bx + 12, by), (35, 35, 45), 2, lineType=cv2.LINE_AA)
    _add_noise(img, 2.4, seed=44)
    return img


def _scene_closeup_portrait() -> np.ndarray:
    """人像特写：大主体 + 强虚化冷色背景（主体占比高）。"""
    # 先画背景，再整幅虚化，最后画清晰人物 → 景深感
    background = _vgradient((70, 82, 100), (34, 40, 52))
    for cx, cy, radius, color in (
        (110, 260, 85, (90, 120, 160)), (610, 190, 95, (80, 110, 140)),
        (630, 1030, 110, (70, 95, 125)), (100, 1100, 85, (60, 85, 115)),
        (360, 90, 70, (100, 120, 150)),
    ):
        cv2.circle(background, (cx, cy), radius, color, -1, lineType=cv2.LINE_AA)
    background = cv2.GaussianBlur(background, (0, 0), 28)
    img = background
    # 大特写：头 + 肩膀占满画面（竖屏）
    _draw_person(img, cx=360, head_cy=440, head_r=182, brightness=1.05, hair_style="long",
                 cloth=(150, 100, 75))
    # 主体补光（左脸提亮，形成柔和立体感）
    face_light = np.zeros_like(img)
    cv2.ellipse(face_light, (315, 430), (135, 205), 0, 0, 360, (60, 70, 90), -1, lineType=cv2.LINE_AA)
    face_light = cv2.GaussianBlur(face_light, (0, 0), 40)
    cv2.addWeighted(img, 1.0, face_light, 0.35, 0, dst=img)
    _add_noise(img, 1.4, seed=55)
    return img


_SCENE_BUILDERS = {
    "backlit_portrait": _scene_backlit_portrait,
    "cluttered_portrait": _scene_cluttered_portrait,
    "product_still": _scene_product_still,
    "landscape_mood": _scene_landscape_mood,
    "closeup_portrait": _scene_closeup_portrait,
}


# ---------------------------------------------------------------------------
# 生成入口
# ---------------------------------------------------------------------------
def generate(out_dir: Path | str | None = None, scenarios_path: Path | str | None = None) -> dict:
    """生成全部 demo 帧 + scenarios.json（幂等；expected_features 实测后写入）。"""
    target_dir = Path(out_dir) if out_dir is not None else DEMO_DIR
    target_scenarios = Path(scenarios_path) if scenarios_path is not None else target_dir / "scenarios.json"
    target_dir.mkdir(parents=True, exist_ok=True)

    scenarios: list[dict] = []
    for meta in SCENARIOS:
        scene_id = meta["id"]
        builder = _SCENE_BUILDERS[scene_id]
        frame = builder()
        jpg_path = target_dir / f"{scene_id}.jpg"
        ok = cv2.imwrite(str(jpg_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            raise RuntimeError(f"写 demo 帧失败：{jpg_path}")
        raw = jpg_path.read_bytes()
        extracted = features.extract(raw)
        # expected_features 是实测值（不是手填），见模块 docstring
        scenarios.append(
            {
                "id": scene_id,
                "label": meta["label"],
                "intent": meta["intent"],
                "frame_url": f"/static/demo/{scene_id}.jpg",
                "expected_features": extracted["frame_features"],
            }
        )

    payload = {"version": "1.0", "scenarios": scenarios}
    target_scenarios.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"scenarios": scenarios, "dir": str(target_dir), "scenarios_path": str(target_scenarios)}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_dir = None
    scenarios_path = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-h", "--help"):
            print(__doc__)
            return 0
        if arg in ("--out-dir", "--out") and index + 1 < len(argv):
            out_dir = argv[index + 1]
            index += 2
            continue
        if arg == "--scenarios" and index + 1 < len(argv):
            scenarios_path = argv[index + 1]
            index += 2
            continue
        print(f"未知参数：{arg}（用 --help 查看用法）")
        return 2
    result = generate(out_dir=out_dir, scenarios_path=scenarios_path)
    print(f"demo 帧已生成（{WIDTH}x{HEIGHT} 竖屏）：{result['dir']}")
    print(f"场景清单：{result['scenarios_path']}")
    for item in result["scenarios"]:
        print(f"  - {item['id']}: {item['label']} -> {item['frame_url']}")
        print(f"    expected_features = {json.dumps(item['expected_features'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
