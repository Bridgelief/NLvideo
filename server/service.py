# -*- coding: utf-8 -*-
"""纯业务编排（不依赖 HTTP，可被 tests 直接 import 调用）。

对外函数（docs/INTERFACES.md §4）：
- ``suggest_service(intent, frame_features=None, frame_base64=None)`` → §2.3 + frame_features + overlay + timing_ms
- ``record_shot_service(payload)`` / ``list_shots_service()`` / ``reset_shots_service()``
- ``feedback_service(payload)``
- ``materials_service()`` / ``edit_service(source="materials")`` / ``demo_service()`` / ``health_service()``

engine/ 一律**惰性 import**（函数内部 import）。algo 尚未交付时服务仍能启动，
``suggest`` / ``edit`` 会走**服务端兜底实现**并在 ``warnings`` 里明确说明，
绝不假装是引擎结果。
"""

from __future__ import annotations

import base64
import csv
import inspect
import json
import platform
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from server import __version__, features, store

try:  # 仓库根（供兜底逻辑读取 reference/）
    ROOT = store.ROOT
except Exception:  # pragma: no cover
    ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = store.DATA_DIR
SERVER_DIR = Path(__file__).resolve().parent
STATIC_DIR = SERVER_DIR / "static"
DEMO_DIR = STATIC_DIR / "demo"
SCENARIOS_PATH = DEMO_DIR / "scenarios.json"

#: 当前拍摄记录为空/素材缺失时的兜底示例素材（reference/ 只读，仅读取不修改）
REFERENCE_MATERIALS = ROOT / "reference" / "kit" / "materials.csv"

_INTENT_KEYWORD_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("portrait_closeup", ("特写", "虚化", "电影感", "脸部", "半身", "眼神")),
    ("product", ("产品", "商品", "静物", "带货", "细节", "质感")),
    ("emotional", ("氛围", "情绪", "黄昏", "日落", "治愈", "故事")),
    ("landscape", ("风光", "风景", "环境", "旅拍", "城市", "建筑")),
    ("portrait_medium", ("人像", "人物", "街拍", "中景")),
)

#: 兜底建议用到的中文动作文案（正式文案以 data/phrases.json 为准）
_ACTION_TEXT_FALLBACK: dict[str, str] = {
    "step_forward": "往前走一步",
    "step_forward_close": "再靠近半步",
    "step_back": "后退两步",
    "step_back_more": "再退两步",
    "longer_focal": "换长焦端",
    "clean_background": "换个干净背景",
    "remove_clutter": "移开杂物",
    "blur_background": "开大光圈虚化",
    "side_light": "改侧光",
    "back_light": "试逆光轮廓",
    "golden_hour": "等个暖光",
    "front_light": "转成顺光",
    "shift_subject_third": "主体挪到三分之一",
    "gaze_room": "给视线留白",
    "step_left": "往左挪半步",
}

#: engine.edit_engine 未就绪时的兜底剪辑规则（与 INTERFACES §3.4 的缺省一致）
_DEFAULT_EDIT_RULES: dict[str, Any] = {
    "version": "1.1-fallback",
    "quality_filters": [
        {"id": "Q001", "field": "stability", "op": "<", "value": 0.45, "reason": "画面抖动严重，无法使用"},
        {"id": "Q002", "field": "focus_ok", "op": "==", "value": 0, "reason": "主体失焦"},
        {"id": "Q003", "field": "has_mistake", "op": "==", "value": 1, "reason": "存在闭眼或穿帮"},
        {"id": "Q004", "field": "duration_sec", "op": "<", "value": 1.0, "reason": "片段太短，不够剪"},
        {"id": "Q005", "field": "quality_score", "op": "<", "value": 0.5, "reason": "构图质量分过低"},
    ],
    "priority_bonus": [
        {"id": "P001", "field": "followed_advice", "op": "==", "value": 1, "bonus": 0.15,
         "reason": "按拍摄建议调整过，通常质量更好"},
        {"id": "P002", "field": "quality_score", "op": ">", "value": 0.75, "bonus": 0.10,
         "reason": "构图评分高"},
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
    "transitions": {
        "after_intent": "emotional",
        "after_intent_transition": "淡入淡出",
        "same_scene_type_transition": "硬切",
        "default_transition": "叠化",
        "reason_templates": {
            "淡入淡出": "情绪镜头后留呼吸",
            "硬切": "同一场地硬切最自然",
            "叠化": "场景或镜头类型变化用叠化过渡",
            "无": "开篇镜头",
        },
    },
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000.0))


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是", "有用"}
    return False


