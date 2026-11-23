#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 现场拍摄指导助手 —— 建议引擎（可 import 的纯函数 + 兼容 kit CLI）。

输入：用户自然语言意图 + 当前画面特征（dict）
输出：意图识别结果 + 四维评分 + 主要差距 + 1~3 条可执行机位动作建议

契约（唯一权威）：``docs/INTERFACES.md`` §2.2 / §2.3 / §3.1~3.3。
数学定义：``reference/kit/math_definition.md`` §4（四个评分函数，原样实现）。

设计要点
--------
* 纯函数：``suggest(intent_text, features, resources=None)`` 不读文件以外的外部状态，
  不联网、不依赖 UI；数据坏了只写 ``warnings``，不抛异常。
* 资源缓存：``load_resources()`` 按三个 JSON 的 ``mtime_ns`` 做进程内失效缓存。
* 确定性：所有排序键都显式给出最终 tie-break（``action_id`` 升序 / 意图表原始顺序），
  同一输入两次运行输出逐字节一致。
* 兜底：规则库没命中时用 ``intent.preferred_actions``，再不行按 ``primary_gap`` 映射
  默认动作；只要资源可读就保证返回 1~3 条建议（数据文件全缺时才允许空列表）。

运行方式（两种都支持）::

    python -m engine.suggest_engine --intent "电影感人物特写，背景虚化" \\
        --ratio 0.18 --bg 0.42 --light front --gaze 0.3
    echo '{"intent":"产品 电商 质感","frame_features":{...}}' | python engine/suggest_engine.py --stdin

依赖：仅 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

# 兼容 `python engine/suggest_engine.py ...`：脚本方式运行时把仓库根放进 sys.path，
# 否则找不到 `engine.action_effects`。`python -m engine.suggest_engine` 时无需处理。
if __package__ in (None, ""):  # pragma: no cover - 取决于启动方式
    _REPO_ROOT = Path(__file__).resolve().parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from engine.action_effects import ACTION_EFFECTS, LIGHT_DIRECTIONS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
#: 数据目录（契约 §2.2 固定为 <repo>/data；可用 data_dir 参数覆盖，用于自测 reference/kit）
DATA_DIR = REPO_ROOT / "data"

#: load_resources 读取的三个数据文件
DATA_FILES = ("intent_weights.json", "rules.json", "phrases.json")
#: 评分维度（固定四键，顺序即确定性 tie-break 顺序）
FEATURE_FIELDS = ("subject_ratio", "bg_complexity", "light_angle", "gaze_space")
#: 五个固定意图 ID（契约 §3.1）
REQUIRED_INTENT_IDS = ("portrait_closeup", "portrait_medium", "landscape", "product", "emotional")
DEFAULT_INTENT_ID = "portrait_medium"

#: features 缺省值（契约 §2.2 输入表）
DEFAULT_FEATURES = {
    "subject_ratio": 0.30,
    "bg_complexity": 0.30,
    "light_direction": "front",
    "gaze_space": 0.30,
}

#: 兜底用的默认权重 / 目标区间（数据文件损坏时保证引擎仍能给出人话建议）
_FALLBACK_WEIGHTS = {
    "subject_ratio": 0.30,
    "bg_complexity": 0.20,
    "light_angle": 0.20,
    "gaze_space": 0.30,
}
_FALLBACK_TARGETS = {
    "subject_ratio": [0.20, 0.40],
    "bg_complexity": [0.0, 0.45],
    "light_angle": ["side", "front"],
    "gaze_space": [0.30, 0.60],
}

