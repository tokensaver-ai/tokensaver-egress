"""Local MITM CA for tokensaver-egress (ACP-4 opt-in TLS inspection)."""

from __future__ import annotations

import datetime
import ipaddress
import os
import ssl
from datetime import timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

DEFAULT_CA_DIR = Path(os.environ.get("EGRESS_CA_DIR", str(Path.home() / ".tokensaver-egress" / "ca")))


class MitmCA:
    """On-disk CA + per-host leaf certificates for TLS MITM."""

    def __init__(self, ca_dir: Path | None = None) -> None:
        self.ca_dir = Path(ca_dir or DEFAULT_CA_DIR)
        self.ca_dir.mkdir(parents=True, exist_ok=True)
        self._ca_key_path = self.ca_dir / "ca.key"
        self._ca_cert_path = self.ca_dir / "ca.crt"
        self._leaves_dir = self.ca_dir / "leaves"
        self._leaves_dir.mkdir(parents=True, exist_ok=True)

    @property
    def ca_cert_path(self) -> Path:
        return self._ca_cert_path

    def is_initialized(self) -> bool:
        return self._ca_key_path.is_file() and self._ca_cert_path.is_file()

    def init_ca(self) -> Path:
        """Generate a new local CA (idempotent if already present)."""
        if self.is_initialized():
            return self._ca_cert_path

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "TokenSaver Egress MITM CA"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TokenSaver"),
            ]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(timezone.utc))
            .not_valid_after(datetime.datetime.now(timezone.utc) + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(key, hashes.SHA256())
        )
        self._ca_key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self._ca_cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return self._ca_cert_path

    def _leaf_paths(self, hostname: str) -> tuple[Path, Path]:
        safe = hostname.replace("*", "_").replace(":", "_")[:128]
        return self._leaves_dir / f"{safe}.crt", self._leaves_dir / f"{safe}.key"

    def _issue_leaf(self, hostname: str) -> None:
        if not self.is_initialized():
            raise FileNotFoundError(f"CA not initialized — run: python -m tokensaver_egress init-ca (dir={self.ca_dir})")

        cert_path, key_path = self._leaf_paths(hostname)
        if cert_path.is_file() and key_path.is_file():
            return

        ca_key = serialization.load_pem_private_key(self._ca_key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(self._ca_cert_path.read_bytes())
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
        san = x509.SubjectAlternativeName([x509.DNSName(hostname)])
        leaf_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(timezone.utc))
            .not_valid_after(datetime.datetime.now(timezone.utc) + datetime.timedelta(days=825))
            .add_extension(san, critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        key_path.write_bytes(
            leaf_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))

    def server_ssl_context(self, hostname: str) -> ssl.SSLContext:
        self._issue_leaf(hostname)
        cert_path, key_path = self._leaf_paths(hostname)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        # Pin ALPN to HTTP/1.1: the relay parses HTTP/1, so we must not let the
        # client negotiate HTTP/2 inside the MITM tunnel.
        try:
            ctx.set_alpn_protocols(["http/1.1"])
        except NotImplementedError:
            pass
        return ctx

    def client_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        # Force HTTP/1.1 upstream so the response is parseable by the HTTP/1 relay.
        try:
            ctx.set_alpn_protocols(["http/1.1"])
        except NotImplementedError:
            pass
        return ctx
