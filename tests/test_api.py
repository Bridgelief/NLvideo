# -*- coding: utf-8 -*-
"""HTTP API 契约测试（契约：docs/INTERFACES.md §4）。

用 FastAPI TestClient，不需要真的起端口。
跑法（仓库根目录）：python -m unittest tests.test_api -v
"""

import base64
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from server.app import app  # noqa: E402

FEATURES = {
    "subject_ratio": 0.18,
    "bg_complexity": 0.42,
    "light_direction": "front",
    "gaze_space": 0.30,
}


class ApiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        demo = cls.client.get("/api/demo")
        assert demo.status_code == 200, demo.text
        scenarios = demo.json()["scenarios"]
        assert len(scenarios) >= 4, "demo 场景不足 4 个"
        cls.scenarios = scenarios
        frame = cls.client.get(scenarios[0]["frame_url"])
        assert frame.status_code == 200, frame.text
        cls.frame_bytes = frame.content
        cls.frame_data_url = "data:image/jpeg;base64," + base64.b64encode(cls.frame_bytes).decode("ascii")

    def setUp(self):
        self.client.post("/api/shots/reset")

    def tearDown(self):
        self.client.post("/api/shots/reset")


class TestHealthAndDemo(ApiTestBase):
    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertIn("version", data)
        self.assertIsInstance(data["ffmpeg"], bool)
        counts = data["counts"]
        self.assertEqual(counts["intents"], 5)
        self.assertEqual(counts["phrases"], 50)
        self.assertGreaterEqual(counts["rules"], 25)
        self.assertGreaterEqual(counts["materials"], 13)

    def test_demo_scenarios(self):
        for scenario in self.scenarios:
            for key in ("id", "label", "intent", "frame_url", "expected_features"):
                self.assertIn(key, scenario)
            self.assertIn(scenario["expected_features"]["light_direction"], ("front", "side", "back", "low", "golden"))
            frame = self.client.get(scenario["frame_url"])
            self.assertEqual(frame.status_code, 200, scenario["frame_url"])
            self.assertGreater(len(frame.content), 1000, msg="demo 帧不能是空图")