#: 内置兜底意图表（仅在 data/intent_weights.json 不可用或全部非法时使用）
_FALLBACK_INTENTS: list[dict[str, Any]] = [
    {
        "id": "portrait_closeup",
        "label": "人像特写",
        "keywords": ["特写", "人物特写", "电影感", "背景虚化", "虚化", "情绪", "脸部", "半身头像"],
        "weights": {"subject_ratio": 0.40, "bg_complexity": 0.25, "light_angle": 0.20, "gaze_space": 0.15},
        "targets": {
            "subject_ratio": [0.30, 0.60],
            "bg_complexity": [0.0, 0.35],
            "light_angle": ["side", "back"],
            "gaze_space": [0.25, 0.50],
        },
        "preferred_actions": ["step_forward", "longer_focal", "lower_camera", "side_light", "shift_subject_third"],
    },
    {
        "id": "portrait_medium",
        "label": "人物中景",
        "keywords": ["中景", "半身", "采访", "口播", "人物", "对话", "站着", "坐着"],
        "weights": {"subject_ratio": 0.30, "bg_complexity": 0.20, "light_angle": 0.20, "gaze_space": 0.30},
        "targets": {
            "subject_ratio": [0.20, 0.40],
            "bg_complexity": [0.0, 0.45],
            "light_angle": ["side", "front"],
            "gaze_space": [0.30, 0.60],
        },
        "preferred_actions": ["step_back", "eye_level", "shift_subject_third", "clean_background", "gaze_room"],
    },
    {
        "id": "landscape",
        "label": "风光环境",
        "keywords": ["风光", "风景", "环境", "建筑", "街拍", "城市", "天空", "大场景", "远景"],
        "weights": {"subject_ratio": 0.15, "bg_complexity": 0.40, "light_angle": 0.35, "gaze_space": 0.10},
        "targets": {
            "subject_ratio": [0.05, 0.25],
            "bg_complexity": [0.0, 0.50],
            "light_angle": ["side", "back", "golden"],
            "gaze_space": [0.10, 0.35],
        },
        "preferred_actions": ["lower_horizon", "widest", "golden_hour", "step_back", "clean_edge"],
    },
    {
        "id": "product",
        "label": "产品静物",
        "keywords": ["产品", "商品", "静物", "电商", "带货", "质感", "摆拍", "美食", "细节"],
        "weights": {"subject_ratio": 0.35, "bg_complexity": 0.35, "light_angle": 0.25, "gaze_space": 0.05},
        "targets": {
            "subject_ratio": [0.35, 0.65],
            "bg_complexity": [0.0, 0.25],
            "light_angle": ["side", "front"],
            "gaze_space": [0.0, 0.15],
        },
        "preferred_actions": ["clean_background", "side_light", "lower_camera", "step_back", "remove_clutter"],
    },
    {
        "id": "emotional",
        "label": "情绪叙事",
        "keywords": ["情绪", "故事", "叙事", "压抑", "孤独", "冲突", "氛围", "文艺", "安静", "紧张"],
        "weights": {"subject_ratio": 0.20, "bg_complexity": 0.20, "light_angle": 0.35, "gaze_space": 0.25},
        "targets": {
            "subject_ratio": [0.15, 0.45],
            "bg_complexity": [0.0, 0.40],
            "light_angle": ["back", "side", "low"],
            "gaze_space": [0.35, 0.65],
        },
        "preferred_actions": ["dutch_angle", "back_light", "gaze_room", "lower_camera", "slow_push"],
    },
]

#: 兜底动作：primary_gap.field + direction → 默认动作
_GAP_FALLBACK_ACTIONS: dict[str, dict[str, str]] = {
    "subject_ratio": {"low": "step_forward", "high": "step_back"},
    "bg_complexity": {"high": "clean_background", "low": "frame_within_frame"},
    "light_angle": {"mismatch": "side_light"},
    "gaze_space": {"low": "gaze_room", "high": "center_subject"},
}
#: 连 primary_gap 都没有时的默认动作（均存在于 ACTION_EFFECTS）
_DEFAULT_FALLBACK_ACTIONS = ["step_back", "clean_background", "side_light"]

#: 进程内资源缓存：{resolved_data_dir: (mtime_signature, resources)}
_RESOURCE_CACHE: dict[str, tuple[tuple, dict]] = {}

_PHRASE_TEXT_FALLBACK = "当前画面还需要微调，按这个动作试一下"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """把数值夹到 [lo, hi]，NaN/inf 返回 lo。"""
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return lo
    return max(lo, min(hi, float(value)))


