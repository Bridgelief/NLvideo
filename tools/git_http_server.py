#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""局域网 Git 智能 HTTP 共享服务（支持 clone / fetch / push）。

为什么不用 `git daemon`：在本机 Windows 上实测 git:// 的 receive-pack（push）会卡住，
而 HTTP smart 协议（git http-backend）在同样环境可用；另外 HTTP 端口更容易过防火墙。

用法（仓库根目录）：
  python tools/git_http_server.py --repo D:\\NLvideo.git --port 8088
然后在同一 Wi-Fi 的电脑上：
  git clone http://<本机IP>:8088/NLvideo.git NLvideo
  git push / git pull 同地址

安全：默认没有账号密码，同一局域网可读写；只适合可信网络（自己家/宿舍/手机热点）。
需要临时口令时：--token 123456，伙伴用 http://<IP>:8088/NLvideo.git 时浏览器会要求
用户名/密码，密码填 123456 即可（用户名随意）。只做最简防护，不是完整鉴权系统。
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKEND = REPO_ROOT / "_tools" / "git-full" / "mingw64" / "libexec" / "git-core" / "git-http-backend.exe"
DEFAULT_GIT = REPO_ROOT / "_tools" / "git-full" / "cmd" / "git.exe"


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True


def find_backend(explicit: str | None) -> str:
    if explicit:
        path = Path(explicit)
        if path.exists():
            return str(path)
        raise SystemExit(f"找不到 git-http-backend：{explicit}")
    if DEFAULT_BACKEND.exists():
        return str(DEFAULT_BACKEND)
    # PATH 里的 git 安装
    git = subprocess.run(["where", "git"], capture_output=True, text=True).stdout.strip().splitlines()
    if git:
        root = Path(git[0]).resolve().parent.parent
        candidate = root / "mingw64" / "libexec" / "git-core" / "git-http-backend.exe"
        if candidate.exists():
            return str(candidate)
    raise SystemExit(
        "找不到 git-http-backend.exe：请用 --backend 指定，"
        "或把 PortableGit 放在 _tools/git-full/"
    )


def find_git(explicit: str | None) -> str:
    if explicit:
        return explicit
    if DEFAULT_GIT.exists():
        return str(DEFAULT_GIT)
    return "git"


def _read_chunked(stream) -> bytes:
    """读 HTTP/1.1 chunked 请求体（新版本 git 推送大包时用 chunked）。"""
    chunks: list[bytes] = []
    while True:
        line = stream.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            size = int(line.split(b";", 1)[0], 16)
        except ValueError:
            break
        if size == 0:
            stream.readline()
            break
        chunks.append(stream.read(size))
        stream.read(2)  # 吃尾部 CRLF
    return b"".join(chunks)


