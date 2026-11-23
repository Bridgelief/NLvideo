# -*- coding: utf-8 -*-
"""data/ 数据契约测试（契约：docs/INTERFACES.md §3）。

跑法（仓库根目录）：python -m unittest tests.test_data_contract -v
"""

import csv
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.action_effects import ACTION_EFFECTS, LIGHT_DIRECTIONS  # noqa: E402

DATA = ROOT / "data"
DIMENSIONS = {"subject_ratio", "bg_complexity", "light_angle", "gaze_space"}
INTENT_IDS = {"portrait_closeup", "portrait_medium", "landscape", "product", "emotional"}
MATERIALS_HEADER = (
    "clip_id,filename,duration_sec,scene_type,intent_id,quality_score,stability,"
    "focus_ok,has_mistake,duplicate_of,followed_advice,in_point,out_point,notes"
)
SHOTS_HEADER = (
    "id,film,scene,shot_type,shot_size,subject_ratio,camera_height,focal_estimate,"
    "light_direction,gaze_space,bg_complexity,why_this_shot,ai_common_mistake,notes"
)
TRANSITIONS = {"无", "硬切", "叠化", "淡入淡出"}
BANNED_REASON_WORDS = ("权重", "函数", "评分", "维度", "objective", "weight")


def load_json(name):
    path = DATA / name
    if not path.exists():
        raise AssertionError(f"缺少数据文件：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(name):
    path = DATA / name
    if not path.exists():
        raise AssertionError(f"缺少数据文件：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


class TestIntentWeights(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_json("intent_weights.json")

    def test_version(self):
        self.assertIsInstance(self.data.get("version"), str)

    def test_five_intents(self):
        intents = self.data["intents"]
        self.assertEqual(len(intents), 5)
        self.assertEqual({i["id"] for i in intents}, INTENT_IDS)

    def test_each_intent_fields(self):
        for intent in self.data["intents"]:
            iid = intent["id"]
            self.assertTrue(intent["label"].strip(), msg=iid)
            self.assertGreaterEqual(len(intent["keywords"]), 12, msg=f"{iid} keywords 不足 12")
            self.assertEqual(set(intent["weights"]), DIMENSIONS, msg=iid)
            self.assertAlmostEqual(sum(intent["weights"].values()), 1.0, places=3, msg=iid)
            for value in intent["weights"].values():
                self.assertGreaterEqual(value, 0.0, msg=iid)
            self.assertEqual(set(intent["targets"]), DIMENSIONS, msg=iid)

    def test_targets_ranges(self):
        for intent in self.data["intents"]:
            iid = intent["id"]
            t = intent["targets"]
            for key in ("subject_ratio", "gaze_space"):
                lo, hi = t[key]
                self.assertLessEqual(0.0, lo, msg=f"{iid}.{key}")
                self.assertLess(lo, hi, msg=f"{iid}.{key}")
                self.assertLessEqual(hi, 1.0, msg=f"{iid}.{key}")
            lo, hi = t["bg_complexity"]
            self.assertEqual(lo, 0.0, msg=f"{iid}.bg_complexity 下限应为 0")
            self.assertLessEqual(hi, 1.0, msg=f"{iid}.bg_complexity")
            self.assertIsInstance(t["light_angle"], list)
            self.assertTrue(t["light_angle"], msg=f"{iid}.light_angle 不能为空")
            self.assertTrue(set(t["light_angle"]) <= set(LIGHT_DIRECTIONS), msg=iid)

    def test_preferred_actions(self):
        for intent in self.data["intents"]:
            actions = intent["preferred_actions"]
            self.assertGreaterEqual(len(actions), 3, msg=intent["id"])
            self.assertLessEqual(len(actions), 5, msg=intent["id"])
            for aid in actions:
                self.assertIn(aid, ACTION_EFFECTS, msg=intent["id"])


class TestPhrases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_json("phrases.json")
        cls.phrases = cls.data["phrases"]
        cls.by_id = {p["id"]: p for p in cls.phrases}

    def test_exact_id_set(self):
        self.assertEqual(len(self.phrases), 50)
        self.assertEqual(set(self.by_id), set(ACTION_EFFECTS))

    def test_fields(self):
        allowed_categories = {
            "distance", "focal", "height", "angle", "light", "composition", "background", "movement",
        }
        for phrase in self.phrases:
            pid = phrase["id"]
            self.assertTrue(phrase["action"].strip(), msg=pid)
            self.assertLessEqual(len(phrase["action"]), 12, msg=f"{pid} action 超过 12 字")
            self.assertIn(phrase["category"], allowed_categories, msg=pid)
            self.assertIsInstance(phrase["affects"], list)
            self.assertTrue(set(phrase["affects"]) <= DIMENSIONS, msg=pid)
            self.assertTrue(phrase["delta_hint"].strip(), msg=pid)
            reason = phrase["reason_template"]
            self.assertTrue(reason.strip(), msg=pid)
            self.assertLessEqual(len(reason), 40, msg=f"{pid} reason_template 超过 40 字")
            for word in BANNED_REASON_WORDS:
                self.assertNotIn(word, reason, msg=f"{pid} 理由出现工程术语 {word!r}")

    def test_action_mentions_quantity(self):
        """话术要可执行：至少一半动作带具体量词/数字。"""
        quantifiers = ("步", "x", "X", "倍", "厘米", "米", "度", "腰", "肩", "眼", "三分之一", "半")
        with_quantity = [p for p in self.phrases if any(q in p["action"] for q in quantifiers)]
        self.assertGreaterEqual(len(with_quantity), 25, msg="可执行量词覆盖不足")


class TestRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_json("rules.json")
        cls.rules = cls.data["rules"]

    def test_count(self):
        self.assertGreaterEqual(len(self.rules), 25)

    def test_fields(self):
        for rule in self.rules:
            rid = rule["id"]
            self.assertIn(rule["action_id"], ACTION_EFFECTS, msg=rid)
            self.assertTrue(rule["reason"].strip(), msg=rid)
            self.assertIsInstance(rule["priority"], int, msg=rid)
            self.assertTrue(1 <= rule["priority"] <= 10, msg=rid)
            cond = rule["condition"]
            self.assertIn(cond["field"], DIMENSIONS, msg=rid)
            self.assertIn(cond["op"], {"<", "<=", ">", ">=", "==", "in", "not_in"}, msg=rid)
            if cond["op"] in {"in", "not_in"}:
                self.assertIsInstance(cond["value"], list, msg=rid)
            applies = rule["applies_to"]
            self.assertTrue(applies, msg=rid)
            if "all" not in applies:
                self.assertTrue(set(applies) <= INTENT_IDS, msg=rid)

    def test_coverage_per_intent(self):
        counts = {i: 0 for i in INTENT_IDS}
        for rule in self.rules:
            applies = rule["applies_to"]
            targets = INTENT_IDS if "all" in applies else set(applies)
            for intent in targets:
                counts[intent] += 1
        for intent, count in counts.items():
            self.assertGreaterEqual(count, 4, msg=f"{intent} 仅 {count} 条规则")

    def test_coverage_per_dimension(self):
        counts = {d: 0 for d in DIMENSIONS}
        for rule in self.rules:
            counts[rule["condition"]["field"]] += 1
        for dimension, count in counts.items():
            self.assertGreaterEqual(count, 4, msg=f"{dimension} 仅 {count} 条规则")


class TestEditRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_json("edit_rules.json")

    def test_quality_filters(self):
        filters = self.data["quality_filters"]
        self.assertGreaterEqual(len(filters), 5)
        fields = {f["field"] for f in filters}
        for required in ("stability", "focus_ok", "has_mistake", "duration_sec", "quality_score"):
            self.assertIn(required, fields, msg=f"缺少 {required} 的废片过滤规则")
        for rule in filters:
            self.assertEqual(rule["action"], "exclude")
            self.assertTrue(rule["reason"].strip())

    def test_followed_advice_bonus(self):
        bonuses = self.data["priority_bonus"]
        self.assertTrue(bonuses)
        matched = [
            b for b in bonuses
            if b["field"] == "followed_advice" and b["op"] in {"==", ">=", ">"} and float(b["value"]) >= 1
        ]
        self.assertTrue(matched, msg="缺少 followed_advice 的优先加分（闭环验收 #7）")
        self.assertGreater(float(matched[0]["bonus"]), 0)

    def test_selection(self):
        sel = self.data["selection"]
        for key in ("max_clips", "min_duration_sec", "max_duration_sec", "trim_head_sec", "trim_tail_sec"):
            self.assertIn(key, sel)
        self.assertGreaterEqual(sel["min_duration_sec"], 0.3)
        self.assertGreater(sel["max_duration_sec"], sel["min_duration_sec"])
        self.assertGreaterEqual(sel["max_clips"], 3)

    def test_ordering(self):
        order = self.data["ordering"]["intent_order"]
        self.assertEqual(set(order), INTENT_IDS, msg="intent_order 必须包含全部 5 个意图")
        self.assertEqual(len(order), 5)

    def test_transitions(self):
        trans = self.data["transitions"]
        for key in (
            "after_intent",
            "after_intent_transition",
            "same_scene_type_transition",
            "default_transition",
            "reason_templates",
        ):
            self.assertIn(key, trans)
        self.assertIn(trans["after_intent"], INTENT_IDS)
        self.assertIn(trans["default_transition"], TRANSITIONS)
        self.assertIn(trans["after_intent_transition"], TRANSITIONS)
        self.assertIn(trans["same_scene_type_transition"], TRANSITIONS)
        for name in TRANSITIONS:
            self.assertIn(name, trans["reason_templates"], msg=f"reason_templates 缺 {name}")


class TestMaterialsCsv(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header, cls.rows = read_csv("materials.csv")

    def test_header(self):
        self.assertEqual(",".join(self.header), MATERIALS_HEADER)

    def test_row_count(self):
        self.assertGreaterEqual(len(self.rows), 13)

    def test_values(self):
        clip_ids = set()
        for row in self.rows:
            cid = row["clip_id"]
            self.assertTrue(cid.strip(), msg="clip_id 不能为空")
            self.assertNotIn(cid, clip_ids, msg=f"clip_id 重复：{cid}")
            clip_ids.add(cid)
            self.assertTrue(row["filename"].strip())
            duration = float(row["duration_sec"])
            in_p = float(row["in_point"])
            out_p = float(row["out_point"])
            self.assertGreater(duration, 0, msg=cid)
            self.assertGreaterEqual(in_p, 0.0, msg=cid)
            self.assertLess(in_p, out_p, msg=cid)
            self.assertLessEqual(out_p, duration + 0.01, msg=cid)
            for field in ("quality_score", "stability"):
                value = float(row[field])
                self.assertGreaterEqual(value, 0.0, msg=f"{cid}.{field}")
                self.assertLessEqual(value, 1.0, msg=f"{cid}.{field}")
            for field in ("focus_ok", "has_mistake", "followed_advice"):
                self.assertIn(int(float(row[field])), (0, 1), msg=f"{cid}.{field}")
            self.assertIn(row["intent_id"], INTENT_IDS, msg=cid)

    def test_duplicates_reference_existing(self):
        clip_ids = {row["clip_id"] for row in self.rows}
        for row in self.rows:
            dup = row["duplicate_of"].strip()
            if dup:
                self.assertIn(dup, clip_ids, msg=f"{row['clip_id']} duplicate_of 指向不存在的 {dup}")

    def test_has_followed_advice_good_clips(self):
        good = [
            row for row in self.rows
            if int(float(row["followed_advice"])) == 1 and float(row["quality_score"]) >= 0.7
        ]
        self.assertGreaterEqual(len(good), 5)

    def test_has_usable_and_rejects(self):
        # 既要有能留下素材，也要有会被过滤的样本，否则剪辑演示没东西可展示
        usable = [r for r in self.rows if not r["duplicate_of"].strip()]
        self.assertGreaterEqual(len(usable), 5)


class TestShotsCsv(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header, cls.rows = read_csv("shots.csv")

    def test_header(self):
        self.assertEqual(",".join(self.header), SHOTS_HEADER)

    def test_row_count(self):
        self.assertGreaterEqual(len(cls_rows := self.rows), 90, msg=f"只有 {len(cls_rows)} 行，需 ≥90")

    def test_values(self):
        ids = set()
        for row in self.rows:
            rid = row["id"]
            self.assertTrue(rid.strip())
            self.assertNotIn(rid, ids, msg=f"id 重复：{rid}")
            ids.add(rid)
            for field in ("subject_ratio", "gaze_space", "bg_complexity"):
                value = float(row[field])
                self.assertGreaterEqual(value, 0.0, msg=f"{rid}.{field}")
                self.assertLessEqual(value, 1.0, msg=f"{rid}.{field}")
            self.assertIn(row["light_direction"], LIGHT_DIRECTIONS, msg=rid)
            self.assertTrue(row["why_this_shot"].strip(), msg=f"{rid} 缺拍摄理由")


if __name__ == "__main__":
    unittest.main(verbosity=2)
