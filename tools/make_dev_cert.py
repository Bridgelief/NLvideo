#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成本机开发用的 HTTPS 证书（自签 CA + 服务器证书），给手机真机调试用。

为什么需要：手机浏览器只在**安全上下文**里允许 getUserMedia（相机）。局域网用
http://<电脑IP> 打开时浏览器会禁用相机，只能用演示模式；换成 https:// 即可。

本机没有 openssl，这里用 `cryptography` 生成：
  certs/ca.pem          # 自签 CA（Android App 内置信任；iOS 可装描述文件）
  certs/ca-key.pem      # CA 私钥（只在本机调式用，别分发）
  certs/server.pem      # 服务器证书（CA 签发，含 SAN：localhost / 127.0.0.1 / ::1 / 局域网 IP）
  certs/server-key.pem  # 服务器私钥

用法：
  python tools/make_dev_cert.py                # 缺证书或 LAN IP 变了才重新生成
  python tools/make_dev_cert.py --force        # 强制重新生成
  python tools/make_dev_cert.py --lan-ip 192.168.1.5 --lan-ip 10.0.0.8
  python tools/make_dev_cert.py --out D:/tmp/certs

幂等：证书存在、未临近过期、SAN 覆盖当前探测到的局域网 IP、并且确实由同一个 CA 签发，
就直接复用（打印复用的证书信息），不会每次重写文件。
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "certs"

CA_CN = "LearnBuddy Dev CA"
SERVER_CN = "localhost"
LEAF_DAYS = 397
CA_DAYS = 3650
MIN_REMAINING_DAYS = 7


# ---------------------------------------------------------------------------
# 网络探测
# ---------------------------------------------------------------------------
def detect_lan_ips() -> list[str]:
    """探测本机局域网 IPv4（去重、排除回环/APIPA）。"""
    ips: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # 不会真的发包，只用来选出口网卡
        ips.add(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(
        ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254.")
    )


def required_sans(lan_ips: list[str]) -> list[x509.GeneralName]:
    """服务器证书必须覆盖的名字：localhost + 回环 + 传入/探测到的局域网 IP。"""
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    try:
        hostname = socket.gethostname()
        if hostname and "." not in hostname:
            names.append(x509.DNSName(hostname))
    except OSError:
        pass
    for ip in sorted(set(lan_ips)):
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            continue
    # 去重（按类型+值）
    uniq: dict[tuple[str, str], x509.GeneralName] = {}
    for name in names:
        key = (type(name).__name__, str(getattr(name, "value", getattr(name, "address", ""))))
        uniq.setdefault(key, name)
    return list(uniq.values())


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def build_ca(out: Path, days: int = CA_DAYS) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = _new_key()
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LearnBuddy"),
            x509.NameAttribute(NameOID.COMMON_NAME, CA_CN),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_cert(out / "ca.pem", cert)
    _write_key(out / "ca-key.pem", key)
    return cert, key


def build_server_cert(
    out: Path,
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    sans: list[x509.GeneralName],
) -> x509.Certificate:
    key = _new_key()
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LearnBuddy"),
            x509.NameAttribute(NameOID.COMMON_NAME, SERVER_CN),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
    )
    cert = cert.sign(ca_key, hashes.SHA256())
    _write_cert(out / "server.pem", cert)
    _write_key(out / "server-key.pem", key)
    return cert


# ---------------------------------------------------------------------------
# 检查 / 复用
# ---------------------------------------------------------------------------
def _san_values(cert: x509.Certificate) -> set[str]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set()
    values: set[str] = set()
    for name in san:
        if isinstance(name, x509.IPAddress):
            values.add(str(name.value))
        elif isinstance(name, x509.DNSName):
            values.add(str(name.value))
    return values


