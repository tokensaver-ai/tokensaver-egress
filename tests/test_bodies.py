"""ACP-4 §4.6 — full payload capture helpers (egress.bodies)."""

from __future__ import annotations

import importlib

import tokensaver_egress.bodies as bodies


def _reload(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return importlib.reload(bodies)


def test_bodies_disabled_by_default(monkeypatch):
    b = _reload(monkeypatch, EGRESS_CAPTURE_BODIES=None)
    assert b.bodies_enabled() is False


def test_bodies_enabled_flag(monkeypatch):
    b = _reload(monkeypatch, EGRESS_CAPTURE_BODIES="1")
    assert b.bodies_enabled() is True


def test_prepare_body_decodes_and_truncates(monkeypatch):
    b = _reload(monkeypatch, EGRESS_BODY_MAX_BYTES="8")
    assert b.prepare_body(None) is None
    assert b.prepare_body(b"") is None
    assert b.prepare_body(b'{"a":1}') == '{"a":1}'
    out = b.prepare_body(b"0123456789ABCDEF")
    assert out.startswith("01234567")
    assert "truncated" in out


def test_sanitize_headers_redacts_credentials(monkeypatch):
    b = _reload(monkeypatch, EGRESS_CAPTURE_RAW_HEADERS=None)
    sanitized = b.sanitize_headers({"Authorization": "Bearer sk-x", "X-Api-Key": "k", "Accept": "application/json"})
    assert sanitized["Authorization"] == "[REDACTED]"
    assert sanitized["X-Api-Key"] == "[REDACTED]"
    assert sanitized["Accept"] == "application/json"


def test_sanitize_headers_raw_opt_out(monkeypatch):
    b = _reload(monkeypatch, EGRESS_CAPTURE_RAW_HEADERS="1")
    sanitized = b.sanitize_headers({"Authorization": "Bearer sk-x"})
    assert sanitized["Authorization"] == "Bearer sk-x"


def test_headers_from_lines_skips_request_line(monkeypatch):
    b = _reload(monkeypatch, EGRESS_CAPTURE_RAW_HEADERS=None)
    lines = [b"Content-Type: application/json\r\n", b"Authorization: Bearer x\r\n"]
    parsed = b.headers_from_lines(lines)
    assert parsed["Content-Type"] == "application/json"
    assert parsed["Authorization"] == "[REDACTED]"


def test_reload_restores_default(monkeypatch):
    _reload(monkeypatch, EGRESS_CAPTURE_BODIES=None, EGRESS_BODY_MAX_BYTES=None, EGRESS_CAPTURE_RAW_HEADERS=None)
