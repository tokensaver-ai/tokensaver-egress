"""EGRESS_AUDIT_ONLY / journalisation seule flag."""

from __future__ import annotations

from tokensaver_egress.audit_only import audit_only_mode


def test_audit_only_env_flag(monkeypatch):
    monkeypatch.delenv("EGRESS_AUDIT_ONLY", raising=False)
    monkeypatch.delenv("EGRESS_PIPELINE_MODE", raising=False)
    assert audit_only_mode() is False
    monkeypatch.setenv("EGRESS_AUDIT_ONLY", "1")
    assert audit_only_mode() is True


def test_audit_only_pipeline_mode_alias(monkeypatch):
    monkeypatch.delenv("EGRESS_AUDIT_ONLY", raising=False)
    monkeypatch.setenv("EGRESS_PIPELINE_MODE", "audit")
    assert audit_only_mode() is True
    monkeypatch.setenv("EGRESS_PIPELINE_MODE", "passthrough_audit")
    assert audit_only_mode() is True
    monkeypatch.setenv("EGRESS_PIPELINE_MODE", "full")
    assert audit_only_mode() is False
