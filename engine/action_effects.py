# -*- coding: utf-8 -*-
"""动作 → 画面特征影响估计表（v1 近似模型，唯一真值表）。

键集（50 个动作 ID）就是全系统动作 ID 的唯一真值：
- ``data/phrases.json`` 必须与这里**完全一一对应**（50 条，不多不少）；
- ``data/rules.json`` 的 ``action_id`` 只能引用这里的键；
- 前端、剪辑侧都只通过 ID 关联，不硬编码中文动作文案。

数值是 v1 的人工近似（不是物理建模），作用是给 ``suggest_engine`` 一个可比较的
预期收益 ΔS 用于排序；**这份表就是调参旋钮**，owner = algo。

字段含义（只允许这五个键）：
- ``r``: 主体占比增量
- ``c``: 背景复杂度增量
- ``l``: 光线方向替换值（front/side/back/low/golden）
- ``g``: 视线空间增量
- ``notes``: 给规则同学看的口径说明（可选）

来源：reference/kit/suggest_engine.py 的 ACTION_EFFECTS（原样保留数值），
并按 INTERFACES.md §2.1 的定稿键集整理（kit 的 phrases.json 里死条目 ``side_45``
不进入本表，补入 ``slow_push``）。
"""

from __future__ import annotations

ACTION_EFFECTS: dict[str, dict] = {
    "step_back": {"r": -0.12, "c": -0.05, "g": 0.03},
    "step_back_more": {"r": -0.20, "c": -0.08},
    "step_forward": {"r": 0.12, "c": 0.02},
    "step_forward_close": {"r": 0.18},
    "step_left": {"g": 0.08, "c": -0.03},
    "step_right": {"g": 0.08, "c": -0.03},
    "orbit_subject": {"g": 0.06, "l": "side"},
    "back_to_wall": {"c": -0.18},
    "longer_focal": {"r": 0.05, "c": -0.15},
    "widest": {"r": -0.10, "c": 0.05},
    "medium_focal": {"r": 0.02},
    "zoom_in_slow": {"r": 0.08},
    "zoom_out_context": {"r": -0.08, "c": 0.05},
    "avoid_wide_face": {"r": -0.05},
    "telephoto_compress": {"c": -0.12},
    "lower_camera": {"r": 0.03},
    "raise_camera": {"r": -0.02, "c": 0.03},
    "eye_level": {"g": 0.04},
    "crouch_down": {"c": -0.10, "r": 0.02},
    "knee_level": {"r": 0.02},
    "slightly_above": {"r": -0.02},
    "ground_level": {"c": -0.05},
    "dutch_angle": {"g": 0.05},
    "front_on": {"g": -0.10, "r": 0.02},
    "back_view": {"g": 0.12},
    "over_shoulder": {"g": 0.08},
    "low_angle_up": {"r": 0.05},
    "high_angle_down": {"r": -0.03},
    "side_light": {"l": "side"},
    "back_light": {"l": "back"},
    "front_light": {"l": "front"},
    "golden_hour": {"l": "golden"},
    "avoid_backlight": {"l": "front"},
    "move_to_window": {"l": "side"},
    "shade_subject": {"l": "low"},
    "shift_subject_third": {"g": 0.12, "r": -0.03},
    "shift_right_third": {"g": 0.12, "r": -0.03},
    "center_subject": {"g": -0.12},
    "gaze_room": {"g": 0.15},
    "lower_horizon": {"c": -0.05},
    "raise_horizon": {"c": -0.05},
    "leave_negative_space": {"c": -0.10, "r": -0.03},
    "frame_within_frame": {"c": 0.03},
    "clean_background": {"c": -0.20},
    "remove_clutter": {"c": -0.15},
    "clean_edge": {"c": -0.06},
    "change_angle_bg": {"c": -0.12},
    "step_for_separation": {"c": -0.10},
    "blur_background": {"c": -0.15},
    "slow_push": {"r": 0.06},
}

#: 合法光线方向类别
LIGHT_DIRECTIONS: tuple[str, ...] = ("front", "side", "back", "low", "golden")

#: 引擎内部使用的单字母键 → 对外特征字段名
FEATURE_KEYS: dict[str, str] = {
    "r": "subject_ratio",
    "c": "bg_complexity",
    "l": "light_direction",
    "g": "gaze_space",
}


def validate() -> list[str]:
    """自检动作表结构，返回问题列表（空列表 = 通过）。"""
    problems: list[str] = []
    if len(ACTION_EFFECTS) != 50:
        problems.append(f"动作数量应为 50，实际 {len(ACTION_EFFECTS)}")
    for aid, effect in ACTION_EFFECTS.items():
        unknown = set(effect) - {"r", "c", "g", "l", "notes"}
        if unknown:
            problems.append(f"{aid}: 未知字段 {sorted(unknown)}")
        if "l" in effect and effect["l"] not in LIGHT_DIRECTIONS:
            problems.append(f"{aid}: 非法光线方向 {effect['l']!r}")
        for key in ("r", "c", "g"):
            if key in effect and not isinstance(effect[key], (int, float)):
                problems.append(f"{aid}: {key} 必须是数值")
    return problems


if __name__ == "__main__":  # pragma: no cover - 手工自检
    issues = validate()
    print("actions:", len(ACTION_EFFECTS))
    print("validate:", "OK" if not issues else issues)
