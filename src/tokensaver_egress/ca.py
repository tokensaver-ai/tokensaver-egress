"""Local MITM CA for tokensaver-egress (ACP-4 opt-in TLS inspection)."""

from __future__ import annotations

import datetime
import logging
import os
import ssl
from datetime import timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_CA_DIR = Path(os.environ.get("EGRESS_CA_DIR", str(Path.home() / ".tokensaver-egress" / "ca")))
logger = logging.getLogger("tokensaver-egress.ca")


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

    def init_ca(self, *, force: bool = False) -> Path:
        """Generate a new local CA (idempotent if already present unless ``force``)."""
        if self.is_initialized() and not force:
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
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_cert_sign=True,
                    crl_sign=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
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
        # Old host certs were signed by a previous key → CERT_SIGNATURE_FAILURE.
        self._purge_all_leaves()
        return self._ca_cert_path

    def _leaf_paths(self, hostname: str) -> tuple[Path, Path]:
        safe = hostname.replace("*", "_").replace(":", "_")[:128]
        return self._leaves_dir / f"{safe}.crt", self._leaves_dir / f"{safe}.key"

    def _load_ca(self) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
        ca_key = serialization.load_pem_private_key(self._ca_key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(self._ca_cert_path.read_bytes())
        if not isinstance(ca_key, rsa.RSAPrivateKey):
            raise TypeError("MITM CA key must be RSA")
        return ca_key, ca_cert

    def _leaf_signed_by_current_ca(self, cert_path: Path, ca_cert: x509.Certificate) -> bool:
        """False when leaf was issued by a previous CA with the same subject CN."""
        try:
            leaf = x509.load_pem_x509_certificate(cert_path.read_bytes())
        except Exception:
            return False
        if leaf.issuer != ca_cert.subject:
            return False
        try:
            ca_cert.public_key().verify(  # type: ignore[union-attr]
                leaf.signature,
                leaf.tbs_certificate_bytes,
                padding.PKCS1v15(),
                leaf.signature_hash_algorithm,  # type: ignore[arg-type]
            )
            return True
        except Exception:
            return False

    def _purge_all_leaves(self) -> int:
        removed = 0
        if not self._leaves_dir.is_dir():
            return 0
        for path in self._leaves_dir.iterdir():
            if path.is_file():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def ensure_leaves_match_ca(self) -> int:
        """Delete cached host certs not signed by the current CA key. Returns files removed."""
        if not self.is_initialized():
            return 0
        _, ca_cert = self._load_ca()
        removed = 0
        for cert_path in self._leaves_dir.glob("*.crt"):
            if self._leaf_signed_by_current_ca(cert_path, ca_cert):
                continue
            key_path = cert_path.with_suffix(".key")
            for p in (cert_path, key_path):
                try:
                    if p.is_file():
                        p.unlink()
                        removed += 1
                except OSError:
                    pass
        if removed:
            logger.warning(
                "Removed %s stale MITM leaf file(s) (CA was rotated — old host certs caused "
                "CERT_SIGNATURE_FAILURE). New leaves will be issued on demand.",
                removed,
            )
        return removed

    def _issue_leaf(self, hostname: str) -> None:
        if not self.is_initialized():
            raise FileNotFoundError(
                f"CA not initialized — run: tokensaver-egress init-ca (dir={self.ca_dir})"
            )

        cert_path, key_path = self._leaf_paths(hostname)
        ca_key, ca_cert = self._load_ca()
        if (
            cert_path.is_file()
            and key_path.is_file()
            and self._leaf_signed_by_current_ca(cert_path, ca_cert)
        ):
            return
        for p in (cert_path, key_path):
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass

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
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
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
