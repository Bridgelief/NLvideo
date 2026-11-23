# -*- coding: utf-8 -*-
"""画面特征提取契约测试（契约：docs/INTERFACES.md §4.3、§4.3.1）。

跑法（仓库根目录）：python -m unittest tests.test_features -v
"""

import base64
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import features as F  # noqa: E402
from engine.action_effects import LIGHT_DIRECTIONS  # noqa: E402


def make_portrait_frame() -> bytes:
    """画一张"人像构图"测试帧：暗背景 + 中间偏左的亮脸 + 肩部。"""
    h, w = 480, 640
    img = np.zeros((h, w, 3), np.uint8)
    # 背景：左上暗、右下亮的渐变（模拟侧光）
    ys, xs = np.mgrid[0:h, 0:w]
    img[..., 2] = np.clip(40 + 120 * xs / w, 0, 255)
    img[..., 1] = np.clip(30 + 60 * ys / h, 0, 255)
    img[..., 0] = np.clip(25 + 40 * xs / w, 0, 255)
    # 肩部 + 脸部
    cv2.ellipse(img, (300, 430), (150, 90), 0, 180, 360, (110, 140, 165), -1)
    cv2.circle(img, (300, 210), 85, (150, 185, 210), -1)
    cv2.circle(img, (272, 195), 6, (30, 30, 30), -1)
    cv2.circle(img, (330, 195), 6, (30, 30, 30), -1)
    cv2.ellipse(img, (300, 245), (28, 12), 0, 0, 180, (60, 60, 60), 2)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


class TestExtract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame_bytes = make_portrait_frame()
        cls.result = F.extract(cls.frame_bytes)

    def test_structure(self):
        for key in ("frame_features", "overlay", "focus_ok", "warnings"):
            self.assertIn(key, self.result)

    def test_frame_features(self):
        feats = self.result["frame_features"]
        for key in ("subject_ratio", "bg_complexity", "light_direction", "gaze_space"):
            self.assertIn(key, feats)
        for key in ("subject_ratio", "bg_complexity", "gaze_space"):
            self.assertGreaterEqual(feats[key], 0.0)
            self.assertLessEqual(feats[key], 1.0)
        self.assertIn(feats["light_direction"], LIGHT_DIRECTIONS)

    def test_overlay(self):
        overlay = self.result["overlay"]
        box = overlay.get("subject_box")
        if box is not None:
            self.assertEqual(len(box), 4)
            x, y, w, h = [float(v) for v in box]
            self.assertGreaterEqual(x, 0.0)
            self.assertGreaterEqual(y, 0.0)
            self.assertGreater(w, 0.0)
            self.assertGreater(h, 0.0)
            self.assertLessEqual(x + w, 1.001)
            self.assertLessEqual(y + h, 1.001)
        self.assertTrue(overlay.get("thirds"))
        self.assertGreaterEqual(float(overlay.get("horizon_y", 0.5)), 0.0)
        self.assertLessEqual(float(overlay.get("horizon_y", 0.5)), 1.0)

    def test_focus_flag(self):
        self.assertIn(int(self.result["focus_ok"]), (0, 1))

    def test_bad_input_degrades(self):
        for bad in (b"", b"not-an-image-at-all"):
            out = F.extract(bad)
            self.assertIn("frame_features", out)
            self.assertTrue(out.get("warnings"), msg="坏图必须给 warnings 而不是抛异常")
            self.assertIn(out["frame_features"]["light_direction"], LIGHT_DIRECTIONS)


class TestDecodeImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame_bytes = make_portrait_frame()
        cls.raw_b64 = base64.b64encode(cls.frame_bytes).decode("ascii")

    def test_raw_base64(self):
        img = F.decode_image(self.raw_b64)
        self.assertIsNotNone(img)
        self.assertEqual(img.ndim, 3)
        self.assertEqual(img.shape[2], 3)

    def test_data_url(self):
        img = F.decode_image("data:image/jpeg;base64," + self.raw_b64)
        self.assertIsNotNone(img)

    def test_invalid_returns_none(self):
        self.assertIsNone(F.decode_image("definitely-not-base64!!!"))

    def test_roundtrip_through_extract(self):
        out = F.extract(self.frame_bytes)
        self.assertIn(out["frame_features"]["light_direction"], LIGHT_DIRECTIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