def _as_float(value: Any) -> float | None:
    """宽容地转 float；失败或非有限值返回 None（不抛异常）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        text = value.strip()
        return text if text else default
    if value is None:
        return default
    return str(value).strip() or default


def _first_number(mapping: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in mapping:
            number = _as_float(mapping.get(key))
            if number is not None:
                return number
    return None


# ---------------------------------------------------------------------------
# 特征归一化
# ---------------------------------------------------------------------------
def normalize_features(features: Any) -> dict:
    """把任意 features 输入归一化成契约 §2.2 的四个长键；缺失/非法用缺省值。

    兼容别名：``r`` → subject_ratio，``c`` / ``edge_density`` → bg_complexity，
    ``g`` → gaze_space，``l`` → light_direction。
    """
    source = features if isinstance(features, dict) else {}

    subject = _first_number(source, ("subject_ratio", "r"))
    bg = _first_number(source, ("bg_complexity", "edge_density", "c"))
    gaze = _first_number(source, ("gaze_space", "g"))

    light = source.get("light_direction", source.get("l", DEFAULT_FEATURES["light_direction"]))
    light = _as_text(light, DEFAULT_FEATURES["light_direction"])

    return {
        # 数值维度统一夹到 [0,1]；非法值回落到缺省值
        "subject_ratio": round(clamp(subject if subject is not None else DEFAULT_FEATURES["subject_ratio"]), 4),
        "bg_complexity": round(clamp(bg if bg is not None else DEFAULT_FEATURES["bg_complexity"]), 4),
        "light_direction": light,
        "gaze_space": round(clamp(gaze if gaze is not None else DEFAULT_FEATURES["gaze_space"]), 4),
    }


def _to_internal(features: Any) -> dict:
    """长键 features → 引擎内部短键 {r, c, l, g}。"""
    normalized = normalize_features(features)
    return {
        "r": normalized["subject_ratio"],
        "c": normalized["bg_complexity"],
        "l": normalized["light_direction"],
        "g": normalized["gaze_space"],
    }


def _from_internal(x: dict) -> dict:
    return {
        "subject_ratio": round(x["r"], 4),
        "bg_complexity": round(x["c"], 4),
        "light_direction": x["l"],
        "gaze_space": round(x["g"], 4),
    }


# ---------------------------------------------------------------------------
# 评分函数（reference/kit/math_definition.md §4，原样实现）
# ---------------------------------------------------------------------------
def score_range(value: float, lo: float, hi: float) -> float:
    """目标区间型评分（§4.1 f₁ / §4.4 f₄）：

    ``1``（区间内）；低于下限 ``max(0, 1 - (lo-v)/lo)``；高于上限
    ``max(0, 1 - (v-hi)/(1-hi))``。
    """
    value = clamp(value)
    lo = clamp(lo)
    hi = clamp(hi)
    if lo > hi:  # 数据写反了也尽量给个分，不抛异常
        lo, hi = hi, lo
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return max(0.0, 1.0 - (lo - value) / lo) if lo > 0 else 0.0
    return max(0.0, 1.0 - (value - hi) / (1.0 - hi)) if hi < 1 else 0.0


def _intent_scoring_params(intent: Any) -> tuple[dict, dict]:
    """从意图 dict 取 (weights, targets)；缺字段/坏类型用内置默认值兜底。"""
    source = intent if isinstance(intent, dict) else {}
    weights: dict[str, float] = {}
    targets: dict[str, Any] = {}

    raw_weights = source.get("weights")
    raw_weights = raw_weights if isinstance(raw_weights, dict) else {}
    for field in FEATURE_FIELDS:
        number = _as_float(raw_weights.get(field))
        weights[field] = number if (number is not None and number >= 0) else _FALLBACK_WEIGHTS[field]

    raw_targets = source.get("targets")
    raw_targets = raw_targets if isinstance(raw_targets, dict) else {}
    for field in FEATURE_FIELDS:
        value = raw_targets.get(field)
        if field == "light_angle":
            if (
                isinstance(value, (list, tuple))
                and value
                and all(isinstance(item, str) and item in LIGHT_DIRECTIONS for item in value)
            ):
                targets[field] = list(value)
            else:
                targets[field] = list(_FALLBACK_TARGETS[field])
        else:
            if (
                isinstance(value, (list, tuple))
                and len(value) >= 2
                and _as_float(value[0]) is not None
                and _as_float(value[1]) is not None
                and 0.0 <= float(value[0]) < float(value[1]) <= 1.0
            ):
                targets[field] = [float(value[0]), float(value[1])]
            else:
                targets[field] = list(_FALLBACK_TARGETS[field])
    return weights, targets


def score_state(features_rclg: dict, intent: dict) -> dict:
    """按 math_definition.md §4 计算 S(x, I)。

    参数可以传内部短键（``{"r","c","l","g"}``）或契约长键（``subject_ratio`` 等）。
    返回 ``{"total": float, "components": {...}}``，永不抛异常。
    """
    x = _to_internal(features_rclg)
    weights, targets = _intent_scoring_params(intent)

    f1 = score_range(x["r"], *targets["subject_ratio"])
    f2 = score_range(x["c"], 0.0, targets["bg_complexity"][1])
    f3 = 1.0 if x["l"] in targets["light_angle"] else 0.4
    f4 = score_range(x["g"], *targets["gaze_space"])

    total = (
        weights["subject_ratio"] * f1
        + weights["bg_complexity"] * f2
        + weights["light_angle"] * f3
        + weights["gaze_space"] * f4
    )
    return {
        "total": round(total, 3),
        "components": {
            "subject_ratio": round(f1, 3),
            "bg_complexity": round(f2, 3),
            "light_angle": f3,
            "gaze_space": round(f4, 3),
        },
    }


# ---------------------------------------------------------------------------
# 意图匹配
# ---------------------------------------------------------------------------
def match_intent(intent_text: Any, intent_data: Any) -> dict:
    """关键词意图匹配。

    规则（契约 §2.2）：
    1. 命中关键词**数量最多**者胜；
    2. 平手时取**命中的关键词更长**（更具体）者；再平手取匹配总字数更多者；
    3. 仍平手按意图表原始顺序（保证确定性）；
    4. 空串 / 无命中 → ``portrait_medium``。
    """
    text = intent_text if isinstance(intent_text, str) else ""
    intents: list[dict] = []
    if isinstance(intent_data, dict) and isinstance(intent_data.get("intents"), list):
        intents = [item for item in intent_data["intents"] if isinstance(item, dict)]
    if not intents:
        intents = list(_FALLBACK_INTENTS)

    best: dict | None = None
    best_key: tuple[int, int, int] | None = None
    for index, intent in enumerate(intents):
        keywords_raw = intent.get("keywords")
        keywords = (
            [kw for kw in keywords_raw if isinstance(kw, str) and kw]
            if isinstance(keywords_raw, list)
            else []
        )
        matched = [kw for kw in keywords if kw in text]
        hits = len(matched)
        max_len = max((len(kw) for kw in matched), default=0)
        sum_len = sum(len(kw) for kw in matched)
        # 排序键越大越优先；index 越小越优先（用负 index 参与比较）
        key = (hits, max_len, sum_len)
        if hits <= 0:
            continue
        if best is None or key > best_key:  # type: ignore[operator]
            best, best_key = intent, key
        # key 相等时保留先出现的意图（不替换），因此这里不需要处理 index

    if best is not None:
        return best

    # 无命中 → 默认 portrait_medium；数据里没有就退回内置表
    for intent in intents:
        if _as_text(intent.get("id")) == DEFAULT_INTENT_ID:
            return intent
    return intents[0]


# ---------------------------------------------------------------------------
# 规则条件求值 / 动作效果
# ---------------------------------------------------------------------------
_FIELD_TO_KEY = {
    "subject_ratio": "r",
    "bg_complexity": "c",
    "light_angle": "l",
    "gaze_space": "g",
}
_VALID_OPS = ("<", "<=", ">", ">=", "==", "in", "not_in")


def evaluate_condition(cond: Any, x: dict) -> bool:
    """规则条件求值（契约 §3.3）：支持 ``< <= > >= == in not_in``。

    ``light_angle`` 的 ``in``/``not_in`` 与字符串数组配合使用。
    条件结构非法一律返回 False（不抛异常）。
    """
    if not isinstance(cond, dict):
        return False
    field = cond.get("field")
    op = cond.get("op")
    expected = cond.get("value")
    if field not in _FIELD_TO_KEY or op not in _VALID_OPS:
        return False
    actual = x[_FIELD_TO_KEY[field]]

    if op in ("in", "not_in"):
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        hit = False
        for item in values:
            actual_number, expected_number = _as_float(actual), _as_float(item)
            if actual_number is not None and expected_number is not None:
                hit = actual_number == expected_number
            else:
                hit = str(actual) == str(item)
            if hit:
                break
        return hit if op == "in" else not hit

    if op == "==":
        actual_number, expected_number = _as_float(actual), _as_float(expected)
        if actual_number is not None and expected_number is not None:
            return actual_number == expected_number
        return str(actual) == str(expected)

    actual_number, expected_number = _as_float(actual), _as_float(expected)
    if actual_number is None or expected_number is None:
        return False
    if op == "<":
        return actual_number < expected_number
    if op == "<=":
        return actual_number <= expected_number
    if op == ">":
        return actual_number > expected_number
    return actual_number >= expected_number


def apply_action(x: dict, action_id: str) -> dict:
    """估计动作执行后的画面特征（线性叠加，见 ARCHITECTURE §5.3）。"""
    effect = ACTION_EFFECTS.get(action_id) or {}
    new = dict(x)
    if "l" in effect:
        new["l"] = effect["l"]
    new["r"] = clamp(new["r"] + float(effect.get("r", 0.0)))
    new["c"] = clamp(new["c"] + float(effect.get("c", 0.0)))
    new["g"] = clamp(new["g"] + float(effect.get("g", 0.0)))
    return new


# ---------------------------------------------------------------------------
# 资源加载 + 校验（带 mtime 失效缓存）
# ---------------------------------------------------------------------------
def _mtime_signature(directory: Path) -> tuple:
    signature = []
    for name in DATA_FILES:
        path = directory / name
        try:
            signature.append((name, path.stat().st_mtime_ns))
        except OSError:
            signature.append((name, None))
    return tuple(signature)


def _read_json(path: Path, warnings: list[str]) -> Any:
    """读 JSON；失败写 warnings 并返回 None（不抛异常）。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except FileNotFoundError:
        warnings.append(f"缺少数据文件: {path}")
        return None
    except (OSError, ValueError) as exc:
        warnings.append(f"数据文件读取/解析失败: {path}（{exc}）")
        return None


