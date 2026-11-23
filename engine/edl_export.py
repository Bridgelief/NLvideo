#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 现场拍摄指导助手 —— EDL / FFmpeg 导出（纯函数，永不抛异常）。

契约（唯一权威）：``docs/INTERFACES.md`` §2.5 / §4.9 / §4.10。

三个导出物：

* ``build_ffmpeg(timeline, ...)``：检测本机 ffmpeg（``shutil.which``），返回
  ``{available, ffmpeg_path, single_command, batch_script, notes}``。
  **机器上没装 ffmpeg 是硬约束**：检测不到时 ``available=False``，但命令文本与
  ``.bat`` 依然完整可执行（拿到装了 ffmpeg 的机器上直接跑）。
* ``build_cmx3600(timeline, ...)``：CMX 3600 格式 EDL 文本，可直接导入剪辑软件。
* ``build_edl_json(rough_cut, ...)``：``/api/edit` 响应里的 ``edl`` 字段。

实现取舍（写进 notes，不吹）：
* 本机无 ffmpeg → 只生成命令/脚本/EDL，不渲染视频；
* FFmpeg 命令默认 **硬切 concat**（trim + setpts + concat，一次转码）；转场信息保留在
  EDL 时间线的 ``transition_in`` 里，需要 xfade 的人按 notes 自行替换；
* 只处理视频轨（``a=0``），现场声需要另加音频映射。

依赖：仅 Python 标准库。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

#: 本机没有 ffmpeg 时 batch_script 也要能以 @echo off 开头
_BATCH_HEADER = "@echo off"
_FFMPEG_UNAVAILABLE_NOTE = "本机未检测到 ffmpeg，命令仅供导出/在装有 ffmpeg 的机器上执行"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _as_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _quote(text: str) -> str:
    """给命令行参数加引号并去掉内嵌引号（防注入/防空格路径）。"""
    cleaned = str(text).replace('"', "")
    return f'"{cleaned}"'


def _is_absolute_like(path: str) -> bool:
    return bool(Path(path).is_absolute()) or (len(path) >= 2 and path[1] == ":") or path.startswith("\\\\")


def _entry_segment(entry: dict) -> dict | None:
    """从时间线条目取 (文件名, 入点, 出点)；字段坏掉返回 None。"""
    filename = entry.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        return None
    filename = filename.strip()

    start = entry.get("in")
    end = entry.get("out")
    start = _as_float(start, 0.0)
    end = _as_float(end, 0.0)
    if end <= start:
        duration = _as_float(entry.get("duration_sec"), 1.0)
        end = start + (duration if duration > 0 else 1.0)
    return {"filename": filename, "in": start, "out": end}


def _normalize_timeline(timeline: Any, base_dir: str) -> list[dict]:
    """时间线 → [{path,in,out}]，跳过坏条目（不抛异常）。"""
    if not isinstance(timeline, (list, tuple)):
        return []
    items: list[dict] = []
    for entry in timeline:
        if not isinstance(entry, dict):
            continue
        segment = _entry_segment(entry)
        if segment is None:
            continue
        filename = segment["filename"]
        if _is_absolute_like(filename):
            path = filename.replace("\\", "/")
        else:
            path = str(Path(base_dir) / filename).replace("\\", "/")
        items.append({"path": path, "in": segment["in"], "out": segment["out"]})
    return items


# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------
def _ffmpeg_location() -> tuple[bool, str | None]:
    try:
        path = shutil.which("ffmpeg")
    except (OSError, ValueError):  # pragma: no cover - 极端环境
        return False, None
    return (path is not None), path


def _build_single_command(items: list[dict], output: str, fps: int) -> str:
    """trim + setpts + concat 一条命令出片（硬切）。"""
    if not items:
        return (
            f'ffmpeg -y -f lavfi -i "color=c=black:s=1280x720:d=1" '
            f"-c:v libx264 -pix_fmt yuv420p {_quote(output)}"
        )

    inputs = " ".join(f"-i {_quote(item['path'])}" for item in items)
    filters = [
        f"[{index}:v]trim=start={item['in']:.3f}:end={item['out']:.3f},setpts=PTS-STARTPTS[v{index}]"
        for index, item in enumerate(items)
    ]
    labels = "".join(f"[v{index}]" for index in range(len(items)))
    concat = f"{labels}concat=n={len(items)}:v=1:a=0[v]"
    filter_complex = ";".join(filters + [concat])
    return (
        f"ffmpeg -y {inputs} -filter_complex \"{filter_complex}\" "
        f"-map \"[v]\" -r {fps} -c:v libx264 -pix_fmt yuv420p {_quote(output)}"
    )


def _build_batch_script(single_command: str) -> str:
    """Windows .bat：必须 @echo off 开头，CRLF 行尾，可双击运行。"""
    lines = [
        _BATCH_HEADER,
        "chcp 65001 >nul",
        "rem AI 拍摄助手 · 粗剪渲染脚本（需要本机安装 ffmpeg 并加入 PATH）",
        'cd /d "%~dp0"',
        "echo [INFO] 开始渲染粗剪...",
        single_command,
        "if errorlevel 1 (",
        "  echo [ERROR] ffmpeg 执行失败，请检查是否安装 ffmpeg 并加入 PATH",
        "  exit /b 1",
        ")",
        "echo [OK] 已输出视频，见命令里的输出路径",
        "pause",
        "",
    ]
    return "\r\n".join(lines)


def build_ffmpeg(
    timeline: Any,
    *,
    base_dir: str = "inputs",
    output: str = "output/rough_cut.mp4",
    fps: int = 30,
) -> dict:
    """生成单条 ffmpeg 命令 + Windows .bat + 说明。

    返回契约 §2.5 结构；任何异常都在内部消化，``available`` 只由 ``shutil.which("ffmpeg")`` 决定。
    """
    notes: list[str] = []
    safe_fps = _as_int(fps, 30)
    safe_base = str(base_dir or "inputs")
    safe_output = str(output or "output/rough_cut.mp4")

    available, ffmpeg_path = _ffmpeg_location()
    if not available:
        notes.append(_FFMPEG_UNAVAILABLE_NOTE)
    else:
        notes.append(f"检测到 ffmpeg: {ffmpeg_path}（可清空 output/ 后直接运行 .bat）")

    try:
        items = _normalize_timeline(timeline, safe_base)
        if not items:
            notes.append("时间线为空，命令将生成 1 秒黑场占位视频")
        single_command = _build_single_command(items, safe_output, safe_fps)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        notes.append(f"时间线数据异常，已回退为占位命令: {exc!r}")
        items = []
        single_command = (
            f'ffmpeg -y -f lavfi -i "color=c=black:s=1280x720:d=1" '
            f"-c:v libx264 -pix_fmt yuv420p {_quote(safe_output)}"
        )

    try:
        batch_script = _build_batch_script(single_command)
    except Exception as exc:  # pragma: no cover
        notes.append(f"批处理脚本生成异常: {exc!r}")
        batch_script = f"{_BATCH_HEADER}\r\nrem 生成失败\r\n"

    notes.append(
        "命令用 trim+setpts+concat 一次转码、默认硬切；"
        "时间线里的 transition_in（叠化/淡入淡出）需用 xfade 滤镜手动替换"
    )
    notes.append("命令只处理视频轨（a=0）；如需现场声，追加音频输入并用 amerge/acrossfade 映射")
    notes.append(f"共 {len(items)} 段；素材路径按 base_dir={safe_base!r} 拼接，输出到 {safe_output}")

    return {
        "available": available,
        "ffmpeg_path": ffmpeg_path,
        "single_command": single_command,
        "batch_script": batch_script,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# CMX 3600 EDL
# ---------------------------------------------------------------------------
def _timecode(seconds: float, fps: int) -> str:
    """秒 → 非丢帧时间码 HH:MM:SS:FF（越界夹到 0）。"""
    safe_fps = fps if fps > 0 else 30
    total_frames = int(round(max(0.0, seconds) * safe_fps))
    frames = total_frames % safe_fps
    total_seconds = total_frames // safe_fps
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    mins = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{mins:02d}:{secs:02d}:{frames:02d}"


def build_cmx3600(timeline: Any, *, fps: int = 30) -> str:
    """生成 CMX 3600 格式 EDL 文本（可导入 Premiere/达芬奇等剪辑软件）。"""
    safe_fps = _as_int(fps, 30)
    lines: list[str] = ["TITLE: AI_SHOOT_ROUGH_CUT", "FCM: NON-DROP FRAME", ""]
    try:
        cursor = 0.0
        event = 0
        for entry in timeline if isinstance(timeline, (list, tuple)) else []:
            if not isinstance(entry, dict):
                continue
            segment = _entry_segment(entry)
            if segment is None:
                continue
            event += 1
            duration = segment["out"] - segment["in"]
            src_in = _timecode(segment["in"], safe_fps)
            src_out = _timecode(segment["out"], safe_fps)
            rec_in = _timecode(cursor, safe_fps)
            cursor += duration
            rec_out = _timecode(cursor, safe_fps)
            lines.append(
                f"{event:03d}  AX       V     C        {src_in} {src_out} {rec_in} {rec_out}"
            )
            lines.append(f"* FROM CLIP NAME: {segment['filename']}")
            lines.append("")
    except Exception:  # pragma: no cover - 防御性兜底
        pass
    return "\r\n".join(lines)


# ---------------------------------------------------------------------------
# EDL JSON
# ---------------------------------------------------------------------------
def build_edl_json(rough_cut: Any, *, source: str = "materials.csv") -> dict:
    """把 ``rough_cut`` 转成 ``/api/edit`` 响应里的 ``edl`` 字段（契约 §4.9）。"""
    try:
        timeline = rough_cut.get("timeline") if isinstance(rough_cut, dict) else None
        if not isinstance(timeline, (list, tuple)):
            timeline = []
        copied = [dict(entry) for entry in timeline if isinstance(entry, dict)]
    except Exception:  # pragma: no cover
        copied = []
    return {
        "version": 1,
        "source": str(source or "materials.csv"),
        "timeline": copied,
    }


if __name__ == "__main__":  # pragma: no cover - 手工自检
    # 演示：读 data/（或传入 --data-dir）生成整理结果 + EDL + FFmpeg 导出
    from engine.edit_engine import run as run_edit

    data_dir = sys.argv[sys.argv.index("--data-dir") + 1] if "--data-dir" in sys.argv else None
    edit_result = run_edit(data_dir=data_dir)
    export = {
        "organization": edit_result["organization"],
        "rough_cut": edit_result["rough_cut"],
        "source": edit_result["source"],
        "warnings": edit_result["warnings"],
        "edl": build_edl_json(edit_result["rough_cut"], source=edit_result["source"]),
        "ffmpeg": build_ffmpeg(edit_result["rough_cut"]["timeline"]),
        "cmx3600": build_cmx3600(edit_result["rough_cut"]["timeline"]),
    }
    print(json.dumps(export, ensure_ascii=False, indent=2))
