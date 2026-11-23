# -*- coding: utf-8 -*-
"""EDL / FFmpeg 导出契约测试（契约：docs/INTERFACES.md §2.5）。

本机没有 ffmpeg 可执行文件：测试重点验证"检测不到也必须给出可执行命令"。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import edl_export as ex  # noqa: E402

TIMELINE = [
    {
        "order": 1,
        "clip_id": "C001",
        "filename": "shot_001.mp4",
        "in": 0.7,
        "out": 4.2,
        "duration_sec": 3.5,
        "timeline_in": 0.0,
        "transition_in": "无",
        "priority": 0.87,
        "reason": "开篇镜头",
    },
    {
        "order": 2,
        "clip_id": "C002",
        "filename": "shot_002.mp4",
        "in": 0.3,
        "out": 2.8,
        "duration_sec": 2.5,
        "timeline_in": 3.5,
        "transition_in": "叠化",
        "priority": 0.91,
        "reason": "按建议调整过",
    },
]

ROUGH_CUT = {"clip_count": 2, "total_duration_sec": 6.0, "timeline": TIMELINE}


class TestBuildFfmpeg(unittest.TestCase):
    def test_structure(self):
        out = ex.build_ffmpeg(TIMELINE)
        for key in ("available", "ffmpeg_path", "single_command", "batch_script", "notes"):
            self.assertIn(key, out)
        self.assertIsInstance(out["available"], bool)
        self.assertIsInstance(out["notes"], list)

    def test_command_present_even_without_ffmpeg(self):
        out = ex.build_ffmpeg(TIMELINE, base_dir="inputs", output="output/rough_cut.mp4")
        self.assertTrue(str(out["single_command"]).strip(), msg="没有 ffmpeg 也必须给出命令文本")
        self.assertIn("ffmpeg", out["single_command"])
        self.assertIn("shot_001.mp4", out["single_command"])
        self.assertIn("shot_002.mp4", out["single_command"])
        self.assertTrue(out["batch_script"].startswith("@echo off"), msg=".bat 必须以 @echo off 开头")
        self.assertIn("ffmpeg", out["batch_script"])
        if not out["available"]:
            self.assertTrue(out["notes"], msg="没有 ffmpeg 时要在 notes 里说明")

    def test_empty_timeline_does_not_raise(self):
        out = ex.build_ffmpeg([])
        self.assertIsInstance(out, dict)
        self.assertIn("single_command", out)


class TestOtherExports(unittest.TestCase):
    def test_cmx3600(self):
        text = ex.build_cmx3600(TIMELINE)
        self.assertIsInstance(text, str)
        self.assertTrue(text.strip())
        self.assertIn("shot_001.mp4", text)

    def test_edl_json(self):
        data = ex.build_edl_json(ROUGH_CUT, source="materials.csv")
        self.assertIsInstance(data, dict)
        self.assertIn("timeline", data)
        self.assertEqual(len(data["timeline"]), 2)
        self.assertEqual(data.get("source"), "materials.csv")


if __name__ == "__main__":
    unittest.main(verbosity=2)
