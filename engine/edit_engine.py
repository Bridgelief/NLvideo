#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 现场拍摄指导助手 —— 素材自动整理与粗剪引擎（可 import 的纯函数 + 兼容 kit CLI）。

契约（唯一权威）：``docs/INTERFACES.md`` §2.4 / §3.4 / §3.5。
行为继承 ``reference/kit/edit_engine.py`` + ``edit_logic.md``：

* 去重：``duplicate_of`` 非空 → duplicates，不参与后续；
* 质量过滤：按 ``quality_filters`` 顺序首个命中即剔除（任一命中即淘汰）；
* 优先加权：``priority = quality_score + Σ priority_bonus``（按建议调整过 → +0.15，闭环）；
* 叙事排序：按 ``ordering.intent_order`` 分组，组内保持原始行序，截断 ``selection.max_clips``；
* 入出点：``in_point + trim_head`` / ``out_point - trim_tail``，夹在 [min, max] 且保证成段；
* 转场：优先由 ``rules["transitions"]``（契约 §2.4 的 dict schema）驱动，
  缺失/旧 schema（kit 的 list）回退 kit 逻辑（情绪镜头后淡入淡出 → 同场景硬切 → 其余叠化）。

运行方式::

    python -m engine.edit_engine
    python -m engine.edit_engine --json
    python engine/edit_engine.py --materials materials.csv --rules edit_rules.json

依赖：仅 Python 标准库。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
#: 默认数据目录（契约：<repo>/data）
DATA_DIR = REPO_ROOT / "data"

#: 素材行字段（契约 §3.5；output/shot_log.csv 额外列会被忽略）
MATERIAL_FIELDS = (
    "clip_id",
    "filename",
    "duration_sec",
    "scene_type",
    "intent_id",
    "quality_score",
    "stability",
    "focus_ok",
    "has_mistake",
    "duplicate_of",
    "followed_advice",
    "in_point",
    "out_point",
    "notes",
)
_FLOAT_FIELDS = ("duration_sec", "quality_score", "stability", "in_point", "out_point")
_INT_FIELDS = ("focus_ok", "has_mistake", "followed_advice")
_TEXT_FIELDS = ("clip_id", "filename", "scene_type", "intent_id", "duplicate_of", "notes")

#: 内置编辑规则（edit_rules.json 缺失/损坏时的降级方案，与 kit 默认一致）
DEFAULT_EDIT_RULES: dict[str, Any] = {
    "version": "fallback",
    "quality_filters": [
        {"id": "Q001", "field": "stability", "op": "<", "value": 0.45, "action": "exclude", "reason": "画面抖动严重，无法使用"},
        {"id": "Q002", "field": "focus_ok", "op": "==", "value": 0, "action": "exclude", "reason": "主体失焦"},
        {"id": "Q003", "field": "has_mistake", "op": "==", "value": 1, "action": "exclude", "reason": "存在闭眼或穿帮"},
        {"id": "Q004", "field": "quality_score", "op": "<", "value": 0.35, "action": "exclude", "reason": "构图质量分过低"},
    ],
    "priority_bonus": [
        {"id": "P001", "field": "followed_advice", "op": "==", "value": 1, "bonus": 0.15, "reason": "按拍摄建议调整过，通常质量更好"},
        {"id": "P002", "field": "quality_score", "op": ">", "value": 0.75, "bonus": 0.10, "reason": "构图评分高"},
    ],
    "selection": {
        "max_clips": 12,
        "min_duration_sec": 1.5,
        "max_duration_sec": 8.0,
        "default_duration_sec": 3.5,
        "trim_head_sec": 0.3,
        "trim_tail_sec": 0.3,
    },
    "ordering": {
        "strategy": "intent_arc",
        "intent_order": ["landscape", "portrait_medium", "product", "portrait_closeup", "emotional"],
        "keep_shooting_order_within_intent": True,
    },
    # 无 transitions 键 → 使用 kit 逻辑
}


# ---------------------------------------------------------------------------
# 宽容数值/路径工具
# ---------------------------------------------------------------------------
def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default  # 过滤 NaN


