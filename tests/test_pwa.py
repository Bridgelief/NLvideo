# -*- coding: utf-8 -*-
"""PWA 契约测试：manifest / Service Worker / 安装入口 / HTTPS 启动脚本。

跑法（仓库根目录）：python -m unittest tests.test_pwa -v
"""

import json
import re
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} 不是 PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


class TestManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(read(WEB / "manifest.json"))

    def test_required_fields(self):
        for key in ("name", "short_name", "start_url", "display", "theme_color",
                    "background_color", "orientation", "icons"):
            self.assertIn(key, self.manifest, msg=f"manifest 缺 {key}")
        self.assertEqual(self.manifest["display"], "standalone")
        self.assertEqual(self.manifest["orientation"], "portrait")
        self.assertTrue(self.manifest["name"].strip())

    def test_icons_exist_and_have_right_size(self):
        icons = self.manifest["icons"]
        by_size = {icon.get("sizes"): icon for icon in icons}
        self.assertIn("192x192", by_size, "Chrome 安装至少需要 192x192 图标")
        self.assertIn("512x512", by_size, "PWA 需要 512x512 图标")
        for size_key in ("192x192", "512x512"):
            icon = by_size[size_key]
            self.assertEqual(icon["type"], "image/png")
            path = WEB / Path(icon["src"]).name
            self.assertTrue(path.exists(), f"图标文件不存在：{path}")
            expected = tuple(int(part) for part in size_key.split("x"))
            self.assertEqual(png_size(path), expected, msg=f"{path.name} 尺寸不符")
        maskable = [icon for icon in icons if "maskable" in (icon.get("purpose") or "")]
        self.assertTrue(maskable, "缺少 maskable 图标（Android 自适应图标会裁圆角）")

    def test_start_url_and_scope_relative(self):
        self.assertTrue(self.manifest["start_url"].startswith("./"))
        self.assertEqual(self.manifest["scope"], "./")


class TestServiceWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sw = read(WEB / "sw.js")

    def test_lifecycle_handlers(self):
        for token in ("'install'", "'activate'", "'fetch'", "skipWaiting", "clients.claim"):
            self.assertIn(token, self.sw, msg=f"sw.js 缺 {token}")

    def test_precaches_app_shell(self):
        for asset in ("/index.html", "/app.js", "/app-config.js", "/styles.css", "/manifest.json"):
            self.assertIn(asset, self.sw, msg=f"App Shell 未预缓存 {asset}")

    def test_precaches_demo_frames(self):
        self.assertIn("/static/demo/backlit_portrait.jpg", self.sw)
        self.assertIn("/api/demo", self.sw, msg="离线时应能回退缓存的 demo 场景清单")

    def test_does_not_cache_suggest_api(self):
        # /api/* 只走网络；不能把旧建议缓存给用户
        self.assertIn("url.pathname.startsWith('/api/')", self.sw)
        self.assertIn("return;", self.sw)

    def test_version_bump_cleanup(self):
        self.assertIn("VERSION", self.sw)
        self.assertIn("caches.delete", self.sw)


class TestIndexAndApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read(WEB / "index.html")
        cls.app_js = read(WEB / "app.js")

    def test_app_config_loaded_before_app_js(self):
        config_pos = self.html.find('src="app-config.js"')
        app_pos = self.html.find('src="app.js"')
        self.assertGreater(config_pos, 0, "index.html 未加载 app-config.js")
        self.assertGreater(app_pos, 0)
        self.assertLess(config_pos, app_pos, "app-config.js 必须在 app.js 之前加载")

    def test_install_button(self):
        self.assertIn('id="btn-install"', self.html, msg="缺少安装到主屏幕入口")
        self.assertIn("beforeinstallprompt", self.app_js)
        self.assertIn("appinstalled", self.app_js)
        # 安装按钮不能抢主按钮的唯一高亮
        self.assertEqual(self.html.count('data-primary="true"'), 1)

    def test_apple_touch_icon_png(self):
        match = re.search(r'rel="apple-touch-icon"[^>]*href="([^"]+)"', self.html)
        self.assertIsNotNone(match, "缺少 apple-touch-icon")
        self.assertTrue(match.group(1).endswith(".png"), "iOS 需要 PNG 图标")
        self.assertTrue((WEB / Path(match.group(1)).name).exists())

    def test_service_worker_registered(self):
        self.assertIn("serviceWorker.register", self.app_js)
        self.assertIn("'sw.js'", self.app_js)
        self.assertIn("isSecureContextNow", self.app_js)

    def test_api_base_override(self):
        """Capacitor/跨源部署：前端必须支持把 /api/* 指到别的主机。"""
        self.assertIn("__LB_API_BASE__", self.app_js)
        self.assertIn("function apiUrl", self.app_js)
        self.assertIn("apiUrl(path)", self.app_js)
        self.assertIn("lb_api_base", self.app_js)
        self.assertIn("window.__LB_API_BASE__", read(WEB / "app-config.js"))

    def test_no_alert_still(self):
        self.assertNotIn("alert(", self.app_js)


class TestHttpsScripts(unittest.TestCase):
    def test_ps1_uses_tls_and_cert_tool(self):
        text = read(ROOT / "scripts" / "start-https.ps1")
        for token in ("make_dev_cert.py", "--ssl-keyfile", "--ssl-certfile",
                      "certs\\server-key.pem", "https://"):
            self.assertIn(token, text, msg=f"start-https.ps1 缺 {token}")

    def test_bat_uses_tls(self):
        text = read(ROOT / "scripts" / "start-https.bat")
        for token in ("make_dev_cert.py", "--ssl-keyfile", "--ssl-certfile"):
            self.assertIn(token, text, msg=f"start-https.bat 缺 {token}")

    def test_cert_tool_detect_only(self):
        proc = subprocess.run(
            [sys.executable, "tools/make_dev_cert.py", "--detect-only"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("局域网 IP", proc.stdout)


class TestScriptEncoding(unittest.TestCase):
    """Windows PowerShell 5.1 读取无 BOM 的 UTF-8 脚本会把中文解析错（曾导致 start-https.ps1 直接报错）。"""

    def test_powershell_scripts_have_utf8_bom(self):
        scripts = sorted(ROOT.glob("scripts/*.ps1")) + sorted(ROOT.glob("mobile/scripts/*.ps1"))
        self.assertTrue(scripts, "没找到任何 .ps1 脚本")
        for path in scripts:
            data = path.read_bytes()
            self.assertTrue(
                data.startswith(b"\xef\xbb\xbf"),
                msg=f"{path.relative_to(ROOT)} 缺少 UTF-8 BOM（Windows PowerShell 5.1 会解析失败）",
            )

    def test_phone_guide_documents_entry_points(self):
        text = read(ROOT / "docs" / "PHONE_GUIDE.md")
        for token in ("start-https.ps1", "start.ps1", "/api/dev/ca.pem",
                      "shooting-guide-debug.apk", "build-debug.ps1"):
            self.assertIn(token, text, msg=f"PHONE_GUIDE 缺 {token}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
