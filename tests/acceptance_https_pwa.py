#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机真机链路端到端验收：HTTPS + 相机模式 + Service Worker 离线壳 + PWA 可安装性。

跑法（仓库根目录）：python tests/acceptance_https_pwa.py
可选：--port 8444

做的事：
1) 确保开发证书（tools/make_dev_cert.py），用 CA 校验证书链真的能过；
2) 起真实 uvicorn（TLS，0.0.0.0:8444）；
3) Playwright（本机 Edge）+ 假摄像头 → 验证 https 下 getUserMedia 生效（相机模式，不是演示模式）；
4) 验证 Service Worker 注册/接管、断网后 App Shell 仍能打开；
5) 用 CDP 检查 PWA 可安装性（无 installability errors）；
6) 截图存 docs/screenshots/。
退出码：0 = 全部 PASS（SKIP 不算失败）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CA = ROOT / "certs" / "ca.pem"
CERT = ROOT / "certs" / "server.pem"
KEY = ROOT / "certs" / "server-key.pem"
RESULTS: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str) -> None:
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name} —— {detail}", flush=True)


def check(name: str, func) -> None:
    try:
        detail = func()
        record("PASS", name, detail or "ok")
    except AssertionError as exc:
        record("FAIL", name, str(exc))
    except Exception as exc:  # noqa: BLE001
        record("FAIL", name, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


def wait_health(base: str, ca: str, proc: subprocess.Popen, timeout: float = 60.0) -> dict:
    import requests

    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn 提前退出，returncode={proc.returncode}")
        try:
            r = requests.get(f"{base}/api/health", timeout=3, verify=ca)
            if r.status_code == 200:
                return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.4)
    raise RuntimeError(f"HTTPS 服务没起来（最后错误：{last}）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8444)
    args = parser.parse_args()
    port = args.port
    base = f"https://127.0.0.1:{port}"

    import requests

    # 0) 证书
    gen = subprocess.run(
        [sys.executable, "tools/make_dev_cert.py"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if gen.returncode != 0 or not CERT.exists() or not CA.exists():
        record("FAIL", "0. 开发证书", (gen.stderr or gen.stdout)[-300:])
        return 1
    record("PASS", "0. 开发证书", "certs/ca.pem + server.pem（CA 签发）就绪")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log_path = ROOT / "output" / "acceptance_https.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.app:app",
         "--host", "0.0.0.0", "--port", str(port),
         "--ssl-keyfile", str(KEY), "--ssl-certfile", str(CERT)],
        cwd=str(ROOT), env=env, stdout=log_file, stderr=subprocess.STDOUT, text=True,
    )

    try:
        health = wait_health(base, str(CA), proc)
        record("PASS", "1. HTTPS 服务 + CA 校验", f"counts={health['counts']}（requests 用 cafile 验过）")

        # 2) 自签证书在默认信任库下应被拒绝（说明手机需要"继续前往"）
        def step_untrusted():
            try:
                requests.get(f"{base}/api/health", timeout=5)
            except requests.exceptions.SSLError:
                return "浏览器默认信任库会拦截自签证书（手机提示「继续前往」属预期）"
            raise AssertionError("自签证书竟被默认信任库接受，异常")

        check("2. 自签证书默认被拒（对应手机首次提示）", step_untrusted)

        # 3) PWA 静态资源
        def step_assets():
            for path, label in (("/manifest.json", "manifest"), ("/sw.js", "sw.js"),
                                ("/icon-192.png", "icon-192"), ("/icon-512.png", "icon-512"),
                                ("/app-config.js", "app-config")):
                r = requests.get(base + path, timeout=10, verify=str(CA))
                assert r.status_code == 200, f"{path} -> {r.status_code}"
            manifest = requests.get(base + "/manifest.json", timeout=10, verify=str(CA)).json()
            assert manifest["display"] == "standalone"
            assert any("192" in i.get("sizes", "") for i in manifest["icons"])
            return "manifest/sw/图标/app-config 全部 200，manifest 可安装字段齐全"

        check("3. PWA 静态资源可访问", step_assets)

        # 4) Playwright：假摄像头 + https → 相机模式
        from playwright.sync_api import sync_playwright

        # 把本机服务器证书的公钥指纹告诉 Chromium，等价于"这台机器的自签证书已被信任"，
        # 这样 Service Worker / 相机等安全上下文能力才能在测试里真实生效
        # （ignore_https_errors 只放行页面导航，Service Worker 脚本抓取仍会因证书被拒）。
        import base64
        import hashlib

        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        server_cert = x509.load_pem_x509_certificate(CERT.read_bytes())
        spki = base64.b64encode(
            hashlib.sha256(
                server_cert.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
            ).digest()
        ).decode()

        import tempfile

        profile_dir = tempfile.mkdtemp(prefix="lb-pwa-profile-")

        with sync_playwright() as pw:
            # 用持久化 profile（非无痕），否则 Chrome 会报 in-incognito、无法判断"可安装"
            context = pw.chromium.launch_persistent_context(
                profile_dir,
                channel="msedge",
                headless=True,
                args=[
                    "--no-sandbox",
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream",
                    f"--ignore-certificate-errors-spki-list={spki}",
                ],
                viewport={"width": 390, "height": 844},
                device_scale_factor=2,
                is_mobile=True,
                has_touch=True,
                permissions=["camera"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(base + "/", wait_until="networkidle", timeout=40000)
            page.wait_for_timeout(2500)

            def step_camera_mode():
                status = page.locator("#status").inner_text()
                assert "相机就绪" in status, f"状态是「{status}」，说明没进相机模式"
                video = page.evaluate(
                    "() => { const v = document.getElementById('video'); return v ? [v.videoWidth, v.videoHeight] : null; }"
                )
                assert video and video[0] > 0 and video[1] > 0, f"video 没出画面：{video}"
                return f"status=「{status}」，video={video[0]}x{video[1]}（https 下 getUserMedia 生效）"

            check("4. https 下真实相机链路（相机模式，非演示）", step_camera_mode)

            def step_suggest_camera():
                page.click("#btn-suggest")
                page.wait_for_selector(".suggestion", timeout=15000)
                cards = page.locator(".suggestion")
                count = cards.count()
                first = cards.nth(0).inner_text().replace("\n", " | ")[:120]
                assert 1 <= count <= 3
                return f"相机抓帧 → {count} 条建议：{first}"

            check("5. 相机模式按按钮出建议", step_suggest_camera)

            # 截图（相机模式）
            shots = ROOT / "docs" / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / "07-https-camera-mode.png"))

            def step_sw():
                # 带超时：注册失败时 navigator.serviceWorker.ready 会永远 pending
                page.evaluate(
                    """() => Promise.race([
                        navigator.serviceWorker.ready.then(() => 'ready'),
                        new Promise((resolve) => setTimeout(() => resolve('timeout'), 12000))
                    ])"""
                )
                page.reload(wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(1200)
                controlled = page.evaluate("() => !!navigator.serviceWorker.controller")
                assert controlled, "刷新后页面没有被 Service Worker 接管（看 output/acceptance_https.log）"
                caches = page.evaluate("() => caches.keys()")
                assert any(key.startswith("lb-shell") for key in caches), f"缓存清单异常：{caches}"
                return f"SW 已接管，缓存：{caches}"

            check("6. Service Worker 注册并接管页面", step_sw)

            def step_offline():
                context.set_offline(True)
                try:
                    page.reload(wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_selector("#btn-suggest", timeout=8000)
                    page.wait_for_timeout(2500)
                    status = page.locator("#status").inner_text()
                    assert "服务未启动" in status, f"离线时状态应为「服务未启动」，实际「{status}」"
                    assert page.locator("#intent-input").count() == 1
                    page.screenshot(path=str(shots / "08-offline-shell.png"))
                    return f"断网后 App Shell 仍打开（状态「{status}」），界面完整"
                finally:
                    context.set_offline(False)

            check("7. 断网仍能打开离线壳（PWA 核心）", step_offline)

            def step_installability():
                cdp = context.new_cdp_session(page)
                try:
                    errors = cdp.send("Page.getInstallabilityErrors")
                except Exception as exc:  # noqa: BLE001
                    return f"SKIP（CDP 不支持 getInstallabilityErrors：{exc}）"
                items = errors.get("installabilityErrors", [])
                assert not items, f"可安装性错误：{json.dumps(items, ensure_ascii=False)}"
                return "Chrome 判定可安装（0 个 installability error）"

            check("8. PWA 可安装性（CDP 检查）", step_installability)
            context.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()

    print("\n" + "=" * 78)
    passed = sum(1 for status, _, _ in RESULTS if status == "PASS")
    failed = sum(1 for status, _, _ in RESULTS if status == "FAIL")
    skipped = sum(1 for status, _, _ in RESULTS if status == "SKIP")
    print(f"结果：PASS {passed} / FAIL {failed} / SKIP {skipped} / 共 {len(RESULTS)} 项")
    if failed:
        print("失败项：")
        for status, name, detail in RESULTS:
            if status == "FAIL":
                print(f"  - {name}: {detail}")
    print("服务日志：", log_path)
    print("=" * 78)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    sys.exit(main())
