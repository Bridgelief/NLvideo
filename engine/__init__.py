# -*- coding: utf-8 -*-
"""engine 包：决策内核（纯函数，不依赖网络与 UI）。

模块划分见 docs/INTERFACES.md §2：
- action_effects: 50 个动作 ID 的特征影响估计（唯一真值表）
- suggest_engine: 意图解析 + 评分 + Top-3 建议
- edit_engine: 素材整理 + 粗剪 EDL
- edl_export: EDL JSON / FFmpeg 命令 / .bat 生成
"""

from __future__ import annotations

__version__ = "1.2.0"
