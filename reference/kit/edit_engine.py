#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 现场拍摄指导助手 - 素材自动剪辑与整理引擎 v1.0
负责人: Bridge + 队友A

输入: 素材清单 (materials.csv) + 剪辑规则 (edit_rules.json)
输出: 整理分类 + 粗剪时间线 EDL (JSON)

闭环说明: materials.csv 中的 quality_score 来自 suggest_engine.py 在拍摄阶段
对画面的评分; followed_advice 记录用户是否按建议调整过。这就是"拍中"喂给"拍后"的数据。

用法:
  python edit_engine.py
  python edit_engine.py --materials materials.csv --rules edit_rules.json
  python edit_engine.py --json     # 仅输出 EDL 部分

依赖: 仅 Python 标准库
"""

import argparse
import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent


def load_json(name):
    with open(BASE / name, "r", encoding="utf-8") as f:
        return json.load(f)


def load_materials(name):
    with open(BASE / name, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for i, row in enumerate(rows):
        row["_index"] = i
        for key in ("duration_sec", "quality_score", "stability", "in_point", "out_point"):
            row[key] = float(row[key]) if row[key] else 0.0
        for key in ("focus_ok", "has_mistake", "followed_advice"):
            row[key] = int(float(row[key] or 0))
    return rows


def eval_condition(clip, cond):
    """条件求值; clip 字段为数值或 0/1"""
    field = cond["field"]
    if field not in clip:
        return False
    actual = clip[field]
    expected = cond["value"]
    op = cond["op"]
    try:
        a, e = float(actual), float(expected)
    except (TypeError, ValueError):
        a, e = str(actual), str(expected)
        if op == "==":
            return a == str(expected)
        if op == "in":
            return a in expected
        return False
    if op == "<":
        return a < e
    if op == "<=":
        return a <= e
    if op == ">":
        return a > e
    if op == ">=":
        return a >= e
    if op == "==":
        return a == e
    return False


def organize(materials, rules):
    """按质量规则分类素材"""
    usable, excluded, duplicates = [], [], []
    for clip in materials:
        if clip.get("duplicate_of"):
            duplicates.append({
                "clip_id": clip["clip_id"],
                "duplicate_of": clip["duplicate_of"],
                "action": "丢弃重复片段",
            })
            continue
        hit = None
        for rule in rules["quality_filters"]:
            if eval_condition(clip, rule):
                hit = rule
                break
        if hit:
            excluded.append({
                "clip_id": clip["clip_id"],
                "reason": hit["reason"],
                "rule_id": hit["id"],
            })
        else:
            score, bonus_reasons = priority_score(clip, rules)
            clip["_priority"] = score
            clip["_bonus_reasons"] = bonus_reasons
            usable.append(clip)
    return usable, excluded, duplicates


def priority_score(clip, rules):
    """质量分 + 优先加权（按建议调整过的片段优先）"""
    score = clip["quality_score"]
    reasons = ["构图质量分合格"]
    for bonus in rules["priority_bonus"]:
        if eval_condition(clip, bonus):
            score += bonus["bonus"]
            reasons.append(bonus["reason"])
    return round(score, 3), reasons


def pick_duration(clip, sel):
    """计算入出点: 裁掉头尾 + 限制时长"""
    dur = clip["duration_sec"]
    in_p = clip["in_point"] or 0.0
    out_p = clip["out_point"] or dur
    in_p = min(in_p + sel["trim_head_sec"], max(dur - 0.5, 0.0))
    out_p = max(out_p - sel["trim_tail_sec"], in_p + 0.5)
    seg = out_p - in_p
    if seg > sel["max_duration_sec"]:
        out_p = in_p + sel["max_duration_sec"]
    elif seg < sel["min_duration_sec"]:
        out_p = min(dur, in_p + sel["min_duration_sec"])
    return round(in_p, 2), round(out_p, 2)


def pick_transition(prev_clip, cur_clip):
    """相邻片段的转场建议"""
    if prev_clip["intent_id"] == "emotional":
        return "淡入淡出", "情绪镜头后留呼吸"
    if prev_clip["scene_type"] == cur_clip["scene_type"]:
        return "硬切", "同一场地硬切最自然"
    return "叠化", "场景或镜头类型变化用叠化过渡"


def build_rough_cut(usable, rules):
    """按叙事弧排序并生成 EDL 时间线"""
    sel = rules["selection"]
    order_map = {k: i for i, k in enumerate(rules["ordering"]["intent_order"])}
    ordered = sorted(
        usable,
        key=lambda c: (order_map.get(c["intent_id"], 99), c["_index"]),
    )[: sel["max_clips"]]

    timeline = []
    cursor = 0.0
    for i, clip in enumerate(ordered):
        in_p, out_p = pick_duration(clip, sel)
        seg = round(out_p - in_p, 2)
        transition, trans_reason = ("无", "开篇镜头") if i == 0 else pick_transition(ordered[i - 1], clip)
        timeline.append({
            "order": i + 1,
            "clip_id": clip["clip_id"],
            "filename": clip["filename"],
            "in": in_p,
            "out": out_p,
            "duration_sec": seg,
            "timeline_in": round(cursor, 2),
            "transition_in": transition,
            "priority": clip["_priority"],
            "reason": "；".join(clip["_bonus_reasons"][:2]) + "；" + trans_reason,
        })
        cursor += seg

    return {
        "clip_count": len(timeline),
        "total_duration_sec": round(cursor, 2),
        "timeline": timeline,
    }


def run(materials_name="materials.csv", rules_name="edit_rules.json"):
    materials = load_materials(materials_name)
    rules = load_json(rules_name)
    usable, excluded, duplicates = organize(materials, rules)
    rough_cut = build_rough_cut(usable, rules)
    return {
        "organization": {
            "total": len(materials),
            "usable": len(usable),
            "excluded_count": len(excluded),
            "duplicate_count": len(duplicates),
            "excluded": excluded,
            "duplicates": duplicates,
            "usable_ids": [c["clip_id"] for c in usable],
        },
        "rough_cut": rough_cut,
    }


def main():
    parser = argparse.ArgumentParser(description="素材自动剪辑与整理引擎")
    parser.add_argument("--materials", default="materials.csv", help="素材清单 CSV")
    parser.add_argument("--rules", default="edit_rules.json", help="剪辑规则 JSON")
    parser.add_argument("--json", action="store_true", help="仅输出 EDL 时间线部分")
    args = parser.parse_args()

    result = run(args.materials, args.rules)
    output = result["rough_cut"] if args.json else result
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
