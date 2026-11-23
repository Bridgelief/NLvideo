#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验收脚本：起真实 uvicorn（默认 127.0.0.1:8011），按方案 §10 的 7 条验收标准逐条检查，然后停进程。

用法（仓库根目录）：
    python tests/acceptance_live.py
    python tests/acceptance_live.py --port 8012

退出码：0 = 全部 PASS（SKIP 不算失败）；1 = 有 FAIL 或服务起不来。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS: list[tuple[str, str, str]] = []  # (状态, 名称, 证据)


def record(status: str, name: str, detail: str) -> None:
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name} —— {detail}", flush=True)


def check(name: str, func) -> None:
    try:
        detail = func()
        record("PASS", name, detail or "ok")
    except AssertionError as exc:
        record("FAIL", name, str(exc))
    except Exception as exc:  # noqa: BLE001 - 验收脚本要报告所有异常
        record("FAIL", name, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


def wait_health(base: str, proc: subprocess.Popen, timeout: float = 60.0) -> dict:
    import requests

    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn 提前退出，returncode={proc.returncode}")
        try:
            r = requests.get(f"{base}/api/health", timeout=2)
            if r.status_code == 200:
                return r.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.4)
    raise RuntimeError(f"服务在 {timeout}s 内没起来（最后的错误：{last_error}）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    port = args.port
    base = f"http://127.0.0.1:{port}"

    import requests

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log_path = ROOT / "output" / "acceptance_server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")

    print(f"启动服务：python -m uvicorn server.app:app --port {port}（日志 {log_path}）", flush=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def drain() -> None:
        try:
            log_file.flush()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=drain, daemon=True).start()
    state: dict = {}

    try:
        health = wait_health(base, proc)
        print(f"服务已就绪：{json.dumps(health, ensure_ascii=False)}", flush=True)
        state["frame_bytes"] = None

        # 0. 健康检查
        def step_health():
            assert health.get("ok") is True, health
            assert health["counts"]["phrases"] == 50, health["counts"]
            return f"phrases={health['counts']['phrases']} rules={health['counts']['rules']} materials={health['counts']['materials']} ffmpeg={health['ffmpeg']}"

        check("0. /api/health 正常且数据齐备", step_health)

        # 1. 验收 #1：打开后只有一个主按钮 + 一个输入框 + 建议区
        def step_frontend():
            html = requests.get(f"{base}/", timeout=10).text
            ids = ["video", "btn-suggest", "intent-input", "suggestion-list", "panel-cut"]
            missing = [i for i in ids if f'id="{i}"' not in html]
            assert not missing, f"缺元素 {missing}"
            assert html.count('data-primary="true"') == 1, "主按钮不唯一"
            return f"index.html {len(html)} 字节，元素齐备，data-primary 唯一"

        check("1. 单按钮 + 输入框 + 建议区（验收 #1）", step_frontend)

        # 2. demo 场景
        def step_demo():
            data = requests.get(f"{base}/api/demo", timeout=10).json()
            scenarios = data["scenarios"]
            assert len(scenarios) >= 4, f"只有 {len(scenarios)} 个 demo 场景"
            for sc in scenarios:
                img = requests.get(f"{base}{sc['frame_url']}", timeout=10)
                assert img.status_code == 200 and len(img.content) > 1000, sc["frame_url"]
            state["scenario"] = scenarios[0]
            state["frame_bytes"] = requests.get(f"{base}{scenarios[0]['frame_url']}", timeout=10).content
            return f"{len(scenarios)} 个场景，帧均可下载"

        check("2. 无相机时的 demo 场景（≥4 个）", step_demo)

        # 3. 验收 #2/#3：抓真实帧 → 5 秒内出建议，且建议是动作+理由
        def step_suggest():
            import base64

            frame_b64 = "data:image/jpeg;base64," + base64.b64encode(state["frame_bytes"]).decode("ascii")
            t0 = time.perf_counter()
            r = requests.post(
                f"{base}/api/suggest",
                json={
                    "session_id": "acceptance",
                    "intent": state["scenario"]["intent"],
                    "frame_base64": frame_b64,
                },
                timeout=15,
            )
            elapsed = time.perf_counter() - t0
            assert r.status_code == 200, r.text[:300]
            data = r.json()
            sugs = data["suggestions"]
            assert 1 <= len(sugs) <= 3, f"建议条数 {len(sugs)}"
            for sug in sugs:
                assert sug["action"].strip() and sug["reason"].strip()
                assert "权重" not in sug["reason"]
            assert elapsed < 5.0, f"{elapsed:.2f}s 超过 5 秒"
            state["last_suggest"] = data
            return f"{elapsed * 1000:.0f}ms 出 {len(sugs)} 条建议：{sugs[0]['action']}｜{sugs[0]['reason'][:24]}…"

        check("3. 按按钮 5 秒内出动作+理由（验收 #2/#3）", step_suggest)

        # 4. 验收 #4：调整前后对比能力（前端实现检查）
        def step_compare():
            js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
            for token in ("调整前", "调整后", "toDataURL", "download"):
                assert token in js, f"app.js 缺 {token}"
            return "app.js 含 before/after 合成与下载逻辑；视觉观感需人工确认"

        check("4. 调整前后对比图（验收 #4，代码级）", step_compare)

        # 5. 验收 #6/#7：记录 5 条素材 → 自动整理 + 粗剪
        posted = []

        def step_shots():
            requests.post(f"{base}/api/shots/reset", timeout=10)
            last = state["last_suggest"]
            frame_b64 = state["frame_bytes"]
            import base64

            b64 = "data:image/jpeg;base64," + base64.b64encode(frame_b64).decode("ascii")
            for i in range(5):
                payload = {
                    "session_id": "acceptance",
                    "intent": state["scenario"]["intent"],
                    "intent_id": last["intent_detected"],
                    "quality_score": round(last["current_score"]["total"] + i * 0.02, 3),
                    "followed_advice": i >= 2,
                    "advice_action_ids": [s["action_id"] for s in last["suggestions"]],
                    "duration_sec": 6.0,
                    "frame_features": last["frame_features"],
                    "before_image": b64,
                    "after_image": b64,
                    "stability": 0.9,
                    "focus_ok": 1,
                    "has_mistake": 0,
                }
                r = requests.post(f"{base}/api/shots", json=payload, timeout=20)
                assert r.status_code == 200, r.text[:300]
                posted.append(r.json())
                time.sleep(0.05)
            listed = requests.get(f"{base}/api/shots", timeout=10).json()
            assert listed["count"] == 5, f"只记录到 {listed['count']} 条"
            for clip in listed["clips"][:1]:
                assert requests.get(f"{base}{clip['before_url']}", timeout=10).status_code == 200
                assert requests.get(f"{base}{clip['after_url']}", timeout=10).status_code == 200
            return f"记录 {len(posted)} 条（{posted[0]['clip_id']}…{posted[-1]['clip_id']}），前后图可访问"

        check("5. 记录 5 条拍摄素材 + 前后图落盘（验收 #6 前半）", step_shots)

        def step_cut():
            r = requests.post(f"{base}/api/edit", json={"source": "shots"}, timeout=30)
            assert r.status_code == 200, r.text[:400]
            data = r.json()
            timeline = data["rough_cut"]["timeline"]
            assert len(timeline) >= 3, f"粗剪只有 {len(timeline)} 条"
            assert data["ffmpeg"]["available"] is False, "本机应检测不到 ffmpeg"
            assert data["ffmpeg"]["single_command"].strip(), "没有 ffmpeg 也必须给命令文本"
            quality_of = {p["row"]["clip_id"]: p["row"]["quality_score"] for p in posted}
            followed = [p["row"]["clip_id"] for p in posted if p["row"]["followed_advice"] == 1]
            cut = {item["clip_id"]: item for item in timeline}
            prioritized = [cid for cid in followed if cid in cut and cut[cid]["priority"] > quality_of[cid] + 0.05]
            assert prioritized, "按建议调整过的素材没有体现优先级加分"
            assert any("建议" in cut[cid]["reason"] for cid in prioritized), "保留理由里看不到按建议调整"
            return (
                f"{len(timeline)} 条入剪 / 总时长 {data['rough_cut']['total_duration_sec']}s；"
                f"followed 优先 {len(prioritized)} 条；每条带理由"
            )

        check("6. 自动整理 + 粗剪时间线（验收 #6 后半）", step_cut)

        def step_priority_reason():
            data = requests.post(f"{base}/api/edit", json={"source": "shots"}, timeout=30).json()
            reasons = [item["reason"] for item in data["rough_cut"]["timeline"]]
            assert any("建议" in r for r in reasons), "时间线没有一条理由提到按建议调整"
            return f"示例理由：{next(r for r in reasons if '建议' in r)[:40]}…"

        check("7. 按建议调整过优先保留且理由可见（验收 #7）", step_priority_reason)

        # 8. 素材库粗剪 + 导出
        def step_materials_cut():
            data = requests.post(f"{base}/api/edit", json={"source": "materials"}, timeout=30).json()
            org = data["organization"]
            assert org["total"] >= 13, org["total"]
            assert org["excluded_count"] >= 1, "示例素材应至少有一条废片被剔除"
            assert data["rough_cut"]["clip_count"] >= 3
            return (
                f"总 {org['total']} 条 / 可用 {org['usable']} / 剔除 {org['excluded_count']} / "
                f"重复 {org['duplicate_count']}；粗剪 {data['rough_cut']['clip_count']} 条"
            )

        check("8. 示例素材库整理 + 粗剪（素材清单驱动）", step_materials_cut)

        def step_exports():
            js = requests.get(f"{base}/api/edit/export/rough_cut.json", timeout=20)
            assert js.status_code == 200 and "attachment" in js.headers.get("content-disposition", ""), js.status_code
            payload = js.json()
            assert "rough_cut" in payload and payload["edl"]["timeline"], "EDL JSON 不完整"
            bat = requests.get(f"{base}/api/edit/export/rough_cut.bat", timeout=20)
            assert bat.status_code == 200 and bat.text.startswith("@echo off"), bat.text[:80]
            assert "ffmpeg" in bat.text
            return f"JSON {len(js.content)}B + BATCH {len(bat.content)}B；本机无 ffmpeg，仅导出命令"

        check("9. EDL JSON / FFmpeg .bat 可下载", step_exports)

        # 10. 收尾
        def step_cleanup():
            r = requests.post(f"{base}/api/shots/reset", timeout=10)
            assert r.status_code == 200
            assert requests.get(f"{base}/api/shots", timeout=10).json()["count"] == 0
            return "拍摄记录已清空，可重复演示"

        check("10. 一键清空拍摄记录（可重复演示）", step_cleanup)

    except Exception as exc:  # noqa: BLE001
        record("FAIL", "环境准备/服务启动", f"{type(exc).__name__}: {exc}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()

    # ---- 汇总 ----
    print("\n" + "=" * 78)
    passed = sum(1 for status, _, _ in RESULTS if status == "PASS")
    failed = sum(1 for status, _, _ in RESULTS if status == "FAIL")
    print(f"结果：PASS {passed} / FAIL {failed} / 共 {len(RESULTS)} 项")
    print("\n方案 §10 验收对照：")
    print("  #1 一个按钮+输入框+建议区   → 步骤 1")
    print("  #2 5 秒内出建议             → 步骤 3")
    print("  #3 动作+理由、不显示分数     → 步骤 3（前端默认隐藏见 test_frontend_assets）")
    print("  #4 调整前后对比图           → 步骤 4（代码级，视觉需人工）")
    print("  #5 5 人试用 ≥3 人说有用      → 需真人试用，见 docs/DEMO_SCRIPT.md")
    print("  #6 导入素材→自动粗剪        → 步骤 5+6+8")
    print("  #7 按建议调整过优先保留+理由  → 步骤 7")
    if failed:
        print("\n失败项：")
        for status, name, detail in RESULTS:
            if status == "FAIL":
                print(f"  - {name}: {detail}")
    print("=" * 78)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    sys.exit(main())
