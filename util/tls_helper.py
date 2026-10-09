"""自签名 HTTPS 证书：iPad 上的浏览器只在 https 下给麦克风。证书写在 config/tls/（不进 git），覆盖本机所有 IPv4 地址和
localhost；地址变了（换网络）就重新生成。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import ipaddress
import json
import socket
import subprocess

import psutil
from loguru import logger

from config.settings import ServerConfig, use_utf8_output


def addresses() -> list[str]:
    """Every IPv4 address of this machine but loopback and link-local (with mirrored WSL networking: the LAN and Tailscale)."""
    found = []
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family == socket.AF_INET:
                address = ipaddress.ip_address(entry.address)
                if not (address.is_loopback or address.is_link_local) and str(address) != "10.255.255.254":
                    found.append(str(address))
    return sorted(set(found))


def ensure_certificate() -> tuple[Path, Path]:
    """(certificate, key), made again when the machine's addresses are not all in the one there."""
    directory = ServerConfig.TLS_DIR
    certificate, key, names = directory / "cert.pem", directory / "key.pem", directory / "names.json"
    wanted = ["localhost", "127.0.0.1", *addresses()]
    if certificate.exists() and key.exists() and names.exists() and json.loads(names.read_text()) == wanted:
        return certificate, key
    directory.mkdir(parents=True, exist_ok=True)
    san = ",".join(f"IP:{name}" if name[0].isdigit() else f"DNS:{name}" for name in wanted)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "825", "-nodes",
                    "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=XLeRobot",
                    "-addext", f"subjectAltName={san}"], check=True, capture_output=True)
    key.chmod(0o600)
    names.write_text(json.dumps(wanted))
    logger.info("HTTPS 证书已生成：{}", "，".join(wanted))
    return certificate, key


def demo_tls_helper() -> None:
    certificate, key = ensure_certificate()
    logger.info("证书 {}，地址 {}", certificate, addresses())


def main() -> None:
    use_utf8_output()
    demo_tls_helper()


if __name__ == "__main__":
    main()