def _validate_intents(raw: Any, warnings: list[str]) -> list[dict]:
    """校验并归一化 intent_weights.json，坏条目跳过、坏字段回退默认值。"""
    if not isinstance(raw, dict):
        warnings.append("intent_weights.json: 顶层不是 JSON 对象")
        return []
    raw_intents = raw.get("intents")
    if not isinstance(raw_intents, list):
        warnings.append("intent_weights.json: 缺少 intents 数组")
        return []

    intents: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_intents):
        if not isinstance(item, dict):
            warnings.append(f"intent_weights.json: 第 {index + 1} 个意图不是对象，已跳过")
            continue
        intent_id = _as_text(item.get("id"))
        if not intent_id:
            warnings.append(f"intent_weights.json: 第 {index + 1} 个意图缺少 id，已跳过")
            continue
        if intent_id in seen:
            warnings.append(f"intent_weights.json: 意图 id 重复 {intent_id!r}，已跳过后者")
            continue
        seen.add(intent_id)

        label = _as_text(item.get("label"), intent_id)

        raw_weights = item.get("weights")
        raw_weights = raw_weights if isinstance(raw_weights, dict) else {}
        missing_weight_fields = []
        weights: dict[str, float] = {}
        for field in FEATURE_FIELDS:
            number = _as_float(raw_weights.get(field))
            if number is None or number < 0:
                missing_weight_fields.append(field)
                number = _FALLBACK_WEIGHTS[field]
            weights[field] = number
        if missing_weight_fields:
            warnings.append(f"意图 {intent_id}: weights 字段缺失/非法 {missing_weight_fields}，已用默认权重")
        weight_sum = sum(weights.values())
        if abs(weight_sum - 1.0) > 0.001:
            warnings.append(f"意图 {intent_id}: weights 之和为 {weight_sum:.3f}（应为 1.0）")

        raw_targets = item.get("targets")
        raw_targets = raw_targets if isinstance(raw_targets, dict) else {}
        targets: dict[str, Any] = {}
        for field in FEATURE_FIELDS:
            value = raw_targets.get(field)
            if field == "light_angle":
                if (
                    isinstance(value, (list, tuple))
                    and value
                    and all(isinstance(entry, str) and entry in LIGHT_DIRECTIONS for entry in value)
                ):
                    targets[field] = list(value)
                else:
                    warnings.append(f"意图 {intent_id}: targets.light_angle 非法，已用默认值")
                    targets[field] = list(_FALLBACK_TARGETS[field])
            elif (
                isinstance(value, (list, tuple))
                and len(value) >= 2
                and _as_float(value[0]) is not None
                and _as_float(value[1]) is not None
                and 0.0 <= float(value[0]) < float(value[1]) <= 1.0
            ):
                targets[field] = [float(value[0]), float(value[1])]
            else:
                warnings.append(f"意图 {intent_id}: targets.{field} 非法，已用默认值")
                targets[field] = list(_FALLBACK_TARGETS[field])

        raw_keywords = item.get("keywords")
        keywords = (
            [_as_text(kw) for kw in raw_keywords if _as_text(kw)]
            if isinstance(raw_keywords, list)
            else []
        )
        if not keywords:
            warnings.append(f"意图 {intent_id}: keywords 缺失或为空（该意图不会被关键词命中）")

        raw_preferred = item.get("preferred_actions")
        preferred = (
            [_as_text(action) for action in raw_preferred if _as_text(action)]
            if isinstance(raw_preferred, list)
            else []
        )
        unknown_preferred = [action for action in preferred if action not in ACTION_EFFECTS]
        if unknown_preferred:
            warnings.append(f"意图 {intent_id}: preferred_actions 含未知动作 {unknown_preferred}（兜底时忽略）")
        preferred = [action for action in preferred if action in ACTION_EFFECTS]

        intents.append(
            {
                "id": intent_id,
                "label": label,
                "keywords": keywords,
                "weights": weights,
                "targets": targets,
                "preferred_actions": preferred,
            }
        )

    got_ids = {intent["id"] for intent in intents}
    missing_ids = [intent_id for intent_id in REQUIRED_INTENT_IDS if intent_id not in got_ids]
    if missing_ids:
        warnings.append(f"intent_weights.json: 缺少固定意图 {missing_ids}")
    if intents and len(intents) != len(REQUIRED_INTENT_IDS):
        warnings.append(f"intent_weights.json: 意图数量应为 5，实际 {len(intents)}")
    return intents


