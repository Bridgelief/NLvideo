# -*- coding: utf-8 -*-
"""server 包：FastAPI 服务 + 画面特征提取 + 本机落盘（output/）。

模块划分见 docs/INTERFACES.md §4：
- app:     HTTP 路由与静态挂载
- service: 纯业务编排（可被 tests 直接 import，不依赖 HTTP）
- features: cv2 启发式画面特征（**近似，不是 MediaPipe**）
- store:   feedback.jsonl / shot_log.csv / output/shots 落盘

注意：engine/ 一律**惰性 import**（函数内部 import），保证 algo 尚未交付时
`from server.app import app` 仍然成功。
"""

from __future__ import annotations

__version__ = "1.0.0"
