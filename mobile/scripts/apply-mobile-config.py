#!/usr/bin/env python
"""Capacitor Android 工程一键应用「开发期后端地址 + 权限/证书」补丁。

用途：`npx cap sync android` 会用 web/ 的内容覆盖
`android/app/src/main/assets/public/`，所以每次 sync 之后跑一次本脚本，
把「APK 里指向电脑 HTTPS 服务」的运行时配置写回去。

用法（在 mobile/ 目录或任意目录）：
    python scripts/apply-mobile-config.py                # 默认 172.20.10.7:8443
    python scripts/apply-mobile-config.py 192.168.1.23:8443
    python scripts/apply-mobile-config.py https://192.168.1.23:8443

环境变量 LB_API_BASE 也可提供地址；命令行参数优先。
脚本幂等：重复执行结果一致。
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(HERE)
ASSETS = os.path.join(MOBILE, "android", "app", "src", "main", "assets", "public")
APP_CONFIG = os.path.join(ASSETS, "app-config.js")
INDEX_HTML = os.path.join(ASSETS, "index.html")
CA_SRC = os.path.join(MOBILE, "..", "certs", "ca.pem")
CA_DST = os.path.join(MOBILE, "android", "app", "src", "main", "res", "raw", "lb_dev_ca.pem")

DEFAULT_HOST = "172.20.10.7:8443"

TEMPLATE = """/* Capacitor 构建时注入：指向电脑上运行的 HTTPS 后端。
 *
 * 电脑先跑： powershell -ExecutionPolicy Bypass -File scripts\\start-https.ps1
 * 手机与电脑必须连同一个 Wi-Fi；换电脑 / 换 IP 后，改这里的地址并重新
 * npx cap sync android + gradlew.bat assembleDebug 即可。
 *
 * 本文件位于 mobile/android/app/src/main/assets/public/，是 cap sync 从
 * web/app-config.js 复制来的；每次 sync 后会被覆盖，请用本脚本重新写回。
 */
window.__LB_API_BASE__ = "{base}";
"""


def normalize_base(raw: str) -> str:
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        raw = DEFAULT_HOST
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return "https://" + raw


def main() -> int:
    raw = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LB_API_BASE", "")
    base = normalize_base(raw)

    if not os.path.isdir(ASSETS):
        print(f"[error] 找不到 {ASSETS}；先在 mobile/ 里执行 npx cap add android + npx cap sync android")
        return 1

    # 1) app-config.js：写入 API 基址（网络层读 window.__LB_API_BASE__）
    with open(APP_CONFIG, "w", encoding="utf-8", newline="\n") as f:
        f.write(TEMPLATE.format(base=base))
    print(f"[ok] app-config.js -> window.__LB_API_BASE__ = \"{base}\"")

    # 2) 校验 index.html 确实在 app.js 之前加载 app-config.js
    html = open(INDEX_HTML, encoding="utf-8").read()
    cfg_pos = html.find("app-config.js")
    app_pos = html.find('src="app.js"')
    if cfg_pos < 0 or app_pos < 0 or cfg_pos > app_pos:
        print("[error] assets/public/index.html 没有在 app.js 之前加载 app-config.js，"
              "APK 将读不到注入地址（请检查 web/index.html 是否更新）")
        return 2
    print("[ok] index.html 加载顺序正确：app-config.js 在 app.js 之前")

    # 3) 证书：certs/ca.pem -> res/raw/lb_dev_ca.pem（Android raw 资源名小写下划线）
    if os.path.isfile(CA_SRC):
        os.makedirs(os.path.dirname(CA_DST), exist_ok=True)
        data = open(CA_SRC, "rb").read()
        if data != (open(CA_DST, "rb").read() if os.path.isfile(CA_DST) else b""):
            open(CA_DST, "wb").write(data)
            print(f"[ok] certs/ca.pem -> res/raw/lb_dev_ca.pem ({len(data)} bytes)")
        else:
            print("[ok] res/raw/lb_dev_ca.pem 已是最新")
    else:
        print(f"[warn] 没找到 {CA_SRC}；APK 将无法信任局域网自签证书")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
