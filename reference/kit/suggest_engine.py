#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 现场拍摄指导助手 - 建议引擎 v1.0
负责人: Bridge

输入: 用户自然语言意图 + 当前画面特征
输出: Top-3 可执行动作 + 理由 (JSON)

用法:
  python suggest_engine.py --intent "电影感人物特写，背景虚化" --ratio 0.18 --bg 0.42 --light front --gaze 0.30
  echo '{"intent":"...","frame_features":{...}}' | python suggest_engine.py --stdin
  python suggest_engine.py   # 不传参数则跑内置示例

依赖: 仅 Python 标准库
"""

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

# ------------------------------------------------------------------
# 动作对画面特征的估计影响 (数值化)
# 说明: 这是 v1 的近似模型。队友B拉片后可调整数值,
#       后续版本可提取到单独的 action_effects.json。
# 格式: {"r": 主体占比变化, "c": 背景复杂度变化, "g": 视线空间变化, "l": 光线类别替换}
# ------------------------------------------------------------------
ACTION_EFFECTS = {
    "step_back":           {"r": -0.12, "c": -0.05, "g": 0.03},
    "step_back_more":      {"r": -0.20, "c": -0.08},
    "step_forward":        {"r": 0.12, "c": 0.02},
    "step_forward_close":  {"r": 0.18},
    "step_left":           {"g": 0.08, "c": -0.03},
    "step_right":          {"g": 0.08, "c": -0.03},
    "orbit_subject":       {"g": 0.06, "l": "side"},
    "back_to_wall":        {"c": -0.18},
    "longer_focal":        {"r": 0.05, "c": -0.15},
    "widest":              {"r": -0.10, "c": 0.05},
    "medium_focal":        {"r": 0.02},
    "zoom_in_slow":        {"r": 0.08},
    "zoom_out_context":    {"r": -0.08, "c": 0.05},
    "avoid_wide_face":     {"r": -0.05},
    "telephoto_compress":  {"c": -0.12},
    "lower_camera":        {"r": 0.03},
    "raise_camera":        {"r": -0.02, "c": 0.03},
    "eye_level":           {"g": 0.04},
    "crouch_down":         {"c": -0.10, "r": 0.02},
    "knee_level":          {"r": 0.02},
    "slightly_above":      {"r": -0.02},
    "ground_level":        {"c": -0.05},
    "dutch_angle":         {"g": 0.05},
    "side_45":             {"g": 0.06},
    "front_on":            {"g": -0.10, "r": 0.02},
    "back_view":           {"g": 0.12},
    "over_shoulder":       {"g": 0.08},
    "low_angle_up":        {"r": 0.05},
    "high_angle_down":     {"r": -0.03},
    "side_light":          {"l": "side"},
    "back_light":          {"l": "back"},
    "front_light":         {"l": "front"},
    "golden_hour":         {"l": "golden"},
    "avoid_backlight":     {"l": "front"},
    "move_to_window":      {"l": "side"},
    "shade_subject":       {"l": "low"},
    "shift_subject_third": {"g": 0.12, "r": -0.03},
    "shift_right_third":   {"g": 0.12, "r": -0.03},
    "center_subject":      {"g": -0.12},
    "gaze_room":           {"g": 0.15},
    "lower_horizon":       {"c": -0.05},
    "raise_horizon":       {"c": -0.05},
    "leave_negative_space": {"c": -0.10, "r": -0.03},
    "frame_within_frame":  {"c": 0.03},
    "clean_background":    {"c": -0.20},
    "remove_clutter":      {"c": -0.15},
    "clean_edge":          {"c": -0.06},
    "change_angle_bg":     {"c": -0.12},
    "step_for_separation": {"c": -0.10},
    "blur_background":     {"c": -0.15},
    "slow_push":           {"r": 0.06},
}


def load_json(name):
    path = BASE / name
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def match_intent(text, intent_data):
    """关键词匹配; 无匹配时默认人物中景"""
    text = text or ""
    best, best_hits = None, 0
    for item in intent_data["intents"]:
        hits = sum(1 for kw in item["keywords"] if kw in text)
        if hits > best_hits:
            best, best_hits = item, hits
    if best is None:
        for item in intent_data["intents"]:
            if item["id"] == "portrait_medium":
                return item
    return best


def score_range(value, lo, hi):
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return max(0.0, 1.0 - (lo - value) / lo) if lo > 0 else 0.0
    return max(0.0, 1.0 - (value - hi) / (1.0 - hi)) if hi < 1 else 0.0


def score_state(x, intent):
    """按 math_definition.md 第4节计算 S(x, I)"""
    t = intent["targets"]
    w = intent["weights"]
    f1 = score_range(x["r"], *t["subject_ratio"])
    f2 = score_range(x["c"], 0.0, t["bg_complexity"][1])
    f3 = 1.0 if x["l"] in t["light_angle"] else 0.4
    f4 = score_range(x["g"], *t["gaze_space"])
    total = w["subject_ratio"] * f1 + w["bg_complexity"] * f2 + \
            w["light_angle"] * f3 + w["gaze_space"] * f4
    return {
        "total": round(total, 3),
        "components": {
            "subject_ratio": round(f1, 3),
            "bg_complexity": round(f2, 3),
            "light_angle": f3,
            "gaze_space": round(f4, 3),
        }
    }


def evaluate_condition(cond, x):
    """规则条件求值; 支持 field/op/value"""
    field_map = {
        "subject_ratio": "r",
        "bg_complexity": "c",
        "light_angle": "l",
        "gaze_space": "g",
    }
    key = field_map[cond["field"]]
    actual = x[key]
    op, expected = cond["op"], cond["value"]
    if op == "<":
        return actual < expected
    if op == "<=":
        return actual <= expected
    if op == ">":
        return actual > expected
    if op == ">=":
        return actual >= expected
    if op == "==":
        return actual == expected
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    return False


def apply_action(x, action_id):
    """估计动作执行后的画面特征"""
    effect = ACTION_EFFECTS.get(action_id, {})
    new = dict(x)
    if "l" in effect:
        new["l"] = effect["l"]
    new["r"] = clamp(new["r"] + effect.get("r", 0.0))
    new["c"] = clamp(new["c"] + effect.get("c", 0.0))
    new["g"] = clamp(new["g"] + effect.get("g", 0.0))
    return new


def suggest(intent_text, features, intent_data, rules_data, phrases_data):
    intent = match_intent(intent_text, intent_data)
    x = {
        "r": clamp(float(features.get("subject_ratio", 0.3))),
        "c": clamp(float(features.get("bg_complexity", 0.3))),
        "l": features.get("light_direction", "front"),
        "g": clamp(float(features.get("gaze_space", 0.3))),
    }

    phrase_map = {p["id"]: p for p in phrases_data["phrases"]}
    before = score_state(x, intent)

    # 规则筛选 + ΔS 排序
    candidates = {}
    for rule in rules_data["rules"]:
        applies = rule["applies_to"]
        if "all" not in applies and intent["id"] not in applies:
            continue
        if not evaluate_condition(rule["condition"], x):
            continue
        aid = rule["action_id"]
        if aid in candidates:
            continue  # 同一动作只保留首次命中的规则

        after_x = apply_action(x, aid)
        after = score_state(after_x, intent)
        delta_s = round(after["total"] - before["total"], 3)
        phrase = phrase_map.get(aid, {})
        candidates[aid] = {
            "action": phrase.get("action", aid),
            "delta_s": delta_s,
            "delta_hint": phrase.get("delta_hint", ""),
            "reason": rule["reason"] or phrase.get("reason_template", ""),
            "priority": rule.get("priority", 0),
        }

    ranked = sorted(
        candidates.values(),
        key=lambda c: (c["delta_s"], c["priority"]),
        reverse=True,
    )[:3]

    return {
        "intent_detected": intent["id"],
        "intent_label": intent["label"],
        "current_score": before,
        "suggestions": [
            {
                "rank": i + 1,
                "action": c["action"],
                "delta": c["delta_hint"],
                "expected_gain": c["delta_s"],
                "reason": c["reason"],
            }
            for i, c in enumerate(ranked)
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="AI 现场拍摄指导助手 - 建议引擎")
    parser.add_argument("--intent", default="电影感人物特写，背景虚化", help="用户自然语言要求")
    parser.add_argument("--ratio", type=float, default=0.18, help="主体占比 0-1")
    parser.add_argument("--bg", type=float, default=0.42, help="背景复杂度 0-1")
    parser.add_argument("--light", default="front", help="光线方向 front/side/back/low/golden")
    parser.add_argument("--gaze", type=float, default=0.30, help="视线空间 0-1")
    parser.add_argument("--stdin", action="store_true", help="从标准输入读取完整 JSON")
    args = parser.parse_args()

    intent_data = load_json("intent_weights.json")
    rules_data = load_json("rules.json")
    phrases_data = load_json("phrases.json")

    if args.stdin:
        payload = json.loads(sys.stdin.read())
        intent_text = payload.get("intent", "")
        features = payload.get("frame_features", {})
    else:
        intent_text = args.intent
        features = {
            "subject_ratio": args.ratio,
            "bg_complexity": args.bg,
            "light_direction": args.light,
            "gaze_space": args.gaze,
        }

    result = suggest(intent_text, features, intent_data, rules_data, phrases_data)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