class TestSuggestApi(ApiTestBase):
    def test_suggest_with_features(self):
        r = self.client.post(
            "/api/suggest",
            json={"session_id": "test", "intent": "电影感人物特写，背景虚化", "frame_features": FEATURES},
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["intent_detected"], "portrait_closeup")
        self.assertGreaterEqual(len(data["suggestions"]), 1)
        self.assertLessEqual(len(data["suggestions"]), 3)
        for sug in data["suggestions"]:
            self.assertTrue(sug["action"].strip())
            self.assertTrue(sug["reason"].strip())
            self.assertNotIn("权重", sug["reason"])
        self.assertIn("current_score", data)
        self.assertIn("overlay", data)
        self.assertIn("timing_ms", data)
        self.assertIn("total", data["timing_ms"])

    def test_suggest_with_image_under_5_seconds(self):
        """验收 #2：按按钮 5 秒内出建议（本机不含网络延迟）。"""
        start = time.perf_counter()
        r = self.client.post(
            "/api/suggest",
            json={
                "session_id": "test",
                "intent": "电影感人物特写，背景虚化",
                "frame_base64": self.frame_data_url,
            },
        )
        elapsed = time.perf_counter() - start
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertLess(elapsed, 5.0, msg=f"端到端 {elapsed:.2f}s，超过 5 秒")
        feats = data["frame_features"]
        for key in ("subject_ratio", "bg_complexity", "light_direction", "gaze_space"):
            self.assertIn(key, feats)
        self.assertGreaterEqual(len(data["suggestions"]), 1)

    def test_missing_frame_is_400(self):
        r = self.client.post("/api/suggest", json={"intent": "电影感人物特写"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("detail", r.json())

    def test_feedback(self):
        r = self.client.post(
            "/api/feedback",
            json={
                "session_id": "test",
                "action_id": "step_forward",
                "rank": 1,
                "useful": True,
                "intent_id": "portrait_closeup",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreaterEqual(r.json()["recorded"], 1)


class TestShotsAndCutApi(ApiTestBase):
    def _post_shot(self, quality: float, followed: bool, action_ids=None):
        return self.client.post(
            "/api/shots",
            json={
                "session_id": "test",
                "intent": "电影感人物特写，背景虚化",
                "intent_id": "portrait_closeup",
                "quality_score": quality,
                "followed_advice": followed,
                "advice_action_ids": action_ids or ["step_forward"],
                "duration_sec": 6.0,
                "frame_features": FEATURES,
                "before_image": self.frame_data_url,
                "after_image": self.frame_data_url,
                "stability": 0.9,
                "focus_ok": 1,
                "has_mistake": 0,
            },
        )

    def test_shot_lifecycle_and_edit(self):
        """验收 #6/#7：记录 5 条素材 → 自动整理 + 粗剪，按建议调整过的优先。"""
        posted = []
        for i in range(5):
            r = self._post_shot(quality=0.62 + i * 0.05, followed=(i >= 2))
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertTrue(data["ok"])
            self.assertTrue(data["clip_id"].startswith("S"))
            posted.append(data)
        self.assertEqual([p["clip_id"] for p in posted], sorted(p["clip_id"] for p in posted))

        listed = self.client.get("/api/shots").json()
        self.assertEqual(listed["count"], 5)
        first = listed["clips"][0]
        self.assertEqual(self.client.get(first["before_url"]).status_code, 200)
        self.assertEqual(self.client.get(first["after_url"]).status_code, 200)

        edit = self.client.post("/api/edit", json={"source": "shots"})
        self.assertEqual(edit.status_code, 200, edit.text)
        data = edit.json()
        self.assertGreaterEqual(data["organization"]["usable"], 3)
        timeline = data["rough_cut"]["timeline"]
        self.assertGreaterEqual(len(timeline), 3, msg="5 条素材至少要有 3 条进粗剪")
        self.assertFalse(data["ffmpeg"]["available"])
        self.assertTrue(data["ffmpeg"]["single_command"].strip())
        # 验收 #7：按建议调整过的片段在时间线里优先级更高
        quality_of = {p["clip_id"]: p["row"]["quality_score"] for p in posted}
        followed_ids = {p["clip_id"] for p in posted if p["row"]["followed_advice"] == 1}
        cut = {item["clip_id"]: item for item in timeline}
        matched = followed_ids & set(cut)
        self.assertTrue(matched, msg="按建议调整过的素材应在粗剪里")
        for cid in matched:
            self.assertGreater(
                cut[cid]["priority"],
                float(quality_of[cid]) + 0.05,
                msg=f"{cid} 的优先级应体现 followed_advice 加分",
            )

        reset = self.client.post("/api/shots/reset")
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(self.client.get("/api/shots").json()["count"], 0)

    def test_edit_source_materials(self):
        r = self.client.post("/api/edit", json={"source": "materials"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertGreaterEqual(data["organization"]["total"], 13)
        self.assertGreaterEqual(data["rough_cut"]["clip_count"], 3)
        self.assertIn("single_command", data["ffmpeg"])
        self.assertEqual(data["source"], "materials")

    def test_edit_defaults_to_materials_when_no_shots(self):
        r = self.client.post("/api/edit", json={"source": "shots"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data.get("warnings"), msg="没有拍摄记录时应回退 materials 并给 warnings")
        self.assertGreaterEqual(data["rough_cut"]["clip_count"], 1)

    def test_materials_list(self):
        r = self.client.get("/api/materials")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertGreaterEqual(data["count"], 13)
        self.assertEqual(len(data["rows"]), data["count"])


class TestExportsAndStatic(ApiTestBase):
    def test_export_json(self):
        self.client.post("/api/edit", json={"source": "materials"})
        r = self.client.get("/api/edit/export/rough_cut.json")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("attachment", r.headers.get("content-disposition", ""))
        payload = json.loads(r.content)
        self.assertIn("rough_cut", payload)
        self.assertIn("edl", payload)

    def test_export_bat(self):
        self.client.post("/api/edit", json={"source": "materials"})
        r = self.client.get("/api/edit/export/rough_cut.bat")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.text.startswith("@echo off"), r.text[:80])
        self.assertIn("ffmpeg", r.text)

    def test_frontend_index(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/html", r.headers.get("content-type", ""))
        body = r.text
        for element_id in ("video", "btn-suggest", "intent-input", "suggestion-list", "panel-cut"):
            self.assertIn(f'id="{element_id}"', body, msg=f"前端缺 #{element_id}")

    def test_unknown_api_is_404_json(self):
        r = self.client.get("/api/definitely-not-exist")
        self.assertEqual(r.status_code, 404)
        self.assertIn("detail", r.json())

    def test_dev_ca_download(self):
        r = self.client.get("/api/dev/ca.pem")
        ca = Path(ROOT) / "certs" / "ca.pem"
        if ca.exists():
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("BEGIN CERTIFICATE", r.text)
        else:
            self.assertEqual(r.status_code, 404)
            self.assertIn("detail", r.json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
