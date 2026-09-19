"""Loop id stamping on egress audits (Boucles / ACP-9)."""

from __future__ import annotations

import os

from tokensaver_egress.audit import AuditSink, EgressRecord, record_to_ingest_dict
from tokensaver_egress.mitm import _attach_loop_meta, _loop_from_headers


def test_loop_from_headers_falls_back_to_env_when_headers_empty(monkeypatch):
    monkeypatch.setenv("TOKENSAVER_LOOP_ID", "loop_env")
    assert _loop_from_headers(None) == "loop_env"
    assert _loop_from_headers({}) == "loop_env"
    assert _loop_from_headers({"Host": "api.anthropic.com"}) == "loop_env"


def test_loop_from_headers_prefers_header(monkeypatch):
    monkeypatch.setenv("TOKENSAVER_LOOP_ID", "loop_env")
    assert (
        _loop_from_headers({"X-Tokensaver-Loop-Id": "loop_hdr", "Host": "x"}) == "loop_hdr"
    )


def test_attach_loop_meta_empty_headers(monkeypatch):
    monkeypatch.setenv("TOKENSAVER_LOOP_ID", "loop_env")
    monkeypatch.setenv("TOKENSAVER_LOOP_ITERATION", "3")
    meta: dict = {}
    _attach_loop_meta(meta, None)
    assert meta["loop_id"] == "loop_env"
    assert meta["loop_iteration"] == 3


def test_audit_sink_stamps_from_env(monkeypatch):
    monkeypatch.setenv("TOKENSAVER_LOOP_ID", "loop_sink")
    monkeypatch.setenv("TOKENSAVER_LOOP_ITERATION", "2")
    sink = AuditSink(ingest_url="", api_key="")
    rec = EgressRecord(host="api.anthropic.com", capture_mode="mitm", attrs={"flow_kind": "llm"})

    import asyncio

    asyncio.run(sink.record(rec))
    assert rec.attrs["loop_id"] == "loop_sink"
    assert rec.attrs["loop_iteration"] == 2
    payload = record_to_ingest_dict(rec)
    assert payload["loop_id"] == "loop_sink"
    assert payload["loop_iteration"] == 2
