# -*- coding: utf-8 -*-
"""画面特征提取（**启发式近似，不是 MediaPipe**）。

诚实说明（同样的说明也写进 ``GET /api/health`` 的 ``features_note``）：
- 主体定位 = OpenCV Haar 人脸检测（**若本机 cv2 未附带级联 XML 则不可用**）
  → 肤色区域（YCrCb 阈值）→ 显著性中心先验，逐级回退；
- ``bg_complexity`` = Canny 边缘密度（占比归一化）+ 高频能量（Laplacian 方差）加权；
- ``light_direction`` = 画面亮度分区（左/右/上/下）与主体亮度对比的**规则映射**；
- ``gaze_space`` = 主体框中心相对画面中心在"朝向"一侧的空白比例；
- ``horizon_y`` = Canny + Hough 近水平线段中位数，失败回退 0.5；
- ``focus_ok`` = 灰度 Laplacian 方差阈值。

这些做法在真实拍摄里只是"粗略但方向对"：没有关键点、没有姿态、没有语义分割，
主体框/光线方向都可能误判。v1 目标是给引擎一个可比较的输入，而不是精确测量。

公开接口（Lead 的 tests 直接 import）：
- ``extract(image_bytes: bytes) -> dict``：
  ``{"frame_features": {...}, "overlay": {...}, "focus_ok": 0|1, "warnings": [...]}``，
  坏图/空字节返回默认特征 + warnings，**不抛异常**；
- ``decode_image(data_url_or_b64: str) -> np.ndarray | None``：接受纯 base64 或
  ``data:image/jpeg;base64,...``，失败返回 None。
"""

from __future__ import annotations

import base64
import binascii
import os
from typing import Any

import cv2
import numpy as np

#: 长边压缩上限（契约 §4.3：长边 ≤1280 再算）
MAX_EDGE = 1280

#: 对焦判定阈值：Laplacian 方差低于它判为失焦（可用环境变量覆盖，便于演示调参）。
#: 15 是偏宽松的阈值：合成简笔画/大色块场景方差本来就低（约 30~50），
#: 真正的失焦实拍帧通常 <10；演示时宁可不误杀干净帧。
FOCUS_VAR_THRESHOLD = float(os.environ.get("VIDEOSHOOT_FOCUS_THRESHOLD", "15"))

#: 图像最小边小于它时"对焦"不可靠，直接判为正常（见 _focus_estimate）
FOCUS_MIN_SIDE = 120

#: 特征缺省值（与 engine.suggest_engine 的缺省一致，见 INTERFACES §2.2）
DEFAULT_FEATURES: dict[str, Any] = {
    "subject_ratio": 0.30,
    "bg_complexity": 0.30,
    "light_direction": "front",
    "gaze_space": 0.30,
}

#: overlay 缺省值（契约 §4.3）
DEFAULT_OVERLAY: dict[str, Any] = {
    "subject_box": None,
    "subject_box_confidence": 0.0,
    "thirds": True,
    "horizon_y": 0.5,
}

#: 合法光线方向（与 engine/action_effects.LIGHT_DIRECTIONS 对齐）
LIGHT_DIRECTIONS = ("front", "side", "back", "low", "golden")

_cascade_cache: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def decode_image(data_url_or_b64: str) -> np.ndarray | None:
    """把 base64 / dataURL 字符串解成 BGR ndarray；失败返回 None（不抛异常）。"""
    if not isinstance(data_url_or_b64, str):
        return None
    s = data_url_or_b64.strip()
    if not s:
        return None
    if s.startswith("data:"):
        comma = s.find(",")
        if comma < 0:
            return None
        s = s[comma + 1:]
    s = "".join(s.split())  # 去换行/空白
    if not s:
        return None
    s += "=" * (-len(s) % 4)  # 补齐 padding
    try:
        raw = base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    try:
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:  # pragma: no cover - 防御性兜底
        return None
    if img is None or img.size == 0:
        return None
    return img


