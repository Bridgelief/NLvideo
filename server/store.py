# -*- coding: utf-8 -*-
"""本机落盘：反馈 JSONL、拍摄记录 shot_log.csv、before/after 图、粗剪产物。

运行时产物目录（不存在自动创建，见 docs/INTERFACES.md §1）：
- ``output/feedback.jsonl``
- ``output/shot_log.csv``（表头 = materials.csv 的 14 列 + before_url/after_url 两列，
  引擎会忽略额外列，见 INTERFACES §2.4）
- ``output/shots/<clip_id>_before.jpg`` / ``_after.jpg``
- ``output/edit/rough_cut.json``（/api/edit 的最近结果，供导出与 tools CLI 使用）

本模块只做文件读写，不 import engine（引擎未就绪也能用）。
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

#: 仓库根目录（server/ 的上一级）
ROOT = Path(__file__).resolve().parent.parent

#: data/ 目录（domain 负责）；允许环境变量覆盖，便于隔离测试
DATA_DIR = Path(os.environ.get("VIDEOSHOOT_DATA_DIR", str(ROOT / "data")))

#: output/ 目录（运行时产物）；允许环境变量覆盖，便于隔离测试
OUTPUT_DIR = Path(os.environ.get("VIDEOSHOOT_OUTPUT_DIR", str(ROOT / "output")))

SHOT_LOG_PATH = OUTPUT_DIR / "shot_log.csv"
SHOTS_DIR = OUTPUT_DIR / "shots"
FEEDBACK_PATH = OUTPUT_DIR / "feedback.jsonl"
EDIT_DIR = OUTPUT_DIR / "edit"
EDIT_RESULT_PATH = EDIT_DIR / "rough_cut.json"

#: materials.csv / shot_log.csv 的 14 个基础列（docs/INTERFACES.md §3.5）
MATERIALS_COLUMNS: list[str] = [
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
]

#: shot_log.csv 额外两列：before/after 图的 URL（引擎忽略额外列）
SHOT_COLUMNS: list[str] = MATERIALS_COLUMNS + ["before_url", "after_url"]

_CLIP_ID_RE = re.compile(r"^S(\d+)$", re.IGNORECASE)


def ensure_dirs() -> None:
    """创建所有运行时目录（幂等）。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    EDIT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 反馈 JSONL
# ---------------------------------------------------------------------------
def append_feedback(record: dict[str, Any]) -> int:
    """追加一行 feedback.jsonl，返回累计行数。"""
    ensure_dirs()
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return count_feedback()


def count_feedback() -> int:
    if not FEEDBACK_PATH.exists():
        return 0
    try:
        with open(FEEDBACK_PATH, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:  # pragma: no cover
        return 0


# ---------------------------------------------------------------------------
# shot_log.csv
# ---------------------------------------------------------------------------
def read_shot_rows() -> list[dict[str, str]]:
    """读取 output/shot_log.csv（不存在/空文件返回 []）。"""
    if not SHOT_LOG_PATH.exists():
        return []
    try:
        with open(SHOT_LOG_PATH, "r", encoding="utf-8-sig", newline="") as fh:
            rows = [row for row in csv.DictReader(fh) if (row.get("clip_id") or "").strip()]
    except (OSError, csv.Error):  # pragma: no cover
        return []
    return rows


def count_shots() -> int:
    return len(read_shot_rows())


def next_clip_id(rows: list[dict[str, str]] | None = None) -> str:
    """从现有 shot_log.csv 的最大编号续号：S0001, S0002, ..."""
    if rows is None:
        rows = read_shot_rows()
    max_id = 0
    for row in rows:
        match = _CLIP_ID_RE.match((row.get("clip_id") or "").strip())
        if match:
            max_id = max(max_id, int(match.group(1)))
    return f"S{max_id + 1:04d}"


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def append_shot_record(row: dict[str, Any]) -> int:
    """按 SHOT_COLUMNS 追加一行 shot_log.csv，返回累计行数。

    表头缺时先写表头；行里缺失字段写空字符串。
    """
    ensure_dirs()
    need_header = (not SHOT_LOG_PATH.exists()) or SHOT_LOG_PATH.stat().st_size == 0
    with open(SHOT_LOG_PATH, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SHOT_COLUMNS, extrasaction="ignore")
        if need_header:
            writer.writeheader()
        writer.writerow({col: _clean(row.get(col, "")) for col in SHOT_COLUMNS})
    return count_shots()


def save_shot_images(
    clip_id: str,
    before_bytes: bytes | None,
    after_bytes: bytes | None,
) -> tuple[str, str]:
    """保存 before/after 图，返回 (/output/shots/..) URL（无图则空字符串）。"""
    ensure_dirs()
    urls: list[str] = []
    for suffix, raw in (("before", before_bytes), ("after", after_bytes)):
        if not raw:
            urls.append("")
            continue
        path = SHOTS_DIR / f"{clip_id}_{suffix}.jpg"
        try:
            path.write_bytes(raw)
        except OSError:  # pragma: no cover
            urls.append("")
            continue
        urls.append(f"/output/shots/{path.name}")
    return urls[0], urls[1]


def reset_shots() -> int:
    """清空 shot_log.csv 与 output/shots/ 下的图片，返回清掉的记录条数。"""
    cleared = count_shots()
    if SHOT_LOG_PATH.exists():
        try:
            SHOT_LOG_PATH.unlink()
        except OSError:  # pragma: no cover
            pass
    if SHOTS_DIR.exists():
        for path in SHOTS_DIR.iterdir():
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:  # pragma: no cover
                pass
    ensure_dirs()
    return cleared


# ---------------------------------------------------------------------------
# 粗剪产物（/api/edit 的最近结果）
# ---------------------------------------------------------------------------
def save_edit_result(payload: dict[str, Any]) -> Path:
    """把 /api/edit 结果写到 output/edit/rough_cut.json（供导出与 tools 使用）。"""
    ensure_dirs()
    try:
        EDIT_RESULT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:  # pragma: no cover
        pass
    return EDIT_RESULT_PATH


def load_edit_result() -> dict[str, Any] | None:
    if not EDIT_RESULT_PATH.exists():
        return None
    try:
        data = json.loads(EDIT_RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # pragma: no cover
        return None
    return data if isinstance(data, dict) else None
