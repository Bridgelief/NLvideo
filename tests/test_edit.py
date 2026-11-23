# -*- coding: utf-8 -*-
"""剪辑引擎契约测试（契约：docs/INTERFACES.md §2.4、§7 验收 #6/#7）。

跑法（仓库根目录）：python -m unittest tests.test_edit -v
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import edit_engine as ee  # noqa: E402

TRANSITIONS = {"无", "硬切", "叠化", "淡入淡出"}


class TestEditRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = ee.load_edit_rules()
        cls.materials = ee.load_materials(None)
        cls.result = ee.run()

    def test_top_level_structure(self):
        for key in ("organization", "rough_cut", "source", "warnings"):
            self.assertIn(key, self.result)
        self.assertEqual(self.result["source"], "materials.csv")
        self.assertIsInstance(self.result["warnings"], list)

    def test_counts_add_up(self):
        org = self.result["organization"]
        total = len(self.materials)
        self.assertEqual(org["total"], total)
        self.assertEqual(org["usable"], len(org["usable_ids"]))
        self.assertEqual(
            org["total"],
            org["usable"] + org["excluded_count"] + org["duplicate_count"],
            msg="可用 + 剔除 + 重复 必须等于总数",
        )
        self.assertGreaterEqual(org["usable"], 3, msg="可用素材太少，粗剪没法演示")

    def test_duplicates_reported(self):
        expected = {row["clip_id"] for row in self.materials if row["duplicate_of"].strip()}
        got = {d["clip_id"] for d in self.result["organization"]["duplicates"]}
        self.assertEqual(got, expected)
        for dup in self.result["organization"]["duplicates"]:
            self.assertTrue(dup["duplicate_of"].strip())

    def test_excluded_have_reasons(self):
        excluded = self.result["organization"]["excluded"]
        self.assertTrue(excluded, msg="示例素材里应有会被过滤掉的废片，否则演示不了自动筛选")
        for item in excluded:
            self.assertTrue(item["clip_id"])
            self.assertTrue(item["reason"].strip())
            self.assertTrue(item["rule_id"].strip())

    def test_rough_cut_timeline(self):
        cut = self.result["rough_cut"]
        self.assertGreater(cut["clip_count"], 0)
        self.assertLessEqual(cut["clip_count"], self.rules["selection"]["max_clips"])
        self.assertAlmostEqual(
            cut["total_duration_sec"],
            round(sum(item["duration_sec"] for item in cut["timeline"]), 2),
            places=1,
        )

    def test_timeline_entries(self):
        cut = self.result["rough_cut"]
        for i, item in enumerate(cut["timeline"]):
            for key in (
                "order",
                "clip_id",
                "filename",
                "in",
                "out",
                "duration_sec",
                "timeline_in",
                "transition_in",
                "priority",
                "reason",
            ):
                self.assertIn(key, item, msg=f"第 {i + 1} 条时间线缺 {key}")
            self.assertEqual(item["order"], i + 1)
            self.assertTrue(item["filename"].strip())
            self.assertIn(item["transition_in"], TRANSITIONS, msg=item["clip_id"])
            self.assertTrue(item["reason"].strip(), msg=f"{item['clip_id']} 保留理由不能为空")
            self.assertGreater(item["out"], item["in"], msg=item["clip_id"])
            self.assertAlmostEqual(item["duration_sec"], round(item["out"] - item["in"], 2), places=2)

    def test_timeline_contiguous(self):
        timeline = self.result["rough_cut"]["timeline"]
        self.assertEqual(timeline[0]["timeline_in"], 0.0)
        self.assertEqual(timeline[0]["transition_in"], "无")
        for prev, cur in zip(timeline, timeline[1:]):
            expected = prev["timeline_in"] + prev["duration_sec"]
            self.assertAlmostEqual(cur["timeline_in"], expected, delta=0.02, msg=cur["clip_id"])

    def test_durations_within_bounds(self):
        sel = self.rules["selection"]
        for item in self.result["rough_cut"]["timeline"]:
            self.assertGreaterEqual(item["duration_sec"], sel["min_duration_sec"] - 0.01, msg=item["clip_id"])
            self.assertLessEqual(item["duration_sec"], sel["max_duration_sec"] + 0.01, msg=item["clip_id"])

    def test_ordering_respects_intent_arc(self):
        order_map = {k: i for i, k in enumerate(self.rules["ordering"]["intent_order"])}
        intent_of = {row["clip_id"]: row["intent_id"] for row in self.materials}
        ranks = [order_map.get(intent_of[item["clip_id"]], 99) for item in self.result["rough_cut"]["timeline"]]
        self.assertEqual(ranks, sorted(ranks), msg="时间线未按 intent_order 叙事弧排序")

    def test_followed_advice_is_prioritized(self):
        """闭环验收 #7：按建议调整过的素材在剪辑里被优先保留（加分可观察）。"""
        rows = [row for row in self.materials if not row["duplicate_of"].strip()]
        followed = [row for row in rows if int(float(row["followed_advice"])) == 1]
        self.assertTrue(followed, msg="素材里需要 followed_advice=1 的样本")
        priorities = {item["clip_id"]: item["priority"] for item in self.result["rough_cut"]["timeline"]}
        for row in rows:
            if row["clip_id"] not in priorities:
                continue
            quality = float(row["quality_score"])
            if int(float(row["followed_advice"])) == 1:
                self.assertGreater(priorities[row["clip_id"]], quality + 0.05, msg=row["clip_id"])

    def test_reason_mentions_advice_for_followed_clip(self):
        followed_ids = {
            row["clip_id"] for row in self.materials
            if int(float(row["followed_advice"])) == 1 and not row["duplicate_of"].strip()
        }
        cut_followed = [item for item in self.result["rough_cut"]["timeline"] if item["clip_id"] in followed_ids]
        self.assertTrue(cut_followed, msg="时间线里应至少保留一条按建议调整过的素材")
        self.assertTrue(
            any("建议" in item["reason"] for item in cut_followed),
            msg="保留理由里应能看到“按建议调整过”之类的说明",
        )

    def test_deterministic(self):
        again = ee.run()
        self.assertEqual(
            json.dumps(self.result, sort_keys=True, ensure_ascii=False),
            json.dumps(again, sort_keys=True, ensure_ascii=False),
        )

    def test_accepts_row_list(self):
        result = ee.run(materials=self.materials)
        self.assertEqual(result["organization"]["total"], len(self.materials))
        self.assertGreater(result["rough_cut"]["clip_count"], 0)

    def test_ignores_extra_columns(self):
        rows = [dict(row) for row in self.materials]
        rows[0]["before_url"] = "/output/shots/x_before.jpg"
        result = ee.run(materials=rows)
        self.assertEqual(result["organization"]["total"], len(rows))


class TestEditCli(unittest.TestCase):
    def test_cli_json_only(self):
        import subprocess

        proc = subprocess.run(
            [sys.executable, "engine/edit_engine.py", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn("timeline", data)
        self.assertGreater(data["clip_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