def _as_bgr(frame: Any) -> np.ndarray | None:
    """把 bytes / dataURL / ndarray 统一成 BGR ndarray。"""
    if isinstance(frame, np.ndarray):
        if frame.size == 0:
            return None
        if frame.ndim == 2:
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if frame.ndim == 3 and frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        if frame.ndim == 3 and frame.shape[2] == 1:
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        return frame if frame.ndim == 3 else None
    if isinstance(frame, (bytes, bytearray)):
        try:
            arr = np.frombuffer(bytes(frame), dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:  # pragma: no cover
            return None
        return img if img is not None and img.size else None
    if isinstance(frame, str):
        return decode_image(frame)
    return None


def _limit_size(img: np.ndarray) -> np.ndarray:
    """长边缩到 ≤ MAX_EDGE（避免大图拖慢特征提取）。"""
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge <= MAX_EDGE:
        return img
    scale = MAX_EDGE / float(long_edge)
    return cv2.resize(
        img,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=cv2.INTER_AREA,
    )


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, value)))


def _default_result(warnings: list[str]) -> dict:
    """坏图/异常时的安全返回：默认特征 + 默认 overlay + warnings，绝不抛异常。"""
    return {
        "frame_features": dict(DEFAULT_FEATURES),
        "overlay": dict(DEFAULT_OVERLAY),
        "focus_ok": 0,
        "warnings": list(warnings),
    }


# ---------------------------------------------------------------------------
# Haar 级联（本机 OpenCV 5.0 的 cv2/data 里**没有**级联 XML，需优雅降级）
# ---------------------------------------------------------------------------
def _cascade(name: str):
    if name in _cascade_cache:
        return _cascade_cache[name]
    cascade = None
    base = getattr(getattr(cv2, "data", None), "haarcascades", "")
    path = os.path.join(str(base), name) if base else ""
    if path and os.path.exists(path):
        try:
            cascade = cv2.CascadeClassifier(path)
            if cascade.empty():
                cascade = None
        except Exception:  # pragma: no cover
            cascade = None
    _cascade_cache[name] = cascade
    return cascade


def haar_available() -> bool:
    """本机是否真的能用 Haar 人脸检测（供 /api/health 诚实说明）。"""
    return any(
        _cascade(n) is not None
        for n in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")
    )