def inspect_existing(out: Path, required_ip_set: set[str]) -> tuple[bool, str]:
    """判断现有证书能否复用；返回 (可用, 说明)。"""
    paths = {name: out / name for name in ("ca.pem", "ca-key.pem", "server.pem", "server-key.pem")}
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        return False, f"缺少证书文件：{', '.join(missing)}"
    try:
        ca_cert = x509.load_pem_x509_certificate(paths["ca.pem"].read_bytes())
        server_cert = x509.load_pem_x509_certificate(paths["server.pem"].read_bytes())
    except ValueError as exc:
        return False, f"证书解析失败：{exc}"

    now = dt.datetime.now(dt.timezone.utc)
    remaining = (server_cert.not_valid_after_utc - now).days
    if remaining < MIN_REMAINING_DAYS:
        return False, f"服务器证书只剩 {remaining} 天，需要重新生成"
    if ca_cert.not_valid_after_utc <= now:
        return False, "CA 证书已过期"

    san = _san_values(server_cert)
    missing_ip = sorted(ip for ip in required_ip_set if ip not in san)
    if missing_ip:
        return False, f"现有证书 SAN 不含局域网 IP：{', '.join(missing_ip)}"
    if "localhost" not in san:
        return False, "现有证书 SAN 不含 localhost"

    # 由 CA 签发校验（链式 verify 在 Python ssl 里用 CA 文件即可，这里做签名级校验）
    try:
        ca_pub = ca_cert.public_key()
        from cryptography.hazmat.primitives.asymmetric import padding

        if isinstance(ca_pub, rsa.RSAPublicKey):
            ca_pub.verify(
                server_cert.signature,
                server_cert.tbs_certificate_bytes,
                padding.PKCS1v15(),
                server_cert.signature_hash_algorithm,
            )
    except Exception as exc:  # noqa: BLE001
        return False, f"服务器证书不是该 CA 签发：{exc}"
    return True, f"复用现有证书（剩余 {remaining} 天，SAN 覆盖 {len(san)} 项）"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成局域网 HTTPS 调试证书（CA + 服务器证书）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="证书输出目录（默认 <repo>/certs）")
    parser.add_argument("--force", action="store_true", help="强制重新生成（即使现有证书可用）")
    parser.add_argument("--lan-ip", action="append", default=[], help="额外追加的局域网 IP（可多次）")
    parser.add_argument("--detect-only", action="store_true", help="只打印探测到的 IP，不生成证书")
    args = parser.parse_args(argv)

    lan_ips = detect_lan_ips()
    for extra in args.lan_ip:
        lan_ips.append(extra.strip())
    lan_ips = sorted(set(lan_ips))

    if args.detect_only:
        print("局域网 IP:", ", ".join(lan_ips) if lan_ips else "（未探测到，可手动 --lan-ip）")
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    ok, reason = (False, "强制重新生成") if args.force else inspect_existing(out, set(lan_ips + ["127.0.0.1"]))
    if ok:
        print(f"[certs] {reason}")
        cert = x509.load_pem_x509_certificate((out / "server.pem").read_bytes())
        print(f"[certs] server.pem 到期：{cert.not_valid_after_utc.strftime('%Y-%m-%d')}")
        print(f"[certs] 指纹 SHA256：{cert.fingerprint(hashes.SHA256()).hex()[:32]}…")
        print(f"[certs] 局域网 IP：{', '.join(lan_ips) if lan_ips else '无'}")
        return 0

    print(f"[certs] 重新生成：{reason}")
    ca_cert, ca_key = build_ca(out)
    sans = required_sans(lan_ips)
    server_cert = build_server_cert(out, ca_cert, ca_key, sans)
    print(f"[certs] 输出目录：{out}")
    print(f"[certs] SAN：{', '.join(sorted(_san_values(server_cert)))}")
    print(f"[certs] server.pem 到期：{server_cert.not_valid_after_utc.strftime('%Y-%m-%d')}（{LEAF_DAYS} 天）")
    print(f"[certs] ca.pem 到期：{ca_cert.not_valid_after_utc.strftime('%Y-%m-%d')}（{CA_DAYS} 天）")
    print(f"[certs] CA 指纹 SHA256：{ca_cert.fingerprint(hashes.SHA256()).hex()}")
    print("[certs] 手机访问：https://<局域网IP>:8443 → 点「高级/继续前往」→ 允许相机")
    print("[certs] Android App 可内置信任 certs/ca.pem（网络安全性配置），无需在系统装证书")
    return 0


if __name__ == "__main__":
    sys.exit(main())
