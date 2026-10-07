"""MITM CA leaf cache must stay signed by the current CA key."""

from __future__ import annotations

from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tokensaver_egress.ca import MitmCA


def _write_orphan_leaf(ca_dir: Path, hostname: str = "api.anthropic.com") -> Path:
    """Leaf with matching CN issuer name but signed by a *different* key (stale CA)."""
    old_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "TokenSaver Egress MITM CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TokenSaver"),
        ]
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(issuer)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        .not_valid_after(
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + __import__("datetime").timedelta(days=30)
        )
        .sign(old_key, hashes.SHA256())
    )
    leaves = ca_dir / "leaves"
    leaves.mkdir(parents=True, exist_ok=True)
    cert_path = leaves / f"{hostname}.crt"
    key_path = leaves / f"{hostname}.key"
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path


def test_ensure_leaves_match_ca_purges_stale(tmp_path: Path) -> None:
    ca = MitmCA(tmp_path)
    ca.init_ca()
    stale = _write_orphan_leaf(tmp_path)
    assert stale.is_file()
    removed = ca.ensure_leaves_match_ca()
    assert removed >= 2
    assert not stale.is_file()


def test_issue_leaf_replaces_stale_signature(tmp_path: Path) -> None:
    ca = MitmCA(tmp_path)
    ca.init_ca()
    host = "api.anthropic.com"
    _write_orphan_leaf(tmp_path, host)
    ctx = ca.server_ssl_context(host)
    assert ctx is not None
    leaf = x509.load_pem_x509_certificate((tmp_path / "leaves" / f"{host}.crt").read_bytes())
    ca_cert = x509.load_pem_x509_certificate(ca.ca_cert_path.read_bytes())
    ca_cert.public_key().verify(  # type: ignore[union-attr]
        leaf.signature,
        leaf.tbs_certificate_bytes,
        __import__("cryptography.hazmat.primitives.asymmetric.padding", fromlist=["padding"]).PKCS1v15(),
        leaf.signature_hash_algorithm,  # type: ignore[arg-type]
    )


def test_init_ca_force_purges_leaves(tmp_path: Path) -> None:
    ca = MitmCA(tmp_path)
    ca.init_ca()
    ca.server_ssl_context("api.openai.com")
    assert any((tmp_path / "leaves").glob("*.crt"))
    ca.init_ca(force=True)
    assert not any((tmp_path / "leaves").glob("*.crt"))