def _clamp01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _decode_image_bytes(value: Any) -> bytes | None:
    """把请求里的 dataURL/base64 字符串解成原始图片字节；无效返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("data:"):
        comma = text.find(",")
        if comma < 0:
            return None
        text = text[comma + 1:]
    text = "".join(text.split())
    if not text:
        return None
    text += "=" * (-len(text) % 4)
    try:
        raw = base64.b64decode(text, validate=False)
    except (ValueError, TypeError):
        return None
    if not raw:
        return None
    if features.decode_image(value) is None:  # 校验确实是可解码图片
        return None
    return raw


def _intent_hint(intent_text: str) -> str:
    """没有 intent_id 时，按意图关键词粗略推断（仅用于 shot_log 兜底）。"""
    text = intent_text or ""
    for intent_id, keywords in _INTENT_KEYWORD_HINTS:
        if any(kw in text for kw in keywords):
            return intent_id
    return "portrait_medium"


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            return [row for row in csv.DictReader(fh) if row]
    except (OSError, csv.Error):
        return []


def _coerce_materials_row(row: dict[str, Any]) -> dict[str, Any]:
    """把 materials/shot 行里的数值字段转成 float/int（引擎与 API 都吃这套类型）。"""
    result = dict(row)
    for key in ("duration_sec", "quality_score", "stability", "in_point", "out_point"):
        result[key] = _to_float(result.get(key), 0.0)
    for key in ("focus_ok", "has_mistake", "followed_advice"):
        result[key] = _to_int(result.get(key), 0)
    for key in store.MATERIALS_COLUMNS:
        result.setdefault(key, "")
    return result


# ---------------------------------------------------------------------------
# 特征归一化
# ---------------------------------------------------------------------------
def _normalize_features(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """把请求里的 frame_features 归一成契约 §2.2 的四个键（带别名兼容）。"""
    warnings: list[str] = []
    features_dict: dict[str, Any] = dict(features.DEFAULT_FEATURES)
    if not isinstance(raw, dict):
        return features_dict, ["frame_features 不是 JSON 对象，已使用默认特征"]

    def pick(keys: tuple[str, ...], default: float) -> float:
        for key in keys:
            if key in raw and raw[key] is not None:
                try:
                    return _clamp01(float(raw[key]))
                except (TypeError, ValueError):
                    warnings.append(f"frame_features.{key} 不是数值，已用默认值 {default}")
                    return default
        return default

    features_dict["subject_ratio"] = round(pick(("subject_ratio", "r"), 0.30), 3)
    features_dict["bg_complexity"] = round(pick(("bg_complexity", "edge_density", "c"), 0.30), 3)
    features_dict["gaze_space"] = round(pick(("gaze_space", "g"), 0.30), 3)
    light_raw = raw.get("light_direction", raw.get("l", "front"))
    light = str(light_raw) if light_raw is not None else "front"
    if light not in features.LIGHT_DIRECTIONS:
        warnings.append(f"light_direction={light!r} 不在取值域 {features.LIGHT_DIRECTIONS}，引擎会按“不合目标”计分")
    features_dict["light_direction"] = light
    return features_dict, warnings


# ---------------------------------------------------------------------------
# 建议（engine 优先，缺失时兜底）
# ---------------------------------------------------------------------------
def _score_range(value: float, lo: float, hi: float) -> float:
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return max(0.0, 1.0 - (lo - value) / lo) if lo > 0 else 0.0
    return max(0.0, 1.0 - (value - hi) / (1.0 - hi)) if hi < 1.0 else 0.0


def _fallback_score(features_dict: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    targets = intent.get("targets") or {}
    weights = intent.get("weights") or {}
    fallback_components = {
        "subject_ratio": 0.5,
        "bg_complexity": 0.5,
        "light_angle": 0.5,
        "gaze_space": 0.5,
    }
    try:
        subject_target = targets.get("subject_ratio")
        if subject_target:
            fallback_components["subject_ratio"] = _score_range(
                features_dict["subject_ratio"], float(subject_target[0]), float(subject_target[1])
            )
        bg_target = targets.get("bg_complexity")
        if bg_target:
            fallback_components["bg_complexity"] = _score_range(
                features_dict["bg_complexity"], 0.0, float(bg_target[1])
            )
        light_target = targets.get("light_angle") or []
        fallback_components["light_angle"] = 1.0 if features_dict["light_direction"] in light_target else 0.4
        gaze_target = targets.get("gaze_space")
        if gaze_target:
            fallback_components["gaze_space"] = _score_range(
                features_dict["gaze_space"], float(gaze_target[0]), float(gaze_target[1])
            )
    except (TypeError, ValueError, IndexError):
        return {"total": 0.5, "components": fallback_components}
    total = sum(
        float(weights.get(dim, 0.25)) * fallback_components[dim]
        for dim in ("subject_ratio", "bg_complexity", "light_angle", "gaze_space")
    )
    return {
        "total": round(total, 3),
        "components": {key: round(value, 3) for key, value in fallback_components.items()},
    }


def _fallback_gap(features_dict: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any] | None:
    targets = intent.get("targets") or {}
    weights = intent.get("weights") or {}
    candidates: list[dict[str, Any]] = []
    pairs = (
        ("subject_ratio", "subject_ratio", features_dict["subject_ratio"]),
        ("bg_complexity", "bg_complexity", features_dict["bg_complexity"]),
        ("gaze_space", "gaze_space", features_dict["gaze_space"]),
    )
    for dim, field, actual in pairs:
        target = targets.get(dim)
        weight = float(weights.get(dim, 0.25))
        try:
            lo, hi = float(target[0]), float(target[1])
        except (TypeError, ValueError, IndexError):
            continue
        direction = None
        if actual < lo:
            direction = "low"
            gap = -(lo - actual) * weight
        elif actual > hi:
            direction = "high"
            gap = -(actual - hi) * weight
        if direction:
            candidates.append({"field": field, "weighted_gap": round(gap, 3), "direction": direction})
    light_target = targets.get("light_angle") or []
    if light_target and features_dict["light_direction"] not in light_target:
        candidates.append(
            {
                "field": "light_angle",
                "weighted_gap": round(-0.4 * float(weights.get("light_angle", 0.2)), 3),
                "direction": "low",
            }
        )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item["weighted_gap"])


def _fallback_suggest(intent_text: str, features_dict: dict[str, Any]) -> dict[str, Any]:
    """engine.suggest_engine 不可用时的**兜底**建议（诚实标记，不等价于引擎）。"""
    warnings = ["engine 未就绪：本结果是服务端兜底建议（粗略），正式演示前请等 algo 交付 engine/"]
    intent_data = _read_json(DATA_DIR / "intent_weights.json") or {}
    phrases_data = _read_json(DATA_DIR / "phrases.json") or {}
    intents = [item for item in (intent_data.get("intents") or []) if isinstance(item, dict)]
    phrase_map = {
        item.get("id"): item
        for item in (phrases_data.get("phrases") or [])
        if isinstance(item, dict) and item.get("id")
    }
    text = intent_text or ""
    matched: dict[str, Any] | None = None
    best_hits = 0
    for item in intents:
        hits = sum(1 for kw in (item.get("keywords") or []) if kw and kw in text)
        if hits > best_hits:
            matched, best_hits = item, hits
    if matched is None:
        matched = next((item for item in intents if item.get("id") == "portrait_medium"), None)
    if matched is None:
        matched = {"id": "portrait_medium", "label": "人物中景", "targets": {}, "weights": {},
                   "preferred_actions": []}
    score = _fallback_score(features_dict, matched)
    primary_gap = _fallback_gap(features_dict, matched)
    gap_actions = {
        "subject_ratio": ["step_forward", "step_forward_close", "longer_focal"],
        "bg_complexity": ["clean_background", "remove_clutter", "blur_background"],
        "light_angle": ["side_light", "back_light", "golden_hour", "front_light"],
        "gaze_space": ["shift_subject_third", "gaze_room", "step_left"],
    }
    pool: list[str] = []
    if primary_gap:
        pool.extend(gap_actions.get(primary_gap["field"], []))
    pool.extend(matched.get("preferred_actions") or [])
    pool.extend(["step_forward", "clean_background", "shift_subject_third"])
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action_id in pool:
        if not action_id or action_id in seen:
            continue
        seen.add(action_id)
        phrase = phrase_map.get(action_id, {})
        suggestions.append(
            {
                "rank": len(suggestions) + 1,
                "action_id": action_id,
                "action": phrase.get("action") or _ACTION_TEXT_FALLBACK.get(action_id, action_id),
                "delta": phrase.get("delta_hint") or "",
                "expected_gain": round(max(0.02, 0.12 - 0.025 * len(suggestions)), 3),
                "reason": phrase.get("reason_template") or "按这个动作调整，通常更接近你想要的画面",
            }
        )
        if len(suggestions) >= 3:
            break
    return {
        "intent_detected": matched.get("id", "portrait_medium"),
        "intent_label": matched.get("label", "人物中景"),
        "features": dict(features_dict),
        "current_score": score,
        "primary_gap": primary_gap,
        "suggestions": suggestions,
        "warnings": warnings,
    }


def _engine_suggest(intent_text: str, features_dict: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """调用 engine.suggest_engine（惰性 import）；任何失败都回退到兜底建议。"""
    try:
        from engine import suggest_engine  # 惰性 import：engine 未交付时 app 仍可启动
    except Exception as exc:  # ImportError 等
        return _fallback_suggest(intent_text, features_dict), [
            f"engine.suggest_engine 未就绪（{type(exc).__name__}: {exc}），已使用服务端兜底建议"
        ]
    resources = None
    load_warnings: list[str] = []
    if hasattr(suggest_engine, "load_resources"):
        try:
            resources = suggest_engine.load_resources()
        except Exception as exc:
            load_warnings.append(f"engine.load_resources 失败（{type(exc).__name__}: {exc}），将直接调用 suggest")
    try:
        if resources is not None:
            result = suggest_engine.suggest(intent_text, features_dict, resources)
        else:
            result = suggest_engine.suggest(intent_text, features_dict)
        if not isinstance(result, dict):
            raise TypeError("engine.suggest 返回值不是 dict")
        result_warnings = [str(w) for w in (result.get("warnings") or [])]
        return result, load_warnings + result_warnings
    except Exception as exc:
        return _fallback_suggest(intent_text, features_dict), load_warnings + [
            f"engine.suggest 执行失败（{type(exc).__name__}: {exc}），已使用服务端兜底建议"
        ]


def suggest_service(
    intent: str = "",
    frame_features: dict[str, Any] | None = None,
    frame_base64: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """§4.3：frame_features 优先，其次 frame_base64；两者都没有 → ValueError（app 转 400）。"""
    started = time.perf_counter()
    warnings: list[str] = []
    overlay = dict(features.DEFAULT_OVERLAY)
    extract_ms = 0

    if frame_features is not None:
        normalized, norm_warnings = _normalize_features(frame_features)
        warnings.extend(norm_warnings)
        frame_features = normalized
    elif frame_base64:
        extract_started = time.perf_counter()
        extracted = features.extract(frame_base64)
        extract_ms = _ms(extract_started)
        frame_features = extracted["frame_features"]
        overlay = extracted["overlay"]
        warnings.extend(extracted["warnings"])
    else:
        raise ValueError("请求里需要 frame_features 或 frame_base64 其中之一")

    engine_started = time.perf_counter()
    try:
        engine_result, engine_warnings = _engine_suggest(intent or "", frame_features)
    except Exception as exc:  # 防御性兜底，理论上 _engine_suggest 不抛
        engine_result = _fallback_suggest(intent or "", frame_features)
        engine_warnings = [f"建议引擎异常（{type(exc).__name__}: {exc}），已使用服务端兜底建议"]
    engine_ms = _ms(engine_started)

    result = dict(engine_result)
    result.setdefault("intent_detected", "portrait_medium")
    result.setdefault("intent_label", "人物中景")
    result.setdefault("current_score", {"total": 0.0, "components": {}})
    result.setdefault("primary_gap", None)
    suggestions = result.get("suggestions") or []
    if not isinstance(suggestions, list):
        suggestions = []
    result["suggestions"] = suggestions[:3]
    if not result["suggestions"]:
        warnings.append("引擎未返回建议（规则/数据可能缺失）")
    result["session_id"] = session_id
    result["frame_features"] = frame_features
    result["features"] = dict(frame_features)
    result["overlay"] = overlay
    result["timing_ms"] = {
        "extract": extract_ms,
        "engine": engine_ms,
        "total": _ms(started),
    }
    result["warnings"] = _dedupe(warnings + engine_warnings + [str(w) for w in (result.get("warnings") or [])])
    return result


# ---------------------------------------------------------------------------
# 拍摄记录（闭环数据）
# ---------------------------------------------------------------------------
def record_shot_service(payload: dict[str, Any]) -> dict[str, Any]:
    """§4.5：把一次拍摄写进 output/shot_log.csv + output/shots/*.jpg。

    stability / focus_ok / has_mistake 客户端可传、可覆盖；不传时用兜底：
    stability=0.9、focus_ok=拉普拉斯估计（图太小/读不到时默认 1）、has_mistake=0。
    """
    if not isinstance(payload, dict):
        payload = {}
    store.ensure_dirs()
    warnings: list[str] = []
    rows = store.read_shot_rows()
    clip_id = store.next_clip_id(rows)

    before_bytes = _decode_image_bytes(payload.get("before_image"))
    after_bytes = _decode_image_bytes(payload.get("after_image"))
    if payload.get("before_image") and before_bytes is None:
        warnings.append("before_image 不是有效图片，未保存 before 图")
    if payload.get("after_image") and after_bytes is None:
        warnings.append("after_image 不是有效图片，未保存 after 图")

    duration = _to_float(payload.get("duration_sec"), 6.0)
    if duration <= 0:
        warnings.append("duration_sec 非法（<=0），已用默认 6.0 秒")
        duration = 6.0

    focus_raw = payload.get("focus_ok")
    if focus_raw is None:
        focus_ok = features.focus_ok_from_image(after_bytes or before_bytes or b"")
    else:
        focus_ok = 1 if _truthy(focus_raw) else 0

    stability_raw = payload.get("stability")
    stability = 0.9 if stability_raw is None else _clamp01(_to_float(stability_raw, 0.9))

    mistake_raw = payload.get("has_mistake")
    has_mistake = 0 if mistake_raw is None else (1 if _truthy(mistake_raw) else 0)

    quality_raw = payload.get("quality_score")
    quality_score = 0.6 if quality_raw is None else _clamp01(_to_float(quality_raw, 0.6))

    followed_advice = 1 if _truthy(payload.get("followed_advice")) else 0
    action_ids = [str(a) for a in (payload.get("advice_action_ids") or []) if a]
    intent_text = str(payload.get("intent") or "")
    intent_id = str(payload.get("intent_id") or "").strip() or _intent_hint(intent_text)
    filename = str(payload.get("filename") or "").strip() or f"{clip_id}.mp4"

    in_point = _to_float(payload.get("in_point"), 0.3)
    in_point = max(0.0, min(in_point, max(0.0, duration - 0.5)))
    out_raw = payload.get("out_point")
    out_point = _to_float(out_raw, round(max(in_point + 0.5, duration - 0.3), 2)) if out_raw is not None \
        else round(max(in_point + 0.5, duration - 0.3), 2)
    out_point = min(duration, max(out_point, in_point + 0.5))

    notes = str(payload.get("notes") or "").strip()
    if not notes:
        if followed_advice and action_ids:
            notes = "按建议调整：" + "、".join(action_ids)
        else:
            notes = "现场拍摄记录"

    row: dict[str, Any] = {
        "clip_id": clip_id,
        "filename": filename,
        "duration_sec": round(duration, 2),
        "scene_type": str(payload.get("scene_type") or "outdoor"),
        "intent_id": intent_id,
        "quality_score": round(quality_score, 3),
        "stability": round(stability, 3),
        "focus_ok": int(focus_ok),
        "has_mistake": int(has_mistake),
        "duplicate_of": str(payload.get("duplicate_of") or ""),
        "followed_advice": followed_advice,
        "in_point": round(in_point, 2),
        "out_point": round(out_point, 2),
        "notes": notes,
    }
    before_url, after_url = store.save_shot_images(clip_id, before_bytes, after_bytes)
    row["before_url"] = before_url
    row["after_url"] = after_url
    count = store.append_shot_record(row)

    response_row = {key: row[key] for key in store.MATERIALS_COLUMNS}
    return {
        "ok": True,
        "clip_id": clip_id,
        "count": count,
        "shot_log": "output/shot_log.csv",
        "before_url": before_url,
        "after_url": after_url,
        "row": response_row,
        "warnings": _dedupe(warnings),
    }


def list_shots_service() -> dict[str, Any]:
    rows = store.read_shot_rows()
    clips = []
    for row in rows:
        clip_id = (row.get("clip_id") or "").strip()
        clips.append(
            {
                "clip_id": clip_id,
                "intent_id": row.get("intent_id") or "",
                "quality_score": round(_to_float(row.get("quality_score"), 0.0), 3),
                "followed_advice": _to_int(row.get("followed_advice"), 0),
                "before_url": row.get("before_url") or f"/output/shots/{clip_id}_before.jpg",
                "after_url": row.get("after_url") or f"/output/shots/{clip_id}_after.jpg",
            }
        )
    return {"count": len(clips), "clips": clips}


def reset_shots_service() -> dict[str, Any]:
    return {"ok": True, "cleared": store.reset_shots()}


# ---------------------------------------------------------------------------
# 反馈
# ---------------------------------------------------------------------------
def feedback_service(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    record = {
        "ts": _now_iso(),
        "session_id": str(payload.get("session_id") or "unknown"),
        "action_id": str(payload.get("action_id") or ""),
        "rank": _to_int(payload.get("rank"), 0),
        "useful": bool(_truthy(payload.get("useful"))),
        "intent_id": str(payload.get("intent_id") or ""),
    }
    recorded = store.append_feedback(record)
    return {"ok": True, "recorded": recorded, "path": "output/feedback.jsonl"}


# ---------------------------------------------------------------------------
# 素材清单
# ---------------------------------------------------------------------------
def materials_service() -> dict[str, Any]:
    path = DATA_DIR / "materials.csv"
    if not path.exists():
        return {
            "source": "data/materials.csv",
            "count": 0,
            "rows": [],
            "warnings": ["data/materials.csv 尚未就绪（由 domain 交付），暂无示例素材"],
        }
    rows = [_coerce_materials_row(row) for row in _read_csv_rows(path)]
    return {"source": "data/materials.csv", "count": len(rows), "rows": rows}


# ---------------------------------------------------------------------------
# 演示场景
# ---------------------------------------------------------------------------
def demo_service() -> dict[str, Any]:
    """§4.2：读取 server/static/demo/scenarios.json；缺失时现场生成一次。"""
    warnings: list[str] = []

    def _needs_generate() -> bool:
        if not SCENARIOS_PATH.exists():
            return True
        try:
            data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
            scenarios = data.get("scenarios") if isinstance(data, dict) else data
            if not scenarios:
                return True
            for item in scenarios:
                url = str(item.get("frame_url") or "")
                name = url.rsplit("/", 1)[-1]
                if not name or not (DEMO_DIR / name).exists():
                    return True
            return False
        except (OSError, json.JSONDecodeError):
            return True

    if _needs_generate():
        try:
            from tools.make_demo_frames import generate  # 惰性 import

            generate()
        except Exception as exc:
            warnings.append(f"demo 帧生成失败（{type(exc).__name__}: {exc}），请手动运行 python tools/make_demo_frames.py")
    try:
        data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"scenarios": [], "warnings": warnings + [f"读不到 scenarios.json：{exc}"]}
    scenarios = data.get("scenarios") if isinstance(data, dict) else data
    if not isinstance(scenarios, list):
        scenarios = []
    response: dict[str, Any] = {"scenarios": scenarios}
    if warnings:
        response["warnings"] = warnings
    return response


# ---------------------------------------------------------------------------
# 粗剪（engine 优先，缺失时兜底）
# ---------------------------------------------------------------------------
def _call_edit_run(edit_engine: Any, materials_path: Path) -> dict[str, Any]:
    """按契约 run(materials=None, rules=None, data_dir=None) 调用，兼容旧签名。"""
    run = edit_engine.run
    try:
        params = set(inspect.signature(run).parameters)
    except (TypeError, ValueError):  # pragma: no cover
        params = {"materials", "rules", "data_dir"}
    kwargs: dict[str, Any] = {}
    if "materials" in params:
        kwargs["materials"] = str(materials_path)
    elif "materials_name" in params:  # pragma: no cover - 旧 kit 风格
        kwargs["materials_name"] = str(materials_path)
    if "data_dir" in params:
        kwargs["data_dir"] = str(DATA_DIR)
    if "rules" in params:
        kwargs["rules"] = None
    try:
        return run(**kwargs)
    except TypeError:  # pragma: no cover - 签名不匹配时退回位置参数
        return run(str(materials_path))


def _fallback_edit(materials_path: Path) -> dict[str, Any]:
    """engine.edit_engine 不可用时的兜底整理（逻辑近似 kit，仅用于引擎未交付窗口）。"""
    warnings = ["engine.edit_engine 未就绪：本结果是服务端兜底整理（近似 kit 逻辑）"]
    rules = _read_json(DATA_DIR / "edit_rules.json") or dict(_DEFAULT_EDIT_RULES)
    if not rules.get("edit_rules.json") and not (DATA_DIR / "edit_rules.json").exists():
        warnings.append("data/edit_rules.json 缺失，已使用内置缺省剪辑规则")
    rows = [_coerce_materials_row(row) for row in _read_csv_rows(materials_path)]
    usable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []

    def _eval(clip: dict[str, Any], cond: dict[str, Any]) -> bool:
        field = cond.get("field")
        if field not in clip:
            return False
        actual = clip[field]
        expected = cond.get("value")
        op = cond.get("op")
        try:
            a, e = float(actual), float(expected)
        except (TypeError, ValueError):
            if op == "==":
                return str(actual) == str(expected)
            if op == "in":
                return actual in expected
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

    for index, clip in enumerate(rows):
        clip["_index"] = index
        if str(clip.get("duplicate_of") or "").strip():
            duplicates.append(
                {"clip_id": clip["clip_id"], "duplicate_of": clip["duplicate_of"], "action": "丢弃重复片段"}
            )
            continue
        hit = None
        for rule in rules.get("quality_filters") or []:
            if _eval(clip, rule):
                hit = rule
                break
        if hit:
            excluded.append({"clip_id": clip["clip_id"], "reason": hit.get("reason", ""), "rule_id": hit.get("id", "")})
            continue
        score = float(clip["quality_score"])
        bonus_reasons = ["构图质量分合格"]
        for bonus in rules.get("priority_bonus") or []:
            if _eval(clip, bonus):
                score += float(bonus.get("bonus", 0.0))
                bonus_reasons.append(bonus.get("reason", ""))
        clip["_priority"] = round(score, 3)
        clip["_bonus_reasons"] = bonus_reasons
        usable.append(clip)

    selection = rules.get("selection") or _DEFAULT_EDIT_RULES["selection"]
    intent_order = ((rules.get("ordering") or {}).get("intent_order")) or _DEFAULT_EDIT_RULES["ordering"]["intent_order"]
    order_map = {intent: index for index, intent in enumerate(intent_order)}
    ordered = sorted(usable, key=lambda c: (order_map.get(c.get("intent_id"), 99), c["_index"]))
    ordered = ordered[: int(selection.get("max_clips", 12))]

    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for index, clip in enumerate(ordered):
        duration = float(clip["duration_sec"])
        in_point = float(clip.get("in_point") or 0.0)
        out_point = float(clip.get("out_point") or duration)
        in_point = min(in_point + float(selection.get("trim_head_sec", 0.3)), max(duration - 0.5, 0.0))
        out_point = max(out_point - float(selection.get("trim_tail_sec", 0.3)), in_point + 0.5)
        seg = out_point - in_point
        if seg > float(selection.get("max_duration_sec", 8.0)):
            out_point = in_point + float(selection.get("max_duration_sec", 8.0))
        elif seg < float(selection.get("min_duration_sec", 1.5)):
            out_point = min(duration, in_point + float(selection.get("min_duration_sec", 1.5)))
        seg = round(out_point - in_point, 2)
        if index == 0:
            transition, trans_reason = "无", "开篇镜头"
        else:
            prev = ordered[index - 1]
            if prev.get("intent_id") == "emotional":
                transition, trans_reason = "淡入淡出", "情绪镜头后留呼吸"
            elif prev.get("scene_type") == clip.get("scene_type"):
                transition, trans_reason = "硬切", "同一场地硬切最自然"
            else:
                transition, trans_reason = "叠化", "场景或镜头类型变化用叠化过渡"
        timeline.append(
            {
                "order": index + 1,
                "clip_id": clip["clip_id"],
                "filename": clip["filename"],
                "in": round(in_point, 2),
                "out": round(out_point, 2),
                "duration_sec": seg,
                "timeline_in": round(cursor, 2),
                "transition_in": transition,
                "priority": clip["_priority"],
                "reason": "；".join([r for r in clip["_bonus_reasons"][:2] if r] + [trans_reason]),
            }
        )
        cursor += seg

    return {
        "organization": {
            "total": len(rows),
            "usable": len(usable),
            "excluded_count": len(excluded),
            "duplicate_count": len(duplicates),
            "excluded": excluded,
            "duplicates": duplicates,
            "usable_ids": [clip["clip_id"] for clip in usable],
        },
        "rough_cut": {
            "clip_count": len(timeline),
            "total_duration_sec": round(cursor, 2),
            "timeline": timeline,
        },
        "source": materials_path.name,
        "warnings": warnings,
    }


def _fallback_ffmpeg(
    timeline: list[dict[str, Any]],
    *,
    base_dir: str = "inputs",
    output: str = "output/rough_cut.mp4",
    fps: int = 30,
) -> dict[str, Any]:
    """engine.edl_export 不可用时的兜底 FFmpeg 命令生成（不执行，本机无 ffmpeg）。"""
    ffmpeg_path = shutil.which("ffmpeg")
    notes = [
        "本机未检测到 ffmpeg，命令仅供导出/在装有 ffmpeg 的机器上执行",
        "兜底实现（engine.edl_export 未就绪），用 trim + concat 滤镜拼时间线",
    ]
    if not timeline:
        return {
            "available": bool(ffmpeg_path),
            "ffmpeg_path": ffmpeg_path,
            "single_command": "",
            "batch_script": "@echo off\r\nrem 时间线为空，无可导出内容\r\n",
            "notes": notes + ["时间线为空，无法生成 ffmpeg 命令"],
        }
    input_parts: list[str] = []
    filter_parts: list[str] = []
    for index, seg in enumerate(timeline):
        filename = str(seg.get("filename") or f"clip_{index + 1}.mp4")
        path = f"{base_dir}/{filename}" if base_dir else filename
        input_parts.append(f'-i "{path}"')
        start = float(seg.get("in") or 0.0)
        end = float(seg.get("out") or 0.0)
        filter_parts.append(f"[{index}:v]trim=start={start:.2f}:end={end:.2f},setpts=PTS-STARTPTS[v{index}]")
    concat_inputs = "".join(f"[v{index}]" for index in range(len(timeline)))
    filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={len(timeline)}:v=1:a=0[v]"
    single_command = (
        "ffmpeg -y "
        + " ".join(input_parts)
        + f' -filter_complex "{filter_complex}"'
        + f' -map "[v]" -r {fps} "{output}"'
    )
    return {
        "available": bool(ffmpeg_path),
        "ffmpeg_path": ffmpeg_path,
        "single_command": single_command,
        "batch_script": "@echo off\r\n" + single_command + "\r\n",
        "notes": notes,
    }


def _fallback_edl(rough_cut: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "version": 1,
        "source": source,
        "generated_by": "server fallback（engine.edl_export 未就绪）",
        "timeline": list(rough_cut.get("timeline") or []),
    }


def edit_service(source: str = "materials") -> dict[str, Any]:
    """§4.9：素材整理 + 粗剪 EDL + FFmpeg 命令。

    source="materials" 用 data/materials.csv；source="shots" 用 output/shot_log.csv，
    为空时自动回退 materials 并在 warnings 说明。
    """
    source = (source or "materials").strip().lower()
    if source not in ("materials", "shots"):
        raise ValueError('source 只能是 "materials" 或 "shots"')
    warnings: list[str] = []
    data_materials = DATA_DIR / "materials.csv"

    if source == "shots":
        if store.count_shots() > 0:
            materials_path = store.SHOT_LOG_PATH
            source_label = "output/shot_log.csv"
            response_source = "shots"
        else:
            materials_path = data_materials
            source_label = "data/materials.csv"
            response_source = "materials"
            warnings.append("本次拍摄记录为空（output/shot_log.csv 无数据），已自动回退到示例素材 data/materials.csv")
    else:
        materials_path = data_materials
        source_label = "data/materials.csv"
        response_source = "materials"

    if not materials_path.exists():
        if REFERENCE_MATERIALS.exists():
            materials_path = REFERENCE_MATERIALS
            source_label = "reference/kit/materials.csv"
            warnings.append("data/materials.csv 尚未就绪，临时回退 reference/kit/materials.csv 演示（只读参考包）")
        else:
            raise RuntimeError(f"素材清单不存在：{materials_path}（请等 domain 交付 data/materials.csv）")

    carried_warnings: list[str] = []
    engine_ready = False
    result: dict[str, Any] | None = None
    try:
        from engine import edit_engine  # 惰性 import

        result = _call_edit_run(edit_engine, materials_path)
        engine_ready = isinstance(result, dict) and isinstance(result.get("rough_cut"), dict)
        if not engine_ready:
            raise TypeError("engine.edit_engine.run 返回结构缺少 rough_cut")
    except Exception as exc:
        warnings.append(f"engine.edit_engine 未就绪（{type(exc).__name__}: {exc}），已使用服务端兜底整理")
        result = _fallback_edit(materials_path)
    if result is None:  # pragma: no cover - 防御
        result = _fallback_edit(materials_path)

    organization = result.get("organization") or {
        "total": 0,
        "usable": 0,
        "excluded_count": 0,
        "duplicate_count": 0,
        "excluded": [],
        "duplicates": [],
        "usable_ids": [],
    }
    rough_cut = result.get("rough_cut") or {"clip_count": 0, "total_duration_sec": 0.0, "timeline": []}
    timeline = list(rough_cut.get("timeline") or [])
    carried_warnings.extend(str(w) for w in (result.get("warnings") or []) if w)
    source_filename = Path(source_label).name

    ffmpeg: dict[str, Any]
    edl: dict[str, Any]
    if engine_ready:
        try:
            from engine import edl_export  # 惰性 import

            ffmpeg = edl_export.build_ffmpeg(
                timeline, base_dir="inputs", output="output/rough_cut.mp4", fps=30
            )
            edl = edl_export.build_edl_json(rough_cut, source=source_filename)
        except Exception as exc:
            warnings.append(f"engine.edl_export 未就绪（{type(exc).__name__}: {exc}），已用服务端兜底导出")
            ffmpeg = _fallback_ffmpeg(timeline)
            edl = _fallback_edl(rough_cut, source_filename)
    else:
        ffmpeg = _fallback_ffmpeg(timeline)
        edl = _fallback_edl(rough_cut, source_filename)

    payload = {
        "ok": True,
        "source": response_source,
        "organization": organization,
        "rough_cut": rough_cut,
        "ffmpeg": ffmpeg,
        "edl": edl,
        "warnings": _dedupe(warnings + carried_warnings),
    }
    try:
        store.save_edit_result(
            {
                "generated_at": _now_iso(),
                "source": response_source,
                "source_file": source_label,
                "organization": organization,
                "rough_cut": rough_cut,
                "ffmpeg": ffmpeg,
                "edl": edl,
                "warnings": payload["warnings"],
            }
        )
    except Exception:  # pragma: no cover - 落盘失败不影响返回
        pass
    return payload


def export_edit_payload() -> dict[str, Any]:
    """§4.10：优先用最近一次 /api/edit 的结果；没有则重算 materials。"""
    stored = store.load_edit_result()
    if stored and isinstance(stored.get("rough_cut"), dict):
        return stored
    return edit_service("materials")


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
def _engine_ready() -> bool:
    engine_dir = ROOT / "engine"
    return all(
        (engine_dir / name).exists()
        for name in ("suggest_engine.py", "edit_engine.py", "edl_export.py")
    )


def health_service() -> dict[str, Any]:
    """§4.1：健康检查 + 数据/引擎就绪情况（诚实标注特征提取是启发式近似）。"""
    warnings: list[str] = []
    counts = {"intents": 0, "rules": 0, "phrases": 0, "materials": 0}

    intent_data = _read_json(DATA_DIR / "intent_weights.json")
    if intent_data is not None:
        counts["intents"] = len(intent_data.get("intents") or [])
    else:
        warnings.append("data/intent_weights.json 未就绪")
    rules_data = _read_json(DATA_DIR / "rules.json")
    if rules_data is not None:
        counts["rules"] = len(rules_data.get("rules") or [])
    else:
        warnings.append("data/rules.json 未就绪")
    phrases_data = _read_json(DATA_DIR / "phrases.json")
    if phrases_data is not None:
        counts["phrases"] = len(phrases_data.get("phrases") or [])
    else:
        warnings.append("data/phrases.json 未就绪")
    materials_path = DATA_DIR / "materials.csv"
    if materials_path.exists():
        counts["materials"] = len(_read_csv_rows(materials_path))
    else:
        warnings.append("data/materials.csv 未就绪")

    ffmpeg_path = shutil.which("ffmpeg")
    response: dict[str, Any] = {
        "ok": True,
        "version": __version__,
        "python": platform.python_version(),
        "ffmpeg": bool(ffmpeg_path),
        "counts": counts,
        "data_dir": str(DATA_DIR),
        "time": _now_iso(),
        "features_note": (
            "特征提取是启发式近似、不是 MediaPipe："
            + ("Haar 人脸级联可用" if features.haar_available()
               else "本机 OpenCV 未附带 Haar 级联 XML，人脸检测不可用，主体定位走肤色/显著性/中心先验回退")
            + "；背景复杂度=Canny 边缘密度+高频能量；光线方向=亮度分区规则；"
            "视线留白=主体框位置推算。粗略但方向对，非精确测量。"
        ),
        "features_mode": "heuristic_cv",
        "haar_available": features.haar_available(),
        "engine_ready": _engine_ready(),
        "ffmpeg_path": ffmpeg_path,
    }
    if warnings:
        response["warnings"] = warnings
    return response