def _validate_rules(raw: Any, warnings: list[str], known_intent_ids: set[str]) -> list[dict]:
    """校验 rules.json：action_id 必须合法，条件结构必须可求值，坏规则跳过并告警。"""
    if raw is None:
        return []
    if not isinstance(raw, dict):
        warnings.append("rules.json: 顶层不是 JSON 对象")
        return []
    raw_rules = raw.get("rules")
    if not isinstance(raw_rules, list):
        warnings.append("rules.json: 缺少 rules 数组")
        return []

    rules: list[dict] = []
    for index, item in enumerate(raw_rules):
        if not isinstance(item, dict):
            warnings.append(f"rules.json: 第 {index + 1} 条规则不是对象，已跳过")
            continue
        rule_id = _as_text(item.get("id"), f"R{index + 1:03d}")
        action_id = _as_text(item.get("action_id"))
        if action_id not in ACTION_EFFECTS:
            warnings.append(f"规则 {rule_id}: action_id {action_id!r} 不在 ACTION_EFFECTS 中，已跳过")
            continue

        condition = item.get("condition")
        if not isinstance(condition, dict):
            warnings.append(f"规则 {rule_id}: condition 不是对象，已跳过")
            continue
        field = condition.get("field")
        op = condition.get("op")
        value = condition.get("value")
        if field not in FEATURE_FIELDS:
            warnings.append(f"规则 {rule_id}: condition.field {field!r} 非法，已跳过")
            continue
        if op not in _VALID_OPS:
            warnings.append(f"规则 {rule_id}: condition.op {op!r} 非法，已跳过")
            continue
        if field == "light_angle" and op in ("in", "not_in"):
            if not isinstance(value, (list, tuple)) or not value:
                warnings.append(f"规则 {rule_id}: light_angle 的 {op} 需要非空数组，已跳过")
                continue
        elif op not in ("==",) and _as_float(value) is None:
            warnings.append(f"规则 {rule_id}: condition.value 不是数值，已跳过")
            continue

        raw_applies = item.get("applies_to")
        applies_to = (
            [_as_text(entry) for entry in raw_applies if _as_text(entry)]
            if isinstance(raw_applies, list)
            else []
        )
        if not applies_to:
            warnings.append(f"规则 {rule_id}: applies_to 缺失，已默认 all")
            applies_to = ["all"]
        unknown_intents = [
            intent_id for intent_id in applies_to if intent_id != "all" and intent_id not in known_intent_ids
        ]
        if unknown_intents:
            warnings.append(f"规则 {rule_id}: applies_to 含未知意图 {unknown_intents}（仅 all 与已知意图生效）")

        priority = 0
        raw_priority = item.get("priority")
        if isinstance(raw_priority, (int, float)) and not isinstance(raw_priority, bool):
            priority = int(raw_priority)
        elif raw_priority is not None:
            warnings.append(f"规则 {rule_id}: priority 非数值，按 0 处理")

        rules.append(
            {
                "id": rule_id,
                "condition": {"field": field, "op": op, "value": value},
                "applies_to": applies_to,
                "action_id": action_id,
                "reason": _as_text(item.get("reason")),
                "priority": priority,
                "_order": index,
            }
        )
    return rules


