"""HTTPS 证书：iPad 上的 Safari 只在 https 下给麦克风，而且只信任「受信任的根证书」签出来的证书。
所以这台电脑自己做一个根证书（本地 CA，config/tls/ca.pem，10 年），每个 iPad 装一次、打开完全信任；网页的证书由它签发，
覆盖本机所有 IPv4 地址和 localhost，地址变了（换网络）就用同一个 CA 重新签，iPad 不用重装。
证书都在 config/tls/（不进 git）。"""

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
import tempfile

import psutil
from loguru import logger

from config.settings import ServerConfig, use_utf8_output


def addresses() -> list[str]:
    """Every IPv4 address of this machine but loopback and link-local
    (with mirrored WSL networking: the LAN and Tailscale)."""
    found = []
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family == socket.AF_INET:
                address = ipaddress.ip_address(entry.address)
                if not (address.is_loopback or address.is_link_local) and str(address) != "10.255.255.254":
                    found.append(str(address))
    return sorted(set(found))


def ca_paths() -> tuple[Path, Path]:
    directory = ServerConfig.TLS_DIR
    return directory / "ca.pem", directory / "ca_key.pem"


def ensure_ca() -> Path:
    """The local root certificate (made once, 10 years): what each iPad installs and trusts. Kept when it exists, so
    new server certificates need no reinstall."""
    ca, ca_key = ca_paths()
    if ca.exists() and ca_key.exists():
        return ca
    ca.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "3650", "-nodes",
                    "-keyout", str(ca_key), "-out", str(ca), "-subj", "/CN=XLeRobot Local Root CA/O=XLeRobot",
                    "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
                   check=True, capture_output=True)
    ca_key.chmod(0o600)
    logger.info("本地根证书已生成：{}（每个 iPad 装一次）", ca)
    return ca


def ensure_certificate() -> tuple[Path, Path]:
    """(certificate, key) for the web server, signed by the local root; made again when the machine's addresses are not
    all in the one there, or it was not signed by this root (the self-signed ones before 2026-10-10)."""
    directory = ServerConfig.TLS_DIR
    certificate, key, names = directory / "cert.pem", directory / "key.pem", directory / "names.json"
    ca, ca_key = ca_paths()
    wanted = {"names": ["localhost", "127.0.0.1", *addresses()], "signed_by_ca": True}
    if (ca.exists() and certificate.exists() and key.exists() and names.exists()
            and json.loads(names.read_text()) == wanted):
        return certificate, key
    ensure_ca()
    san = ",".join(f"IP:{name}" if name[0].isdigit() else f"DNS:{name}" for name in wanted["names"])
    # Apple's rules for TLS server certificates: at most 825 days, the names in subjectAltName, an extended key usage
    # with serverAuth; a leaf, not a CA.
    with tempfile.TemporaryDirectory() as scratch:
        request, extensions = Path(scratch) / "server.csr", Path(scratch) / "server.ext"
        extensions.write_text(f"subjectAltName={san}\nbasicConstraints=critical,CA:FALSE\n"
                              "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
        subprocess.run(["openssl", "req", "-newkey", "rsa:2048", "-sha256", "-nodes", "-keyout", str(key), "-out",
                        str(request), "-subj", "/CN=XLeRobot"], check=True, capture_output=True)
        subprocess.run(["openssl", "x509", "-req", "-in", str(request), "-CA", str(ca), "-CAkey", str(ca_key),
                        "-CAcreateserial", "-out", str(certificate), "-days", "825", "-sha256", "-extfile",
                        str(extensions)], check=True, capture_output=True)
    key.chmod(0o600)
    names.write_text(json.dumps(wanted))
    logger.info("HTTPS 证书已生成（本地根证书签发）：{}", "，".join(wanted["names"]))
    return certificate, key


def demo_tls_helper() -> None:
    certificate, key = ensure_certificate()
    check = subprocess.run(["openssl", "verify", "-CAfile", str(ca_paths()[0]), str(certificate)],
                           capture_output=True, text=True)
    logger.info("证书 {}，地址 {}；校验：{}", certificate, addresses(), check.stdout.strip() or check.stderr.strip())


def main() -> None:
    use_utf8_output()
    demo_tls_helper()


if __name__ == "__main__":
    main()
