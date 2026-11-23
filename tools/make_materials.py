# -*- coding: utf-8 -*-
"""扫描一个目录里的视频文件，生成 materials.csv 骨架（字段与 INTERFACES §3.5 一致）。

用途：真机拍摄/有素材目录时，先用本工具生成素材清单，再交给 engine/edit_engine
做质量筛选与粗剪。**duration 能读就读（cv2），读不到写 0 并在 notes 标注**；
质量分/意图等字段是骨架默认值，需要人工补充。

用法：
  python tools/make_materials.py --dir D:\videos --out data\materials.csv
  python tools/make_materials.py --dir D:\videos --out output\materials_scan.csv
  python tools/make_materials.py --dir D:\videos --out data\materials.csv --force

注意：默认**不会覆盖**已存在的 out 文件（防止误盖 domain 的 data/materials.csv），
除非显式加 ``--force``。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi"}

COLUMNS = [
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


def probe_duration(path: Path) -> float:
    """用 cv2 读视频时长（秒）；读不到返回 0.0。"""
    try:
        import cv2  # 惰性 import：只是扫描工具，失败也能给出骨架

        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            capture.release()
            return 0.0
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        capture.release()
        if fps > 0.1 and frames > 0:
            return round(frames / fps, 2)
        return 0.0
    except Exception:
        return 0.0


def scan(directory: Path) -> list[dict]:
    """递归扫描目录下的视频文件（按路径排序，结果稳定）。"""
    files = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTS
    ]
    files.sort(key=lambda p: str(p).lower())
    rows: list[dict] = []
    for index, path in enumerate(files, start=1):
        duration = probe_duration(path)
        try:
            relative = path.relative_to(directory)
        except ValueError:  # pragma: no cover
            relative = Path(path.name)
        filename = "/".join(relative.parts)  # 统一用 /，方便 EDL 跨平台
        if duration > 0.6:
            out_point = round(max(0.5, duration - 0.2), 2)
            notes = "由 make_materials.py 扫描生成；时长来自 cv2，其余字段是骨架默认值，请人工补充"
        else:
            out_point = 0.0
            notes = "由 make_materials.py 扫描生成；**读不到时长**（cv2 打开失败），请人工补充 duration_sec"
        rows.append(
            {
                "clip_id": f"C{index:03d}",
                "filename": filename,
                "duration_sec": duration,
                "scene_type": "outdoor",
                "intent_id": "portrait_medium",
                "quality_score": 0.60,
                "stability": 0.80,
                "focus_ok": 1,
                "has_mistake": 0,
                "duplicate_of": "",
                "followed_advice": 0,
                "in_point": 0.0 if duration <= 0.6 else 0.20,
                "out_point": out_point,
                "notes": notes,
            }
        )
    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in COLUMNS})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描视频目录，生成 materials.csv 骨架")
    parser.add_argument("--dir", required=True, help="要扫描的目录（递归）")
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "data" / "materials.csv"),
        help="输出 CSV 路径（默认 data/materials.csv）",
    )
    parser.add_argument("--force", action="store_true", help="覆盖已存在的输出文件")
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    if not directory.is_dir():
        print(f"[x] 目录不存在：{directory}")
        return 1
    out_path = Path(args.out)
    if out_path.exists() and not args.force:
        print(f"[x] 输出文件已存在：{out_path}")
        print("    为防止误盖现有素材清单，请换一个 --out，或加 --force 覆盖")
        return 1

    rows = scan(directory)
    if not rows:
        print(f"[!] 在 {directory} 下没找到视频文件（支持 {', '.join(sorted(VIDEO_EXTS))}）")
        print("    已生成只有表头的骨架文件，方便你手工填写")
    write_csv(rows, out_path)
    readable = sum(1 for row in rows if float(row["duration_sec"]) > 0)
    print(f"[v] 已写入 {out_path}（{len(rows)} 行；时长可读 {readable} 行）")
    for row in rows[:10]:
        print(f"  - {row['clip_id']}: {row['filename']}  {row['duration_sec']}s")
    if len(rows) > 10:
        print(f"  ... 其余 {len(rows) - 10} 行略")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