def _as_optional_float(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _as_int(value: Any, default: int = 0) -> int:
    number = _as_optional_float(value)
    return int(number) if number is not None else default


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _resolve_data_path(source: Any, base_dir: Any = None) -> Path:
    """把 str/Path 解析成实际文件路径。

    绝对路径原样；相对路径依次尝试：cwd → ``base_dir``（调用方指定的数据目录）→
    默认 ``data/`` → 仓库根。这样 ``--data-dir reference/kit --materials materials.csv``
    会优先取 kit 目录里的文件，而不会误用仓库 data/ 的同名文件。
    """
    path = source if isinstance(source, Path) else Path(str(source))
    if path.is_absolute():
        return path
    candidates: list[Path] = [path]
    if base_dir is not None:
        candidates.append(Path(base_dir) / path)
    candidates.extend([DATA_DIR / path, REPO_ROOT / path])
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # pragma: no cover - 极端路径
            continue
    # 都不存在时返回默认数据目录下的候选，让调用方拿到 FileNotFoundError
    return DATA_DIR / path


# ---------------------------------------------------------------------------
# 素材加载
# ---------------------------------------------------------------------------
def _normalize_row(row: dict, index: int) -> dict:
    """把一行素材（CSV dict 或外部 list[dict] 元素）归一化成引擎内部结构。

    只保留契约 §3.5 的字段，额外列（如 shot_log.csv 的 before_url/after_url）直接忽略。
    """
    source = row if isinstance(row, dict) else {}
    clip: dict[str, Any] = {field: _as_text(source.get(field)) for field in _TEXT_FIELDS}
    for field in _FLOAT_FIELDS:
        clip[field] = _as_float(source.get(field), 0.0)
    for field in _INT_FIELDS:
        clip[field] = _as_int(source.get(field), 0)
    clip["_index"] = index
    return clip


def _read_materials_csv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [_normalize_row(row, index) for index, row in enumerate(reader)]


def load_materials(source: Any = None, base_dir: Any = None) -> list[dict]:
    """加载素材清单。

    ``source`` 支持四种形态：
    * ``None`` → 默认 ``<repo>/data/materials.csv``；
    * ``Path`` / ``str`` → 具体文件；相对路径会依次按 cwd、``base_dir``、``data/``、
      仓库根解析，因此 ``"materials.csv"``、``"data/materials.csv"`` 都能用；
    * ``list[dict]`` → 直接使用（server 把 shot_log 行传进来时走这条路径），额外列忽略。

    文件不存在 / 列缺失时抛 ``FileNotFoundError`` / 返回容错行；由 ``run()`` 统一降级。
    """
    if source is None:
        return _read_materials_csv(DATA_DIR / "materials.csv")
    if isinstance(source, (list, tuple)):
        return [
            _normalize_row(row, index)
            for index, row in enumerate(source)
            if isinstance(row, dict)
        ]
    return _read_materials_csv(_resolve_data_path(source, base_dir))


def load_edit_rules(path: Any = None, base_dir: Any = None) -> dict:
    """加载剪辑规则 JSON（默认 ``<repo>/data/edit_rules.json``）。

    文件缺失 / 解析失败会抛异常，由 ``run()`` 捕获后回退 ``DEFAULT_EDIT_RULES``。
    ``base_dir`` 用于相对路径优先解析目录（``run(data_dir=...)`` 时传数据目录）。
    """
    resolved = DATA_DIR / "edit_rules.json" if path is None else _resolve_data_path(path, base_dir)
    with open(resolved, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"edit_rules.json 顶层不是 JSON 对象: {resolved}")
    return data


# ---------------------------------------------------------------------------
# 条件求值 / 优先分
# ---------------------------------------------------------------------------
def _num(value: Any) -> float | None:
    return _as_optional_float(value)


def _loose_equal(actual: Any, expected: Any) -> bool:
    actual_number, expected_number = _num(actual), _num(expected)
    if actual_number is not None and expected_number is not None:
        return actual_number == expected_number
    return str(actual).strip() == str(expected).strip()


def eval_condition(clip: dict, cond: Any) -> bool:
    """素材字段条件求值（契约 §3.4 quality_filters / priority_bonus）：

    支持 ``< <= > >= == in not_in``；字段不在素材行里 → False；结构非法 → False。
    """
    if not isinstance(cond, dict):
        return False
    field = cond.get("field")
    op = cond.get("op")
    expected = cond.get("value")
    if field not in clip:
        return False
    actual = clip[field]

    if op in ("in", "not_in"):
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        hit = any(_loose_equal(actual, item) for item in values)
        return hit if op == "in" else not hit

    actual_number, expected_number = _num(actual), _num(expected)
    if actual_number is not None and expected_number is not None:
        if op == "<":
            return actual_number < expected_number
        if op == "<=":
            return actual_number <= expected_number
        if op == ">":
            return actual_number > expected_number
        if op == ">=":
            return actual_number >= expected_number
        if op == "==":
            return actual_number == expected_number
        return False

    if op == "==":
        return str(actual).strip() == str(expected).strip()
    return False


def _priority_score(clip: dict, rules: dict) -> tuple[float, list[str]]:
    """priority = quality_score + Σpriority_bonus；同时收集给人看的原因文案。"""
    score = _as_float(clip.get("quality_score"), 0.0)
    reasons = ["构图质量分合格"]
    bonuses = rules.get("priority_bonus")
    bonuses = bonuses if isinstance(bonuses, list) else []
    for bonus in bonuses:
        if not isinstance(bonus, dict):
            continue
        if eval_condition(clip, bonus):
            score += _as_float(bonus.get("bonus"), 0.0)
            reason = _as_text(bonus.get("reason"))
            if reason:
                reasons.append(reason)
    return round(score, 3), reasons


# ---------------------------------------------------------------------------
# 整理
# ---------------------------------------------------------------------------
def organize(materials: list[dict], rules: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """按规则把素材分成 (usable, excluded, duplicates)（契约 §2.4）。

    usable 里每条会带上内部字段 ``_priority`` / ``_bonus_reasons`` / ``_index``，
    ``build_rough_cut`` 直接用它们排序与写 reason。
    """
    usable: list[dict] = []
    excluded: list[dict] = []
    duplicates: list[dict] = []
    filters = rules.get("quality_filters")
    filters = filters if isinstance(filters, list) else []

    for clip in materials if isinstance(materials, list) else []:
        if not isinstance(clip, dict):
            continue
        item = dict(clip)
        item.setdefault("_index", 0)

        if item.get("duplicate_of"):
            duplicates.append(
                {
                    "clip_id": _as_text(item.get("clip_id")),
                    "duplicate_of": _as_text(item.get("duplicate_of")),
                    "action": "丢弃重复片段",
                }
            )
            continue

        hit = None
        for rule in filters:
            if isinstance(rule, dict) and eval_condition(item, rule):
                hit = rule
                break

        if hit is not None:
            excluded.append(
                {
                    "clip_id": _as_text(item.get("clip_id")),
                    "reason": _as_text(hit.get("reason")),
                    "rule_id": _as_text(hit.get("id")),
                }
            )
            continue

        priority, bonus_reasons = _priority_score(item, rules)
        item["_priority"] = priority
        item["_bonus_reasons"] = bonus_reasons
        usable.append(item)

    return usable, excluded, duplicates


# ---------------------------------------------------------------------------
# 粗剪
# ---------------------------------------------------------------------------
def _selection_params(rules: dict) -> dict[str, float]:
    """取 selection 配置；缺字段用 kit 默认值，坏值也回退默认（不抛异常）。"""
    raw = rules.get("selection")
    raw = raw if isinstance(raw, dict) else {}
    defaults = DEFAULT_EDIT_RULES["selection"]

    def pick(key: str) -> float:
        number = _num(raw.get(key))
        return number if number is not None else float(defaults[key])

    min_duration = max(0.0, pick("min_duration_sec"))
    max_duration = max(min_duration, pick("max_duration_sec"))
    return {
        "max_clips": max(1, int(pick("max_clips"))),
        "min_duration_sec": min_duration,
        "max_duration_sec": max_duration,
        "default_duration_sec": max(0.0, pick("default_duration_sec")),
        "trim_head_sec": max(0.0, pick("trim_head_sec")),
        "trim_tail_sec": max(0.0, pick("trim_tail_sec")),
    }


def pick_duration(clip: dict, sel: dict[str, float]) -> tuple[float, float]:
    """计算入出点：裁掉头尾 → 夹到 [min, max] → 保证 out > in 且不超出源时长。"""
    duration = max(0.0, _as_float(clip.get("duration_sec"), 0.0))
    in_raw = _as_float(clip.get("in_point"), 0.0)
    out_raw = _as_optional_float(clip.get("out_point"))
    if out_raw is None or out_raw <= in_raw:
        out_raw = duration

    in_point = in_raw + sel["trim_head_sec"]
    out_point = out_raw - sel["trim_tail_sec"]

    # 起点必须在源素材内（至少留 0.5s 给片段）
    if duration >= 0.5:
        in_point = min(in_point, duration - 0.5)
    in_point = max(0.0, in_point)
    out_point = max(out_point, in_point + 0.5)

    segment = out_point - in_point
    if segment > sel["max_duration_sec"]:
        out_point = in_point + sel["max_duration_sec"]
    elif segment < sel["min_duration_sec"]:
        out_point = min(duration, in_point + sel["min_duration_sec"])

    out_point = min(out_point, duration) if duration > 0 else out_point
    if out_point <= in_point:
        out_point = min(duration, in_point + 0.5) if duration > 0 else in_point + 0.5
    if out_point - in_point < 0.5:
        in_point = max(0.0, out_point - 0.5)

    return round(in_point, 2), round(out_point, 2)


def _kit_transition(prev_clip: dict, cur_clip: dict) -> tuple[str, str]:
    """kit 逻辑（edit_logic.md §5.3）：情绪镜头后留呼吸 → 同场景硬切 → 其余叠化。"""
    if _as_text(prev_clip.get("intent_id")) == "emotional":
        return "淡入淡出", "情绪镜头后留呼吸"
    if _as_text(prev_clip.get("scene_type")) == _as_text(cur_clip.get("scene_type")):
        return "硬切", "同一场地硬切最自然"
    return "叠化", "场景或镜头类型变化用叠化过渡"


def pick_transition(prev_clip: dict, cur_clip: dict, rules: dict) -> tuple[str, str]:
    """相邻片段转场：``rules["transitions"]`` 为契约 §2.4 dict 时驱动，否则回退 kit 逻辑。"""
    transitions = rules.get("transitions")
    if isinstance(transitions, dict):
        templates = transitions.get("reason_templates")
        templates = templates if isinstance(templates, dict) else {}
        if _as_text(prev_clip.get("intent_id")) == _as_text(transitions.get("after_intent")):
            name = _as_text(transitions.get("after_intent_transition"), "淡入淡出")
        elif _as_text(prev_clip.get("scene_type")) == _as_text(cur_clip.get("scene_type")):
            name = _as_text(transitions.get("same_scene_type_transition"), "硬切")
        else:
            name = _as_text(transitions.get("default_transition"), "叠化")
        reason = _as_text(templates.get(name))
        return name, reason
    # 旧 schema（kit list）或没有 transitions 键 → kit 逻辑
    name, reason = _kit_transition(prev_clip, cur_clip)
    return name, reason


def _opening_transition(rules: dict) -> tuple[str, str]:
    transitions = rules.get("transitions")
    if isinstance(transitions, dict):
        templates = transitions.get("reason_templates")
        templates = templates if isinstance(templates, dict) else {}
        return "无", _as_text(templates.get("无"), "开篇镜头")
    return "无", "开篇镜头"


def build_rough_cut(usable: list[dict], rules: dict) -> dict:
    """按叙事弧排序并生成 EDL 时间线（契约 §2.4 结构）。"""
    sel = _selection_params(rules)

    ordering = rules.get("ordering")
    ordering = ordering if isinstance(ordering, dict) else {}
    raw_order = ordering.get("intent_order")
    intent_order = [entry for entry in raw_order if isinstance(entry, str)] if isinstance(raw_order, list) else []
    if not intent_order:
        intent_order = list(DEFAULT_EDIT_RULES["ordering"]["intent_order"])
    order_map = {intent_id: index for index, intent_id in enumerate(intent_order)}

    def sort_key(clip: dict) -> tuple[int, int]:
        intent_rank = order_map.get(_as_text(clip.get("intent_id")), len(order_map) + 99)
        return intent_rank, _as_int(clip.get("_index"), 0)

    ordered = sorted(usable if isinstance(usable, list) else [], key=sort_key)[: sel["max_clips"]]

    timeline: list[dict] = []
    cursor = 0.0
    for index, clip in enumerate(ordered):
        in_point, out_point = pick_duration(clip, sel)
        segment = round(out_point - in_point, 2)

        if index == 0:
            transition, transition_reason = _opening_transition(rules)
        else:
            transition, transition_reason = pick_transition(ordered[index - 1], clip, rules)

        bonus_reasons = clip.get("_bonus_reasons")
        bonus_reasons = bonus_reasons if isinstance(bonus_reasons, list) else []
        reasons = [_as_text(reason) for reason in bonus_reasons[:2] if _as_text(reason)]
        if transition_reason:
            reasons.append(transition_reason)

        timeline.append(
            {
                "order": index + 1,
                "clip_id": _as_text(clip.get("clip_id")),
                "filename": _as_text(clip.get("filename")),
                "in": in_point,
                "out": out_point,
                "duration_sec": segment,
                "timeline_in": round(cursor, 2),
                "transition_in": transition,
                "priority": _as_float(clip.get("_priority"), 0.0),
                "reason": "；".join(reasons),
            }
        )
        cursor += segment

    return {
        "clip_count": len(timeline),
        "total_duration_sec": round(cursor, 2),
        "timeline": timeline,
    }


# ---------------------------------------------------------------------------
# 编排入口
# ---------------------------------------------------------------------------
def _source_label(materials: Any, path: Path, data_dir: Path) -> str:
    if isinstance(materials, (list, tuple)):
        return "inline"
    try:
        resolved = path.expanduser().resolve()
    except (OSError, RuntimeError):  # pragma: no cover
        return str(path)
    try:
        data_resolved = data_dir.expanduser().resolve()
    except (OSError, RuntimeError):  # pragma: no cover
        data_resolved = data_dir
    if resolved.parent == data_resolved:
        return resolved.name
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def run(materials: Any = None, rules: Any = None, data_dir: Any = None) -> dict:
    """一站式：加载 → 整理 → 粗剪，返回 ``{"organization","rough_cut","source","warnings"}``。

    数据缺失/字段坏不抛异常：降级为空结果或内置默认规则，原因写进 ``warnings``。
    """
    warnings: list[str] = []
    directory = Path(data_dir) if data_dir is not None else DATA_DIR
    try:
        directory = directory.expanduser().resolve()
    except (OSError, RuntimeError):  # pragma: no cover
        directory = Path(directory)

    # ---- 规则 ----
    if rules is None:
        try:
            rules = load_edit_rules(directory / "edit_rules.json")
        except (OSError, ValueError) as exc:
            warnings.append(f"剪辑规则加载失败（{exc}），已使用内置默认规则")
            rules = json.loads(json.dumps(DEFAULT_EDIT_RULES))
    elif isinstance(rules, str) or isinstance(rules, Path):
        try:
            rules = load_edit_rules(rules, base_dir=directory)
        except (OSError, ValueError) as exc:
            warnings.append(f"剪辑规则加载失败（{exc}），已使用内置默认规则")
            rules = json.loads(json.dumps(DEFAULT_EDIT_RULES))
    elif not isinstance(rules, dict):
        warnings.append("剪辑规则类型非法（应为 dict），已使用内置默认规则")
        rules = json.loads(json.dumps(DEFAULT_EDIT_RULES))

    # ---- 素材 ----
    if isinstance(materials, (list, tuple)):
        materials_source: Any = materials
        source = "inline"
    else:
        materials_source = directory / "materials.csv" if materials is None else materials
        resolved_path = _resolve_data_path(materials_source, directory)
        source = _source_label(materials, resolved_path, directory)

    try:
        material_rows = load_materials(materials_source, base_dir=directory)
    except (OSError, ValueError, TypeError) as exc:
        warnings.append(f"素材加载失败（{materials_source}）: {exc}")
        material_rows = []

    if not material_rows:
        warnings.append("素材清单为空或无法读取，粗剪时间线为空")

    try:
        usable, excluded, duplicates = organize(material_rows, rules)
        rough_cut = build_rough_cut(usable, rules)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        warnings.append(f"整理/粗剪计算异常，已降级为空时间线: {exc!r}")
        usable, excluded, duplicates = [], [], []
        rough_cut = {"clip_count": 0, "total_duration_sec": 0.0, "timeline": []}

    return {
        "organization": {
            "total": len(material_rows),
            "usable": len(usable),
            "excluded_count": len(excluded),
            "duplicate_count": len(duplicates),
            "excluded": excluded,
            "duplicates": duplicates,
            "usable_ids": [_as_text(clip.get("clip_id")) for clip in usable],
        },
        "rough_cut": rough_cut,
        "source": source,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# CLI（保留 kit 的 --materials / --rules / --json）
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="素材自动剪辑与整理引擎")
    parser.add_argument("--materials", default="materials.csv", help="素材清单 CSV（默认 data/materials.csv）")
    parser.add_argument("--rules", default="edit_rules.json", help="剪辑规则 JSON（默认 data/edit_rules.json）")
    parser.add_argument("--json", action="store_true", help="仅输出 rough_cut 时间线部分")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 <repo>/data）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    result = run(materials=args.materials, rules=args.rules, data_dir=args.data_dir)

    if args.json:
        print(json.dumps(result["rough_cut"], ensure_ascii=False, indent=2))
        for warning in result["warnings"]:
            print(f"[warn] {warning}", file=sys.stderr)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