def _validate_phrases(raw: Any, warnings: list[str]) -> tuple[list[dict], dict[str, dict]]:
    """校验 phrases.json，并与 ACTION_EFFECTS 键集对齐（不一致只告警不抛）。"""
    if raw is None:
        return [], {}
    if not isinstance(raw, dict):
        warnings.append("phrases.json: 顶层不是 JSON 对象")
        return [], {}
    raw_phrases = raw.get("phrases")
    if not isinstance(raw_phrases, list):
        warnings.append("phrases.json: 缺少 phrases 数组")
        return [], {}

    phrases: list[dict] = []
    phrase_map: dict[str, dict] = {}
    for index, item in enumerate(raw_phrases):
        if not isinstance(item, dict):
            warnings.append(f"phrases.json: 第 {index + 1} 条话术不是对象，已跳过")
            continue
        phrase_id = _as_text(item.get("id"))
        if not phrase_id:
            warnings.append(f"phrases.json: 第 {index + 1} 条话术缺少 id，已跳过")
            continue
        if phrase_id in phrase_map:
            warnings.append(f"phrases.json: 动作 id 重复 {phrase_id!r}，已跳过后者")
            continue

        raw_affects = item.get("affects")
        affects = (
            [_as_text(entry) for entry in raw_affects if _as_text(entry)]
            if isinstance(raw_affects, list)
            else []
        )
        unknown_affects = [field for field in affects if field not in FEATURE_FIELDS]
        if unknown_affects:
            warnings.append(f"话术 {phrase_id}: affects 含未知维度 {unknown_affects}")

        phrase = {
            "id": phrase_id,
            "action": _as_text(item.get("action"), phrase_id),
            "category": _as_text(item.get("category")),
            "affects": [field for field in affects if field in FEATURE_FIELDS],
            "delta_hint": _as_text(item.get("delta_hint")),
            "reason_template": _as_text(item.get("reason_template")),
            "condition_hint": _as_text(item.get("condition_hint")),
        }
        phrases.append(phrase)
        phrase_map[phrase_id] = phrase

    phrase_ids = set(phrase_map)
    effect_ids = set(ACTION_EFFECTS)
    missing_ids = sorted(effect_ids - phrase_ids)
    extra_ids = sorted(phrase_ids - effect_ids)
    if missing_ids or extra_ids:
        parts = []
        if missing_ids:
            parts.append(f"缺少 {missing_ids}")
        if extra_ids:
            parts.append(f"多余 {extra_ids}")
        warnings.append("phrases.json 与 ACTION_EFFECTS 键集不一致（" + "，".join(parts) + "）")
    return phrases, phrase_map


def _build_resources(directory: Path) -> dict:
    warnings: list[str] = []

    intent_raw = _read_json(directory / "intent_weights.json", warnings)
    rules_raw = _read_json(directory / "rules.json", warnings)
    phrases_raw = _read_json(directory / "phrases.json", warnings)

    intents = _validate_intents(intent_raw, warnings)
    if not intents:
        intents = [
            {
                **intent,
                "weights": dict(intent["weights"]),
                "targets": {key: list(value) for key, value in intent["targets"].items()},
                "keywords": list(intent["keywords"]),
                "preferred_actions": list(intent["preferred_actions"]),
            }
            for intent in _FALLBACK_INTENTS
        ]
        warnings.append("intent_weights.json 不可用，已使用内置意图表（结果仅供演示）")

    known_intent_ids = {intent["id"] for intent in intents if intent.get("id")}
    rules = _validate_rules(rules_raw, warnings, known_intent_ids)
    phrases, phrase_map = _validate_phrases(phrases_raw, warnings)

    intent_version = _as_text(intent_raw.get("version")) if isinstance(intent_raw, dict) else ""
    rules_version = _as_text(rules_raw.get("version")) if isinstance(rules_raw, dict) else ""
    phrases_version = _as_text(phrases_raw.get("version")) if isinstance(phrases_raw, dict) else ""

    return {
        "intent_data": {"version": intent_version or "fallback", "intents": intents},
        "rules_data": {"version": rules_version, "rules": rules},
        "phrases_data": {"version": phrases_version, "phrases": phrases},
        "phrase_map": phrase_map,
        "warnings": warnings,
    }


def load_resources(data_dir: Any = None) -> dict:
    """加载并校验三份数据文件，返回契约 §2.2 结构。

    ``data_dir`` 可为 str/Path/None（None → ``<repo>/data``）。
    结果按三个文件的 mtime_ns 做进程内缓存：文件没变就直接复用，变了自动重读。

    返回 ``{"intent_data","rules_data","phrases_data","phrase_map","warnings"}``；
    任何数据问题都只进 ``warnings``，不抛异常。
    """
    directory = Path(data_dir) if data_dir is not None else DATA_DIR
    try:
        directory = directory.expanduser().resolve()
    except (OSError, RuntimeError):  # 极端路径问题，退回原样
        directory = Path(directory)

    cache_key = str(directory)
    signature = _mtime_signature(directory)
    cached = _RESOURCE_CACHE.get(cache_key)
    if cached is not None and cached[0] == signature:
        return cached[1]

    resources = _build_resources(directory)
    _RESOURCE_CACHE[cache_key] = (signature, resources)
    return resources