# ---------------------------------------------------------------------------
# 主体定位
# ---------------------------------------------------------------------------
def _face_subject_box(gray: np.ndarray, w: int, h: int):
    """Haar 人脸 → 主体框（头 + 肩膀）。返回 (box_norm, confidence, method) 或 None。"""
    equalized = cv2.equalizeHist(gray)
    for cascade_name in (
        "haarcascade_frontalface_default.xml",
        "haarcascade_profileface.xml",
    ):
        cascade = _cascade(cascade_name)
        if cascade is None:
            continue
        try:
            faces = cascade.detectMultiScale(
                equalized,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(max(24, w // 25), max(24, h // 25)),
            )
        except Exception:  # pragma: no cover
            continue
        if faces is None or len(faces) == 0:
            continue
        fx, fy, fw, fh = max(faces, key=lambda f: int(f[2]) * int(f[3]))
        fx, fy, fw, fh = float(fx), float(fy), float(fw), float(fh)
        cx = fx + fw / 2.0
        box_w = min(w * 0.98, fw * 2.3)
        box_h = min(h * 0.98, fh * 3.4)
        x0 = _clamp(cx - box_w / 2.0, 0.0, max(0.0, w - box_w))
        y0 = _clamp(fy - fh * 0.25, 0.0, max(0.0, h - box_h))
        box = [x0 / w, y0 / h, box_w / w, box_h / h]
        return box, 0.75, "haar_face"
    return None


def _skin_subject_box(img: np.ndarray, w: int, h: int):
    """肤色区域（YCrCb 阈值）→ 主体框。绘画/简笔画里的人像也能命中。"""
    try:
        ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
        # 经验阈值：Y 不能太暗（排除深色剪影/地面），Cr 136~176 才是肤色，
        # Cb 80~127 排除紫色/蓝色；再要求 Cr-Cb≥32（肤色明显偏红），
        # 否则暗部/灰墙/暖色天空也会落进 Cr 区间被误判成皮肤。
        mask = cv2.inRange(
            ycc,
            np.array([70, 136, 80], dtype=np.uint8),
            np.array([245, 176, 127], dtype=np.uint8),
        )
        chroma = ycc[:, :, 1].astype(np.int16) - ycc[:, :, 2].astype(np.int16)
        warm_skin = (chroma >= 32).astype(np.uint8) * 255
        mask = cv2.bitwise_and(mask, warm_skin)
    except Exception:  # pragma: no cover
        return None
    # 小核 + 少迭代：避免把远处小主体（逆光人像的小脸）腐蚀掉
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    try:
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    except Exception:  # pragma: no cover
        return None
    frame_area = float(w * h)
    best = None
    best_score = 0.0
    for idx in range(1, count):
        x, y, bw, bh, area = (int(v) for v in stats[idx])
        if area < 0.0008 * frame_area or area > 0.55 * frame_area:
            continue
        if bw <= 0 or bh <= 0:
            continue
        aspect = bh / float(bw)
        if aspect < 0.55 or aspect > 2.1:  # 头部/头+颈大致是方形或竖长
            continue
        # 越靠近画面中部/略偏上越像主体
        cx = (x + bw / 2.0) / w
        cy = (y + bh / 2.0) / h
        center_bonus = 1.0 - 0.5 * (abs(cx - 0.5) + abs(cy - 0.5))
        score = float(area) * max(0.2, center_bonus)
        if score > best_score:
            best_score = score
            best = (x, y, bw, bh)
    if best is None:
        return None
    x, y, bw, bh = best
    cx = x + bw / 2.0
    # 由"头/脸"外扩到"人"：宽度约 3.6 个头宽、高度约 5.4 个头高（含肩和上身）
    box_w = min(w * 0.98, bw * 3.6)
    box_h = min(h * 0.98, bh * 5.4)
    x0 = _clamp(cx - box_w / 2.0, 0.0, max(0.0, w - box_w))
    y0 = _clamp(y - bh * 0.20, 0.0, max(0.0, h - box_h))
    box = [x0 / w, y0 / h, box_w / w, box_h / h]
    return box, 0.5, "skin_region"


def _saliency_prior_box(gray: np.ndarray, w: int, h: int):
    """显著性 + 中心先验：给一个"大概在中间"的主体框（置信度低）。"""
    try:
        small = cv2.resize(gray, (max(32, w // 4), max(32, h // 4)))
        blur = cv2.GaussianBlur(small, (0, 0), 3)
        sal = cv2.absdiff(small, blur).astype(np.float32)
    except Exception:  # pragma: no cover
        sal = None
    if sal is not None and float(sal.max()) > 1.0:
        sh, sw = sal.shape[:2]
        # 中心加权：越靠中心越可能是主体
        ys, xs = np.mgrid[0:sh, 0:sw]
        cx, cy = sw / 2.0, sh / 2.0
        dist2 = ((xs - cx) / max(cx, 1.0)) ** 2 + ((ys - cy) / max(cy, 1.0)) ** 2
        sal = sal * np.exp(-1.1 * dist2)
        total = float(sal.sum())
        prior_w, prior_h = 0.25, 0.35
        x0 = int((0.5 - prior_w / 2.0) * sw)
        x1 = int((0.5 + prior_w / 2.0) * sw)
        y0 = int((0.5 - prior_h / 2.0) * sh)
        y1 = int((0.5 + prior_h / 2.0) * sh)
        coverage = float(sal[y0:y1, x0:x1].sum()) / total if total > 0 else 0.0
        scale = _clamp(0.55 + 1.15 * coverage, 0.55, 1.7)
        # 显著性重心偏移（限制在画面中部 ±0.08，避免框跑到画面边缘）
        if total > 0:
            gy = float((sal * ys).sum()) / total / sh
            gx = float((sal * xs).sum()) / total / sw
            shift_x = _clamp(gx - 0.5, -0.08, 0.08)
            shift_y = _clamp(gy - 0.5, -0.08, 0.08)
        else:  # pragma: no cover
            shift_x = shift_y = 0.0
        box_w = _clamp(prior_w * (scale ** 0.5), 0.05, 0.95)
        box_h = _clamp(prior_h * (scale ** 0.5), 0.08, 0.95)
        x0n = _clamp(0.5 + shift_x - box_w / 2.0, 0.0, 1.0 - box_w)
        y0n = _clamp(0.5 + shift_y - box_h / 2.0, 0.0, 1.0 - box_h)
        return [x0n, y0n, box_w, box_h], 0.3, "saliency_prior"
    # 纯中心先验（契约：无人脸则中心先验框 ~0.25×0.35）
    box_w, box_h = 0.25, 0.35
    return [0.375, 0.325, box_w, box_h], 0.2, "center_prior"


def _locate_subject(img: np.ndarray, gray: np.ndarray, w: int, h: int):
    """逐级回退定位主体框：Haar 人脸 → 肤色 → 显著性先验 → 中心先验。"""
    result = _face_subject_box(gray, w, h)
    if result is not None:
        return result
    result = _skin_subject_box(img, w, h)
    if result is not None:
        return result
    return _saliency_prior_box(gray, w, h)


def _subject_ratio(box: list[float]) -> float:
    """主体占比 = 主体框面积 / 画面面积（框是归一化 [x,y,w,h]）。"""
    return _clamp(box[2] * box[3], 0.01, 0.95)


# ---------------------------------------------------------------------------
# 背景复杂度 / 光线方向 / 视线留白 / 地平线 / 对焦
# ---------------------------------------------------------------------------
def _bg_complexity(gray: np.ndarray) -> float:
    """Canny 边缘密度 + 高频能量（Laplacian 方差对数归一化）的加权近似。"""
    h, w = gray.shape[:2]
    denom = max(1, (w - 1) * (h - 1))
    edges = cv2.Canny(gray, 60, 180)
    edge_density = float(np.count_nonzero(edges)) / denom
    # 归一化：实拍照片边缘密度常在 0.02~0.12；0.12 以上视为非常杂
    edge_norm = _clamp(edge_density / 0.12)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    hf = float(lap.var())
    hf_norm = _clamp(np.log1p(hf) / np.log1p(2500.0))
    return _clamp(0.85 * edge_norm + 0.15 * hf_norm)


def _light_direction(img: np.ndarray, gray: np.ndarray, box: list[float] | None) -> str:
    """亮度分区 + 主体亮度对比 → front/side/back/low/golden 的规则映射。"""
    h, w = gray.shape[:2]
    light = gray.astype(np.float32)
    frame_mean = float(light.mean())
    half_w = max(1, w // 2)
    half_h = max(1, h // 2)
    left = float(light[:, :half_w].mean())
    right = float(light[:, half_w:].mean())
    top = float(light[:half_h].mean())
    bottom = float(light[half_h:].mean())
    b_mean, g_mean, r_mean = (float(c.mean()) for c in cv2.split(img.astype(np.float32)))
    warm = r_mean - b_mean
    subj_mean = frame_mean
    if box is not None:
        x, y, bw, bh = box
        x0 = int(_clamp(x, 0.0, 1.0) * w)
        x1 = int(_clamp(x + bw, 0.0, 1.0) * w)
        y0 = int(_clamp(y, 0.0, 1.0) * h)
        y1 = int(_clamp(y + bh, 0.0, 1.0) * h)
        if x1 > x0 + 2 and y1 > y0 + 2:
            subj_mean = float(light[y0:y1, x0:x1].mean())
    brightest = max(left, right, top, bottom)
    # 1) 逆光：主体明显比画面暗，同时画面有更亮的区域（天/窗/背景灯）
    if frame_mean >= 45 and subj_mean <= frame_mean * 0.80 and (brightest - subj_mean) >= 30:
        return "back"
    # 2) 金色时刻：整体明显偏暖，且顶部（天空）比底部亮
    if warm >= 22 and (top - bottom) >= 8 and frame_mean >= 55:
        return "golden"
    # 3) 侧光：左右亮度显著不对称
    if abs(left - right) >= 13:
        return "side"
    # 4) 底光：下方明显比上方亮（地面反光/补光从下打）
    if (bottom - top) >= 15:
        return "low"
    # 5) 顺光：主体不比整体暗，左右均匀
    if subj_mean >= frame_mean * 0.90:
        return "front"
    return "front"


def _gaze_space(box: list[float] | None) -> float:
    """主体中心相对画面中心在"朝向"一侧的空白比例（无朝向信息时的近似）。"""
    if box is None:
        return float(DEFAULT_FEATURES["gaze_space"])
    x, _y, bw, _bh = box
    cx = x + bw / 2.0
    right_room = _clamp(1.0 - (x + bw), 0.0, 1.0)
    left_room = _clamp(x, 0.0, 1.0)
    if abs(cx - 0.5) <= 0.04:  # 居中：取两侧平均
        return round(_clamp((left_room + right_room) / 2.0, 0.02, 0.95), 3)
    if cx < 0.5:  # 主体偏左 → 假定朝右，看右侧留白
        return round(_clamp(right_room, 0.02, 0.95), 3)
    return round(_clamp(left_room, 0.02, 0.95), 3)


def _horizon_y(gray: np.ndarray) -> float:
    """Canny + Hough 近水平线估计地平线高度（归一化 y），失败回退 0.5。"""
    h, w = gray.shape[:2]
    try:
        edges = cv2.Canny(gray, 60, 180)
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180.0,
            threshold=60,
            minLineLength=max(30, int(w * 0.25)),
            maxLineGap=20,
        )
        if lines is not None:
            ys = []
            for line in lines:
                # 兼容不同 OpenCV 版本：HoughLinesP 返回 (N,1,4) 或 (N,4)
                coords = np.asarray(line).reshape(-1)
                if coords.size < 4:
                    continue
                x1, y1, x2, y2 = (float(v) for v in coords[:4])
                dx = abs(x2 - x1)
                if dx < 1e-6:
                    continue
                if abs(y2 - y1) <= 0.12 * dx:  # 近水平（±~7°）
                    ys.append((y1 + y2) / 2.0)
            if ys:
                return round(_clamp(float(np.median(ys)) / h, 0.03, 0.97), 3)
        # 回退：逐行边缘计数峰值（限制在中部 15%~90%）
        row_edges = edges.sum(axis=1) / 255.0
        lo = int(h * 0.15)
        hi = max(lo + 1, int(h * 0.90))
        band = row_edges[lo:hi]
        if band.size and float(band.max()) > max(4.0, w * 0.05):
            return round(_clamp(float(lo + int(band.argmax())) / h, 0.03, 0.97), 3)
    except Exception:  # pragma: no cover - 防御性兜底
        pass
    return 0.5


def _focus_estimate(gray: np.ndarray) -> tuple[int, str | None]:
    """Laplacian 方差 → focus_ok；图像太小则不可靠，默认判正常。"""
    h, w = gray.shape[:2]
    if min(h, w) < FOCUS_MIN_SIDE:
        return 1, f"图像过小（{w}x{h}），对焦无法可靠估计，默认判为对焦正常"
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return (1 if variance >= FOCUS_VAR_THRESHOLD else 0), None


def focus_ok_from_image(frame: Any) -> int:
    """给 /api/shots 用：从 before/after 图估计 focus_ok（失败/过小 → 1）。"""
    try:
        img = _as_bgr(frame)
        if img is None:
            return 1
        img = _limit_size(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return _focus_estimate(gray)[0]
    except Exception:  # pragma: no cover
        return 1


# ---------------------------------------------------------------------------
# 对外主函数
# ---------------------------------------------------------------------------
def extract(image_bytes: Any) -> dict:
    """提取一帧的画面特征；任何异常都降级为默认特征，**不抛异常**。

    参数可以是 ``bytes``（契约要求）、ndarray 或 dataURL 字符串（内部兼容）。
    返回 ``{"frame_features", "overlay", "focus_ok", "warnings"}``。
    """
    warnings: list[str] = []
    try:
        img = _as_bgr(image_bytes)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        return _default_result([f"图像解码异常：{type(exc).__name__}: {exc}"])
    if img is None:
        return _default_result(["图像解码失败（空数据或不是有效的 JPEG/PNG）"])
    try:
        img = _limit_size(img)
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    except Exception as exc:
        return _default_result([f"图像预处理失败：{type(exc).__name__}: {exc}"])

    features = dict(DEFAULT_FEATURES)
    overlay = dict(DEFAULT_OVERLAY)
    focus_ok = 1

    # 主体定位（多层回退，单层失败不影响整体）
    box: list[float] | None = None
    try:
        box, confidence, _method = _locate_subject(img, gray, w, h)
    except Exception as exc:
        warnings.append(f"主体定位失败，改用默认主体特征：{type(exc).__name__}: {exc}")
        box, confidence = None, 0.0
    if box is not None:
        try:
            overlay["subject_box"] = [round(float(v), 3) for v in box]
            overlay["subject_box_confidence"] = round(float(confidence), 2)
            features["subject_ratio"] = round(_subject_ratio(box), 3)
        except Exception as exc:
            warnings.append(f"主体占比计算失败：{type(exc).__name__}: {exc}")
            box = None
    if box is None:
        warnings.append("未能估计主体框，overlay.subject_box = null")

    try:
        features["bg_complexity"] = round(_bg_complexity(gray), 3)
    except Exception as exc:
        warnings.append(f"背景复杂度估计失败：{type(exc).__name__}: {exc}")

    try:
        features["light_direction"] = _light_direction(img, gray, box)
    except Exception as exc:
        warnings.append(f"光线方向估计失败：{type(exc).__name__}: {exc}")

    try:
        features["gaze_space"] = _gaze_space(box)
    except Exception as exc:
        warnings.append(f"视线留白估计失败：{type(exc).__name__}: {exc}")

    try:
        overlay["horizon_y"] = _horizon_y(gray)
    except Exception as exc:
        warnings.append(f"地平线估计失败：{type(exc).__name__}: {exc}")

    try:
        focus_ok, focus_warning = _focus_estimate(gray)
        if focus_warning:
            warnings.append(focus_warning)
    except Exception as exc:
        warnings.append(f"对焦估计失败，默认判为正常：{type(exc).__name__}: {exc}")
        focus_ok = 1

    # 归一化：确保输出字段类型/范围可靠
    try:
        features["subject_ratio"] = round(_clamp(float(features["subject_ratio"])), 3)
        features["bg_complexity"] = round(_clamp(float(features["bg_complexity"])), 3)
        features["gaze_space"] = round(_clamp(float(features["gaze_space"])), 3)
        if features["light_direction"] not in LIGHT_DIRECTIONS:
            features["light_direction"] = "front"
    except Exception:  # pragma: no cover
        features = dict(DEFAULT_FEATURES)

    return {
        "frame_features": features,
        "overlay": overlay,
        "focus_ok": int(focus_ok),
        "warnings": warnings,
    }


#: 兼容别名（tools / 旧脚本可能用这个名字）
extract_frame = extract


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    import json
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m server.features <图片路径>")
        raise SystemExit(2)
    with open(sys.argv[1], "rb") as fh:
        result = extract(fh.read())
    print(json.dumps(result, ensure_ascii=False, indent=2))
