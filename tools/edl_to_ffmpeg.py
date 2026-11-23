# -*- coding: utf-8 -*-
"""CLI：读粗剪 EDL JSON（或 stdin），输出 FFmpeg 命令 + .bat 脚本。

输入优先级：
  1. ``--input <path>`` 指定文件；
  2. 默认文件 ``output/edit/rough_cut.json``（POST /api/edit 会写它）；
  3. ``--stdin`` 或输入文件不存在且 stdin 有管道内容时从 stdin 读 EDL JSON。
支持的输入结构（自动向下找 timeline）：
  ``{"edl": {"timeline": [...]}}``、``{"rough_cut": {"timeline": [...]}}``、
  ``{"timeline": [...]}``、直接是 timeline 数组。

本机**没有 ffmpeg 可执行文件**：只生成命令/脚本，不渲染。engine.edl_export 就绪时
用它生成；否则用服务端兜底实现。用法：
  python tools/edl_to_ffmpeg.py
  python tools/edl_to_ffmpeg.py --input output/edit/rough_cut.json --out-dir output/edit
  type output\edit\rough_cut.json | python tools/edl_to_ffmpeg.py --stdin
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_INPUT = REPO_ROOT / "output" / "edit" / "rough_cut.json"
DEFAULT_OUT_DIR = REPO_ROOT / "output" / "edit"


def extract_timeline(data) -> list[dict]:
    """从各种可能的结构里找出 timeline 数组。"""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    timeline = data.get("timeline")
    if isinstance(timeline, list):
        return [item for item in timeline if isinstance(item, dict)]
    for key in ("edl", "rough_cut", "cut", "result"):
        inner = extract_timeline(data.get(key))
        if inner:
            return inner
    return []


def build_ffmpeg(timeline: list[dict], base_dir: str, output: str, fps: int) -> dict:
    """优先用 engine.edl_export；不可用时用服务端兜底实现。"""
    try:
        from engine import edl_export

        built = edl_export.build_ffmpeg(timeline, base_dir=base_dir, output=output, fps=fps)
        if isinstance(built, dict) and built.get("single_command"):
            return built
    except Exception as exc:
        print(f"[!] engine.edl_export 不可用（{type(exc).__name__}: {exc}），改用服务端兜底实现")
    from server.service import _fallback_ffmpeg

    return _fallback_ffmpeg(timeline, base_dir=base_dir, output=output, fps=fps)


def read_payload(input_path: Path | None, use_stdin: bool) -> dict:
    if use_stdin:
        raw = sys.stdin.read()
        if not raw.strip():
            raise SystemExit("[x] stdin 为空，没有 EDL JSON 可读")
        return json.loads(raw)
    path = input_path or DEFAULT_INPUT
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        if raw.strip():
            return json.loads(raw)
    raise SystemExit(
        f"[x] 找不到 EDL JSON：{path}\n"
        "    先运行一次 POST /api/edit（或 python engine/edit_engine.py --json > ...），"
        "或用 --input / --stdin 指定输入"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="读取粗剪 EDL JSON，输出 FFmpeg 命令与 .bat")
    parser.add_argument("--input", default=None, help="EDL/rough_cut JSON 路径（默认 output/edit/rough_cut.json）")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help=".bat 输出目录（默认 output/edit）")
    parser.add_argument("--base-dir", default="inputs", help="素材视频所在目录（写进 ffmpeg 命令，默认 inputs）")
    parser.add_argument("--output", default="output/rough_cut.mp4", help="ffmpeg 输出文件（默认 output/rough_cut.mp4）")
    parser.add_argument("--fps", type=int, default=30, help="输出帧率（默认 30）")
    parser.add_argument("--stdin", action="store_true", help="从标准输入读 EDL JSON")
    parser.add_argument("--json", action="store_true", help="打印完整 build_ffmpeg 结果（JSON）")
    args = parser.parse_args(argv)

    input_path = Path(args.input) if args.input else None
    try:
        payload = read_payload(input_path, args.stdin)
    except json.JSONDecodeError as exc:
        print(f"[x] 输入不是合法 JSON：{exc}")
        return 1
    timeline = extract_timeline(payload)
    if not timeline:
        print("[x] 输入里没有找到粗剪 timeline（空时间线无法导出）")
        return 1

    ffmpeg = build_ffmpeg(timeline, args.base_dir, args.output, args.fps)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bat_path = out_dir / "rough_cut.bat"
    script = str(ffmpeg.get("batch_script") or "")
    if not script.lstrip().lower().startswith("@echo off"):
        script = "@echo off\r\n" + script
    bat_path.write_text(script, encoding="utf-8", newline="")
    json_path = out_dir / "ffmpeg_command.json"
    json_path.write_text(json.dumps(ffmpeg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[v] 时间线 {len(timeline)} 条，总时长 {sum(float(s.get('duration_sec') or 0) for s in timeline):.2f}s")
    if shutil.which("ffmpeg"):
        print("[v] 检测到 ffmpeg，可执行导出")
    else:
        print("[i] 本机未检测到 ffmpeg：只导出命令/脚本，请在装有 ffmpeg 的机器上执行 .bat")
    print(f"[v] .bat 已写入：{bat_path}")
    print(f"[v] 命令 JSON：{json_path}")
    if args.json:
        print(json.dumps(ffmpeg, ensure_ascii=False, indent=2))
    else:
        print("\n--- single_command ---")
        print(ffmpeg.get("single_command") or "（空）")
        notes = ffmpeg.get("notes") or []
        if notes:
            print("\n--- notes ---")
            for note in notes:
                print(f"  - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
