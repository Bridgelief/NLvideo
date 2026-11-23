# -*- coding: utf-8 -*-
"""开发证书测试：CA/服务器证书链、SAN、幂等、真实 TLS 握手。

跑法（仓库根目录）：python -m unittest tests.test_dev_cert -v
"""

import hashlib
import http.server
import ipaddress
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "make_dev_cert.py"


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def san_values(cert: x509.Certificate) -> set[str]:
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    values = set()
    for name in san:
        if isinstance(name, x509.IPAddress):
            values.add(str(name.value))
        elif isinstance(name, x509.DNSName):
            values.add(str(name.value))
    return values


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - 标准库接口
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


def start_tls_server(cert: Path, key: Path) -> tuple[http.server.HTTPServer, int]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


class TestDevCert(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.proc = run_tool("--out", str(cls.out), "--lan-ip", "10.9.8.7", "--force")
        cls.ca = cls.out / "ca.pem"
        cls.cert = cls.out / "server.pem"
        cls.key = cls.out / "server-key.pem"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_files_created(self):
        self.assertEqual(self.proc.returncode, 0, msg=self.proc.stderr)
        for path in (self.ca, self.out / "ca-key.pem", self.cert, self.key):
            self.assertTrue(path.exists(), msg=f"缺少 {path.name}")
            self.assertGreater(path.stat().st_size, 200)

    def test_san_covers_localhost_loopback_and_lan_ip(self):
        cert = x509.load_pem_x509_certificate(self.cert.read_bytes())
        values = san_values(cert)
        self.assertIn("localhost", values)
        self.assertIn("127.0.0.1", values)
        self.assertIn("10.9.8.7", values, msg="手动传入的 --lan-ip 必须进 SAN")

    def test_leaf_signed_by_ca(self):
        ca = x509.load_pem_x509_certificate(self.ca.read_bytes())
        leaf = x509.load_pem_x509_certificate(self.cert.read_bytes())
        self.assertEqual(ca.subject.rfc4514_string(), leaf.issuer.rfc4514_string())
        from cryptography.hazmat.primitives.asymmetric import padding

        ca.public_key().verify(
            leaf.signature,
            leaf.tbs_certificate_bytes,
            padding.PKCS1v15(),
            leaf.signature_hash_algorithm,
        )

    def test_validity(self):
        cert = x509.load_pem_x509_certificate(self.cert.read_bytes())
        remaining = (cert.not_valid_after_utc - datetime.now(timezone.utc)).days
        self.assertGreater(remaining, 30, msg=f"服务器证书只剩 {remaining} 天")
        ca = x509.load_pem_x509_certificate(self.ca.read_bytes())
        self.assertGreater((ca.not_valid_after_utc - datetime.now(timezone.utc)).days, 365)

    def test_real_tls_handshake_trusted_by_ca(self):
        """真的起一个 TLS 服务，用 CA 校验客户端连接成功（证明链可用）。"""
        httpd, port = start_tls_server(self.cert, self.key)
        try:
            ctx = ssl.create_default_context(cafile=str(self.ca))
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
            with urllib.request.urlopen(f"https://127.0.0.1:{port}/", timeout=10, context=ctx) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.read(), b"ok")
        finally:
            httpd.shutdown()

    def test_untrusted_client_rejected(self):
        httpd, port = start_tls_server(self.cert, self.key)
        try:
            with self.assertRaises(urllib.error.URLError):
                urllib.request.urlopen(f"https://127.0.0.1:{port}/", timeout=5)
        finally:
            httpd.shutdown()

    def test_idempotent_without_force(self):
        before = {name: sha256(self.out / name) for name in ("server.pem", "ca.pem", "server-key.pem")}
        proc = run_tool("--out", str(self.out), "--lan-ip", "10.9.8.7")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("复用现有证书", proc.stdout)
        after = {name: sha256(self.out / name) for name in ("server.pem", "ca.pem", "server-key.pem")}
        self.assertEqual(before, after)

    def test_force_regenerates(self):
        before = sha256(self.cert)
        proc = run_tool("--out", str(self.out), "--lan-ip", "10.9.8.7", "--force")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertNotEqual(sha256(self.cert), before)

    def test_detect_only_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "certs"
            proc = run_tool("--out", str(out), "--detect-only")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertFalse(out.exists(), msg="--detect-only 不应创建目录/证书")


if __name__ == "__main__":
    unittest.main(verbosity=2)