def clear_resource_cache() -> None:
    """清空进程内资源缓存（测试 / 手动刷新用）。"""
    _RESOURCE_CACHE.clear()


# ---------------------------------------------------------------------------
# 建议生成
# ---------------------------------------------------------------------------
def _ensure_resources(resources: Any) -> dict:
    """调用方传了完整 resources 就复用，否则走（带缓存的）load_resources。"""
    if (
        isinstance(resources, dict)
        and isinstance(resources.get("intent_data"), dict)
        and isinstance(resources.get("rules_data", {}).get("rules"), list)
    ):
        return resources
    return load_resources()


def _primary_gap(x: dict, intent: dict, before_score: dict) -> dict | None:
    """主要矛盾：四个维度里加权差距（w·(f-1)，≤0）最负的那个。

    direction：低于目标下限 → ``low``；高于目标上限 → ``high``；
    光线方向不合目标 → ``mismatch``。四维全部达标（f 都为 1）时返回 None。
    """
    weights, targets = _intent_scoring_params(intent)
    components = before_score.get("components") or {}
    best: tuple[float, int, dict] | None = None

    for order, field in enumerate(FEATURE_FIELDS):
        component = _as_float(components.get(field))
        if component is None:
            continue
        weighted = weights[field] * (component - 1.0)

        if field == "light_angle":
            direction = None if component >= 1.0 else "mismatch"
        elif field == "bg_complexity":
            value = x["c"]
            upper = targets["bg_complexity"][1]
            direction = "high" if value > upper else None
        else:
            value = x["r"] if field == "subject_ratio" else x["g"]
            lower, upper = targets[field]
            if value < lower:
                direction = "low"
            elif value > upper:
                direction = "high"
            else:
                direction = None

        if direction is None:
            continue
        item = (weighted, order, {"field": field, "weighted_gap": round(weighted, 3), "direction": direction})
        if best is None or item[0] < best[0] or (item[0] == best[0] and order < best[1]):
            best = item

    if best is None:
        return None
    gap = best[2]
    if gap["weighted_gap"] == 0:
        gap["weighted_gap"] = 0.0  # 避免输出 -0.0
    return gap


def _make_candidate(
    action_id: str,
    x: dict,
    intent: dict,
    phrase_map: dict,
    before_total: float,
    priority: int,
    rule_reason: str,
) -> dict:
    """构造一条候选：估计执行后画面 → 期望收益 ΔS、话术、理由。"""
    after_score = score_state(apply_action(x, action_id), intent)
    expected_gain = round(after_score["total"] - before_total, 3)
    phrase = phrase_map.get(action_id) if isinstance(phrase_map, dict) else None
    phrase = phrase if isinstance(phrase, dict) else {}

    action_text = _as_text(phrase.get("action"), action_id)
    delta_text = _as_text(phrase.get("delta_hint"))
    reason = _as_text(rule_reason) or _as_text(phrase.get("reason_template")) or _PHRASE_TEXT_FALLBACK
    return {
        "action_id": action_id,
        "action": action_text,
        "delta": delta_text,
        "expected_gain": expected_gain,
        "reason": reason,
        "priority": int(priority),
    }


def _rank_candidates(candidates: list[dict], limit: int = 3) -> list[dict]:
    """排序：expected_gain 降序 → priority 降序 → action_id 升序（确定性）。"""
    ranked = sorted(
        candidates,
        key=lambda item: (-item["expected_gain"], -item["priority"], item["action_id"]),
    )
    return ranked[:limit]


def _rule_candidates(x: dict, intent: dict, resources: dict, before_total: float) -> list[dict]:
    """规则筛选 → 动作 ΔS 排序。同一动作被多条规则命中时取 priority 更高（再取靠前）的那条。"""
    rules = (resources.get("rules_data") or {}).get("rules") or []
    phrase_map = resources.get("phrase_map") or {}
    intent_id = _as_text(intent.get("id"))

    by_action: dict[str, dict] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        applies = rule.get("applies_to") or []
        if "all" not in applies and intent_id not in applies:
            continue
        if not evaluate_condition(rule.get("condition"), x):
            continue
        action_id = _as_text(rule.get("action_id"))
        if action_id not in ACTION_EFFECTS:
            continue
        priority = int(rule.get("priority") or 0)
        candidate = _make_candidate(
            action_id,
            x,
            intent,
            phrase_map,
            before_total,
            priority,
            _as_text(rule.get("reason")),
        )
        existing = by_action.get(action_id)
        # 同一动作保留 priority 更大的规则；平手保留先出现的那条（规则表顺序）
        if existing is None or candidate["priority"] > existing["priority"]:
            by_action[action_id] = candidate
    return list(by_action.values())


