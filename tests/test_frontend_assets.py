# -*- coding: utf-8 -*-
"""前端静态资源契约测试（契约：docs/INTERFACES.md §5）。

这里检查"不打开浏览器也能验的东西"：元素 ID、单一主按钮、无外部依赖、关键交互代码存在。
视觉观感与相机权限仍需人工验证（见 docs/ACCEPTANCE.md）。
跑法（仓库根目录）：python -m unittest tests.test_frontend_assets -v
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

REQUIRED_IDS = [
    "video",
    "overlay-canvas",
    "intent-input",
    "btn-suggest",
    "suggestion-list",
    "btn-record",
    "btn-compare",
    "btn-finish",
    "panel-cut",
    "status",
    "debug-toggle",
    "debug-info",
    "demo-scenarios",
    "cut-source",
    "dl-edl",
    "dl-bat",
]


def read(name):
    path = WEB / name
    if not path.exists():
        raise AssertionError(f"缺少前端文件：{path}")
    return path.read_text(encoding="utf-8")


class TestFrontendAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read("index.html")
        cls.js = read("app.js")
        cls.css = read("styles.css")

    def test_files_referenced(self):
        self.assertIn("app.js", self.html)
        self.assertIn("styles.css", self.html)
        self.assertIn("<html", self.html.lower())
        self.assertIn("lang=\"zh", self.html.lower())

    def test_required_ids(self):
        missing = [element_id for element_id in REQUIRED_IDS if f'id="{element_id}"' not in self.html]
        self.assertEqual(missing, [], msg=f"index.html 缺元素 ID：{missing}")

    def test_single_primary_button(self):
        """验收 #1：画面上只有一个主按钮。"""
        count = len(re.findall(r'data-primary="true"', self.html))
        self.assertEqual(count, 1, msg=f"data-primary=\"true\" 出现 {count} 次，必须恰好 1 次")
        pattern = re.compile(r'<[^>]*id="btn-suggest"[^>]*data-primary="true"[^>]*>', re.S)
        self.assertRegex(self.html, pattern, msg="#btn-suggest 必须是那个唯一主按钮")

    def test_intent_input_and_suggestions_area(self):
        self.assertIn('id="intent-input"', self.html)
        self.assertIn('id="suggestion-list"', self.html)

    def test_cut_source_options(self):
        select = re.search(r'<[^>]*id="cut-source"[^>]*>(.*?)</select>', self.html, re.S)
        self.assertIsNotNone(select, msg="找不到 #cut-source")
        values = re.findall(r'<option[^>]*value="([^"]+)"', select.group(1))
        self.assertEqual(sorted(values), ["materials", "shots"], msg=f"cut-source 选项应恰好是 materials/shots，实际 {values}")

    def test_no_external_resources(self):
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', self.html)
        self.assertEqual(external, [], msg=f"index.html 不应依赖外部资源：{external}")
        self.assertNotIn("cdn.", self.html.lower())

    def test_no_alert(self):
        self.assertNotIn("alert(", self.js, msg="全局禁用 alert()，用 toast")

    def test_api_calls_present(self):
        for endpoint in ("/api/health", "/api/demo", "/api/suggest", "/api/shots", "/api/edit"):
            self.assertIn(endpoint, self.js + self.html, msg=f"前端未调用 {endpoint}")

    def test_overlay_and_compare_implemented(self):
        self.assertIn("getContext", self.js, msg="缺少 canvas 绘制")
        self.assertIn("requestAnimationFrame", self.js, msg="叠加层需要跟随视频重绘")
        self.assertIn("overlay-canvas", self.js + self.html)
        self.assertIn("调整前", self.js + self.html)
        self.assertIn("调整后", self.js + self.html)
        self.assertIn("toDataURL", self.js, msg="抓帧需要 canvas.toDataURL")
        self.assertIn("download", (self.js + self.html).lower(), msg="对比图/EDL 需要下载")

    def test_camera_fallback(self):
        self.assertIn("getUserMedia", self.js)
        self.assertIn("demo", self.js.lower(), msg="相机不可用时要走 demo 场景兜底")

    def test_debug_hidden_by_default(self):
        # 默认收起调试信息（验收 #3：界面不出现分数/坐标）
        selector = re.search(r"#debug-info[^{]*\{([^}]*)\}", self.css)
        self.assertIsNotNone(selector, msg="styles.css 缺少 #debug-info 默认样式")
        css_body = selector.group(1).replace(" ", "").lower()
        css_hidden = "display:none" in css_body or "visibility:hidden" in css_body
        html_hidden = bool(re.search(r'id="debug-info"[^>]*hidden', self.html))
        self.assertTrue(css_hidden or html_hidden, msg="#debug-info 默认必须隐藏（display:none 或 hidden 属性）")

    def test_mobile_media_query(self):
        self.assertIn("@media", self.css)


if __name__ == "__main__":
    unittest.main(verbosity=2)
