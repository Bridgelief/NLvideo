# -*- coding: utf-8 -*-
"""建议引擎契约测试（契约：docs/INTERFACES.md §2、§7）。

跑法（仓库根目录）：python -m unittest tests.test_engine -v
"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.action_effects import ACTION_EFFECTS, LIGHT_DIRECTIONS, validate as validate_effects  # noqa: E402
from engine import suggest_engine as se  # noqa: E402

SAMPLE_FEATURES = {
    "subject_ratio": 0.18,
    "bg_complexity": 0.42,
    "light_direction": "front",
    "gaze_space": 0.30,
}
DIMENSIONS = {"subject_ratio", "bg_complexity", "light_angle", "gaze_space"}
VALID_LABELS = {"人像特写", "人物中景", "风光环境", "产品静物", "情绪叙事"}


def run_cli(*args, stdin=None):
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    return proc


class TestActionEffects(unittest.TestCase):
    def test_validate_clean(self):
        self.assertEqual(validate_effects(), [])

    def test_exactly_50_actions(self):
        self.assertEqual(len(ACTION_EFFECTS), 50)

    def test_light_values_legal(self):
        for aid, effect in ACTION_EFFECTS.items():
            if "l" in effect:
                self.assertIn(effect["l"], LIGHT_DIRECTIONS, msg=aid)


class TestResources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = se.load_resources()

    def test_phrases_exactly_match_action_ids(self):
        self.assertEqual(set(self.res["phrase_map"]), set(ACTION_EFFECTS))

    def test_no_warnings_on_final_data(self):
        self.assertEqual(self.res.get("warnings", []), [], msg=str(self.res.get("warnings")))

    def test_five_intents(self):
        intents = self.res["intent_data"]["intents"]
        self.assertEqual(len(intents), 5)
        ids = {i["id"] for i in intents}
        self.assertEqual(
            ids,
            {"portrait_closeup", "portrait_medium", "landscape", "product", "emotional"},
        )
        for intent in intents:
            self.assertIn(intent["label"], VALID_LABELS)
            self.assertEqual(set(intent["weights"]), DIMENSIONS)
            self.assertAlmostEqual(sum(intent["weights"].values()), 1.0, places=2)
            self.assertEqual(intent["targets"].keys(), DIMENSIONS)
            self.assertGreaterEqual(len(intent["keywords"]), 12)
            for aid in intent["preferred_actions"]:
                self.assertIn(aid, ACTION_EFFECTS)

    def test_rules_valid_and_cover_intents(self):
        rules = self.res["rules_data"]["rules"]
        self.assertGreaterEqual(len(rules), 25)
        intent_ids = {"portrait_closeup", "portrait_medium", "landscape", "product", "emotional"}
        per_intent = {i: 0 for i in intent_ids}
        per_field = {f: 0 for f in DIMENSIONS}
        for rule in rules:
            self.assertIn(rule["action_id"], ACTION_EFFECTS, msg=rule["id"])
            self.assertIn(rule["priority"], range(1, 11), msg=rule["id"])
            self.assertTrue(rule["reason"].strip(), msg=rule["id"])
            cond = rule["condition"]
            self.assertIn(cond["field"], DIMENSIONS, msg=rule["id"])
            self.assertIn(cond["op"], {"<", "<=", ">", ">=", "==", "in", "not_in"}, msg=rule["id"])
            per_field[cond["field"]] += 1
            applies = rule["applies_to"]
            self.assertTrue(applies)
            if "all" not in applies:
                self.assertTrue(set(applies) <= intent_ids, msg=rule["id"])
                for i in applies:
                    per_intent[i] += 1
            else:
                for i in intent_ids:
                    per_intent[i] += 1
        for intent, count in per_intent.items():
            self.assertGreaterEqual(count, 4, msg=f"{intent} 只覆盖 {count} 条规则")
        for field, count in per_field.items():
            self.assertGreaterEqual(count, 4, msg=f"{field} 只覆盖 {count} 条规则")

    def test_resources_cached_equivalently(self):
        self.assertEqual(se.load_resources(), se.load_resources())


class TestSuggestContract(unittest.TestCase):
    def test_output_shape(self):
        out = se.suggest("电影感人物特写，背景虚化", SAMPLE_FEATURES)
        for key in ("intent_detected", "intent_label", "features", "current_score", "suggestions"):
            self.assertIn(key, out)
        self.assertEqual(out["intent_detected"], "portrait_closeup")
        self.assertEqual(set(out["current_score"]["components"]), DIMENSIONS)
        self.assertGreaterEqual(len(out["suggestions"]), 1)
        self.assertLessEqual(len(out["suggestions"]), 3)
        for i, sug in enumerate(out["suggestions"], start=1):
            self.assertEqual(sug["rank"], i)
            self.assertIn(sug["action_id"], ACTION_EFFECTS)
            self.assertTrue(str(sug["action"]).strip())
            self.assertIsInstance(sug["delta"], str)
            self.assertIsInstance(sug["expected_gain"], (int, float))
            self.assertTrue(str(sug["reason"]).strip(), msg="reason 不能为空")
            self.assertNotIn(
                "权重",
                str(sug["reason"]),
                msg="理由必须是给用户看的人话，不能出现工程术语",
            )

    def test_scores_bounded_and_features_normalized(self):
        out = se.suggest(
            "产品 电商 质感",
            {"subject_ratio": 2.0, "bg_complexity": -1.0, "light_direction": "front", "gaze_space": 5},
        )
        self.assertTrue(0.0 <= out["current_score"]["total"] <= 1.0)
        for value in out["current_score"]["components"].values():
            self.assertTrue(0.0 <= value <= 1.0)
        feats = out["features"]
        self.assertLessEqual(feats["subject_ratio"], 1.0)
        self.assertGreaterEqual(feats["bg_complexity"], 0.0)
        self.assertLessEqual(feats["gaze_space"], 1.0)

    def test_deterministic(self):
        a = se.suggest("风光 大气 环境", SAMPLE_FEATURES)
        b = se.suggest("风光 大气 环境", SAMPLE_FEATURES)
        self.assertEqual(json.dumps(a, sort_keys=True, ensure_ascii=False), json.dumps(b, sort_keys=True, ensure_ascii=False))

    def test_expected_gain_descending(self):
        out = se.suggest("电影感人物特写，背景虚化", SAMPLE_FEATURES)
        gains = [s["expected_gain"] for s in out["suggestions"]]
        self.assertEqual(gains, sorted(gains, reverse=True))

    def test_empty_intent_defaults_to_medium(self):
        out = se.suggest("", SAMPLE_FEATURES)
        self.assertEqual(out["intent_detected"], "portrait_medium")

    def test_text_that_matches_nothing_defaults_to_medium(self):
        out = se.suggest("随便瞎写一句没有关键词的话", SAMPLE_FEATURES)
        self.assertEqual(out["intent_detected"], "portrait_medium")

    def test_light_mismatch_keeps_partial_credit(self):
        # 人像特写目标光位是 side/back；给 front 时应保留 0.4 的部分分（math_definition §4.3）
        out = se.suggest("特写", {**SAMPLE_FEATURES, "light_direction": "front"})
        self.assertEqual(out["intent_detected"], "portrait_closeup")
        self.assertAlmostEqual(out["current_score"]["components"]["light_angle"], 0.4, places=2)

    def test_each_intent_keyword_roundtrips(self):
        resources = se.load_resources()
        for intent in resources["intent_data"]["intents"]:
            text = " ".join(intent["keywords"][:2])
            out = se.suggest(text, SAMPLE_FEATURES)
            self.assertEqual(
                out["intent_detected"],
                intent["id"],
                msg=f"文本 {text!r} 应命中 {intent['id']}，实际 {out['intent_detected']}",
            )

    def test_fallback_when_no_rules_hit(self):
        resources = copy.deepcopy(se.load_resources())
        resources["rules_data"] = {"version": resources["rules_data"].get("version", "x"), "rules": []}
        out = se.suggest("电影感人物特写，背景虚化", SAMPLE_FEATURES, resources)
        self.assertGreaterEqual(len(out["suggestions"]), 1, msg="无规则命中时必须走 preferred_actions 兜底")

    def test_missing_data_degrades_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                resources = se.load_resources(tmp)
            except FileNotFoundError:
                self.fail("数据目录为空时 load_resources 不应抛异常")
            self.assertTrue(resources.get("warnings"))
            out = se.suggest("电影感人物特写", SAMPLE_FEATURES, resources)
            self.assertIsInstance(out["suggestions"], list)


class TestCli(unittest.TestCase):
    def test_module_cli(self):
        proc = run_cli(
            "-m", "engine.suggest_engine",
            "--intent", "电影感人物特写，背景虚化",
            "--ratio", "0.18", "--bg", "0.42", "--light", "front", "--gaze", "0.3",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["intent_detected"], "portrait_closeup")
        self.assertGreaterEqual(len(data["suggestions"]), 1)

    def test_script_mode_cli(self):
        proc = run_cli("engine/suggest_engine.py", "--intent", "产品 电商 质感")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["intent_detected"], "product")

    def test_stdin_cli(self):
        payload = json.dumps(
            {"intent": "风光 大气 环境", "frame_features": SAMPLE_FEATURES}, ensure_ascii=False
        )
        proc = run_cli("-m", "engine.suggest_engine", "--stdin", stdin=payload)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["intent_detected"], "landscape")


if __name__ == "__main__":
    unittest.main(verbosity=2)