def _fallback_candidates(
    x: dict,
    intent: dict,
    resources: dict,
    before_total: float,
    primary_gap: dict | None,
) -> tuple[list[dict], str]:
    """兜底：preferred_actions → primary_gap 映射 → 通用默认动作。"""
    phrase_map = resources.get("phrase_map") or {}

    preferred_raw = intent.get("preferred_actions")
    preferred = (
        [action for action in preferred_raw if isinstance(action, str) and action in ACTION_EFFECTS]
        if isinstance(preferred_raw, list)
        else []
    )

    actions: list[str] = []
    source = ""
    for action_id in preferred:
        if action_id not in actions:
            actions.append(action_id)
    if actions:
        source = "preferred_actions"

    if not actions:
        mapping = _GAP_FALLBACK_ACTIONS.get((primary_gap or {}).get("field", ""), {})
        gap_action = mapping.get((primary_gap or {}).get("direction", ""))
        if gap_action in ACTION_EFFECTS:
            actions.append(gap_action)
            source = "primary_gap"
        for action_id in _DEFAULT_FALLBACK_ACTIONS:
            if action_id not in actions:
                actions.append(action_id)
        source = source or "default_actions"

    candidates = [
        _make_candidate(action_id, x, intent, phrase_map, before_total, 0, "")
        for action_id in actions[:5]
    ]
    return candidates, source


def _empty_result(features: dict, warnings: list[str], reason: str) -> dict:
    """极端降级结果：结构完整、suggestions 为空、warnings 说明原因。"""
    return {
        "intent_detected": DEFAULT_INTENT_ID,
        "intent_label": "人物中景",
        "features": features,
        "current_score": {
            "total": 0.0,
            "components": {"subject_ratio": 0.0, "bg_complexity": 0.0, "light_angle": 0.0, "gaze_space": 0.0},
        },
        "primary_gap": None,
        "suggestions": [],
        "warnings": warnings + [reason],
    }


def suggest(intent_text: Any, features: Any, resources: dict | None = None) -> dict:
    """纯函数：意图 + 画面特征 → §2.3 结构建议（永不抛异常）。

    ``intent_text`` 可为空；``features`` 允许缺省/别名（见 ``normalize_features``）。
    ``resources`` 传 ``load_resources()`` 的结果可复用缓存；不传则内部加载。
    """
    normalized = normalize_features(features)
    warnings: list[str] = []
    try:
        res = _ensure_resources(resources)
        warnings = list(res.get("warnings") or [])

        intents = (res.get("intent_data") or {}).get("intents") or _FALLBACK_INTENTS
        intent = match_intent(intent_text, {"intents": intents})
        intent_id = _as_text(intent.get("id"), DEFAULT_INTENT_ID)
        intent_label = _as_text(intent.get("label"), intent_id)

        x = _to_internal(normalized)
        before = score_state(x, intent)
        primary_gap = _primary_gap(x, intent, before)

        candidates = _rule_candidates(x, intent, res, before["total"])
        if candidates:
            ranked = _rank_candidates(candidates, 3)
        else:
            fallback, source = _fallback_candidates(x, intent, res, before["total"], primary_gap)
            ranked = _rank_candidates(fallback, 3)
            warnings.append(f"规则库未命中，已使用兜底动作（来源: {source}）")

        suggestions = [
            {
                "rank": index + 1,
                "action_id": candidate["action_id"],
                "action": candidate["action"],
                "delta": candidate["delta"],
                "expected_gain": candidate["expected_gain"],
                "reason": candidate["reason"],
            }
            for index, candidate in enumerate(ranked)
        ]
        return {
            "intent_detected": intent_id,
            "intent_label": intent_label,
            "features": normalized,
            "current_score": before,
            "primary_gap": primary_gap,
            "suggestions": suggestions,
            "warnings": warnings,
        }
    except Exception as exc:  # pragma: no cover - 防御性兜底，正常路径不应触发
        return _empty_result(normalized, warnings, f"建议引擎内部异常，已降级: {exc!r}")


# ---------------------------------------------------------------------------
# CLI（保留 kit 参数 + 可选 --data-dir）
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 现场拍摄指导助手 - 建议引擎")
    parser.add_argument("--intent", default="电影感人物特写，背景虚化", help="用户自然语言要求")
    parser.add_argument("--ratio", type=float, default=0.18, help="主体占比 0-1")
    parser.add_argument("--bg", type=float, default=0.42, help="背景复杂度 0-1")
    parser.add_argument("--light", default="front", help="光线方向 front/side/back/low/golden")
    parser.add_argument("--gaze", type=float, default=0.30, help="视线空间 0-1")
    parser.add_argument("--stdin", action="store_true", help="从标准输入读取完整 JSON（intent + frame_features）")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="数据目录（默认 <repo>/data；自测时可指向 D:\\videoshoot\\reference\\kit）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    resources = load_resources(args.data_dir) if args.data_dir else None

    if args.stdin:
        raw = sys.stdin.read()
        payload: Any = {}
        if raw.strip():
            try:
                payload = json.loads(raw)
            except ValueError as exc:
                print(f"[warn] --stdin 输入不是合法 JSON，已按空输入处理: {exc}", file=sys.stderr)
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        intent_text = payload.get("intent", payload.get("intent_text", ""))
        features = payload.get("frame_features", payload.get("features", {}))
    else:
        intent_text = args.intent
        features = {
            "subject_ratio": args.ratio,
            "bg_complexity": args.bg,
            "light_direction": args.light,
            "gaze_space": args.gaze,
        }

    result = suggest(intent_text, features, resources=resources)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