def make_app(project_root: Path, backend: str, token: str | None):
    backend_env_base = {
        "GIT_PROJECT_ROOT": str(project_root),
        "GIT_HTTP_EXPORT_ALL": "1",
        "GATEWAY_INTERFACE": "CGI/1.1",
        "SERVER_SOFTWARE": "NLvideo-git-http/1.0",
        "SERVER_PROTOCOL": "HTTP/1.1",
    }

    def app(environ, start_response):
        path_info = unquote(environ.get("PATH_INFO", ""))
        # 只服务 <name>.git 下的路径，避免把 project_root 里其它目录暴露出去
        first = path_info.strip("/").split("/")[0]
        if not first.endswith(".git"):
            body = b"404 not found: repo path must be /<name>.git/...\n"
            start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8"),
                                             ("Content-Length", str(len(body)))])
            return [body]

        # 最简口令保护（可选）
        if token:
            auth = environ.get("HTTP_AUTHORIZATION", "")
            ok = False
            if auth.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth[6:]).decode("utf-8", "replace")
                    ok = decoded.split(":", 1)[-1] == token
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                start_response("401 Unauthorized", [
                    ("WWW-Authenticate", 'Basic realm="NLvideo git"'),
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", "13"),
                ])
                return [b"need password"]

        content_length = int(environ.get("CONTENT_LENGTH") or 0)
        transfer_encoding = (environ.get("HTTP_TRANSFER_ENCODING") or "").lower()
        if "chunked" in transfer_encoding:
            body = _read_chunked(environ["wsgi.input"])
        elif content_length:
            body = environ["wsgi.input"].read(content_length)
        else:
            body = b""

        env = dict(os.environ)
        env.update(backend_env_base)
        env.update(
            {
                "REQUEST_METHOD": environ.get("REQUEST_METHOD", "GET"),
                "PATH_INFO": path_info,
                "QUERY_STRING": environ.get("QUERY_STRING", ""),
                "CONTENT_TYPE": environ.get("CONTENT_TYPE", ""),
                "CONTENT_LENGTH": str(len(body)),
                "REMOTE_USER": "dev",
                "REMOTE_ADDR": environ.get("HTTP_X_FORWARDED_FOR") or environ.get("REMOTE_ADDR", ""),
                "HTTP_GIT_PROTOCOL": environ.get("HTTP_GIT_PROTOCOL", ""),
                "HTTP_ACCEPT": environ.get("HTTP_ACCEPT", ""),
                "HTTP_CONTENT_ENCODING": environ.get("HTTP_CONTENT_ENCODING", ""),
            }
        )
        proc = subprocess.run(
            [backend],
            input=body,
            capture_output=True,
            cwd=str(project_root),
            env=env,
            timeout=300,
        )
        raw = proc.stdout
        head, sep, payload = raw.partition(b"\r\n\r\n")
        if not sep:  # 有些错误只打到 stderr
            payload = raw
            head = b"Status: 500 Internal Server Error\r\nContent-Type: text/plain\r\n"
        headers = []
        status = "200 OK"
        for line in head.split(b"\r\n"):
            if not line:
                continue
            name, _, value = line.partition(b":")
            key = name.decode("latin-1").strip().lower()
            val = value.decode("latin-1").strip()
            if key == "status":
                status = val
            elif key in {"content-type", "cache-control", "expires", "pragma", "www-authenticate"}:
                headers.append((name.decode("latin-1").strip(), val))
        headers.append(("Content-Length", str(len(payload))))
        start_response(status, headers)
        return [payload]

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="局域网 Git 智能 HTTP 共享服务")
    parser.add_argument("--repo", default=r"D:\NLvideo.git", help="裸仓库路径（默认 D:\\NLvideo.git）")
    parser.add_argument("--port", type=int, default=8088, help="监听端口（默认 8088）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--backend", default=None, help="git-http-backend.exe 路径")
    parser.add_argument("--git", default=None, help="git.exe 路径（用于设置 http.receivepack）")
    parser.add_argument("--token", default=None, help="可选：最简单的口令保护（Basic Auth 密码）")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / "HEAD").exists():
        print(f"[x] 不是裸仓库：{repo}（先用 git clone --bare 创建）", file=sys.stderr)
        return 1
    project_root = repo.parent
    backend = find_backend(args.backend)
    git = find_git(args.git)

    # 允许 HTTP 推送（默认 git 是关闭的）
    try:
        subprocess.run([git, "-C", str(repo), "config", "http.receivepack", "true"],
                       capture_output=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 设置 http.receivepack 失败（push 可能不可用）：{exc}")

    name = repo.name  # 例如 NLvideo.git
    ips: list[str] = []
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        import socket as _socket

        for info in _socket.getaddrinfo(_socket.gethostname(), None, _socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass

    print("=== NLvideo 局域网 Git 共享（HTTP）===")
    print(f"共享仓库 : {repo}")
    print(f"监听     : {args.host}:{args.port}")
    print(f"口令     : {'有（密码 ' + args.token + '）' if args.token else '无（同网可读写）'}")
    print()
    print("伙伴执行（同一 Wi-Fi）：")
    for ip in ips or ["<本机IP>"]:
        print(f"  git clone http://{ip}:{args.port}/{name} NLvideo")
    print()
    print('首次需在 Windows 防火墙放行（管理员 PowerShell 一次）：')
    print(f'  netsh advfirewall firewall add rule name="NLvideo git http {args.port}" '
          f'dir=in action=allow protocol=TCP localport={args.port}')
    print()
    print("提醒：无鉴权时同网设备可读写；演示完 Ctrl+C 关闭。")
    print()

    app = make_app(project_root, backend, args.token)

    class Handler(WSGIRequestHandler):
        def log_message(self, fmt, *args2):  # 精简日志
            sys.stderr.write("[http] " + (fmt % args2) + "\n")

    server = make_server(args.host, args.port, app, server_class=ThreadingWSGIServer, handler_class=Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
