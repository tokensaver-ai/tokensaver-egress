"""Agentic dual-write must skip count_tokens / non-LLM HTTP."""

from __future__ import annotations

import asyncio

from tokensaver_egress.audit import AuditSink, EgressRecord


def test_skips_count_tokens_agentic_dual_write(monkeypatch, tmp_path):
    monkeypatch.setattr("tokensaver_egress.wizard.CONFIG_DIR", tmp_path)
    enqueued: list[dict] = []

    class _Ship:
        def enqueue(self, envelope):
            enqueued.append(envelope)

    monkeypatch.setattr(
        "tokensaver_egress.claude_capture._SHIPPER",
        _Ship(),
        raising=False,
    )
    monkeypatch.setattr(
        "tokensaver_egress.claude_capture.current_claude_session_id",
        lambda: "sess-test",
    )
    monkeypatch.setattr(
        "tokensaver_egress.claude_capture.note_claude_session",
        lambda *_a, **_k: None,
    )
    seq = {"n": 0}

    def _envelope(**kwargs):
        seq["n"] += 1
        return {"seq": seq["n"], **kwargs}

    monkeypatch.setattr(
        "tokensaver_egress.claude_capture.build_envelope",
        _envelope,
    )

    sink = AuditSink(ingest_url="", api_key="")
    utility = EgressRecord(
        host="api.anthropic.com",
        capture_mode="mitm",
        attrs={
            "flow_kind": "llm",
            "path": "/v1/messages/count_tokens?beta=true",
            "model": "claude-haiku-4-5",
            "session_id": "sess-test",
        },
    )
    asyncio.run(sink.record(utility))
    assert utility.attrs.get("agentic_skipped") == "utility"
    assert utility.attrs.get("agentic_seq") is None
    assert enqueued == []

    chat = EgressRecord(
        host="api.anthropic.com",
        capture_mode="mitm",
        attrs={
            "flow_kind": "llm",
            "path": "/v1/messages?beta=true",
            "model": "claude-opus-5-5",
            "session_id": "sess-test",
        },
    )
    asyncio.run(sink.record(chat))
    assert chat.attrs.get("agentic_seq") == 1
    assert chat.attrs.get("agentic_source") == "api"
    assert len(enqueued) == 1
    assert enqueued[0]["payload"].get("path") == "/v1/messages?beta=true"


def test_skips_http_telemetry_agentic_dual_write(monkeypatch, tmp_path):
    monkeypatch.setattr("tokensaver_egress.wizard.CONFIG_DIR", tmp_path)
    enqueued: list[dict] = []

    class _Ship:
        def enqueue(self, envelope):
            enqueued.append(envelope)

    monkeypatch.setattr(
        "tokensaver_egress.claude_capture._SHIPPER",
        _Ship(),
        raising=False,
    )
    monkeypatch.setattr(
        "tokensaver_egress.claude_capture.current_claude_session_id",
        lambda: "sess-test",
    )
    monkeypatch.setattr(
        "tokensaver_egress.claude_capture.note_claude_session",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "tokensaver_egress.claude_capture.build_envelope",
        lambda **kwargs: {"seq": 9, **kwargs},
    )

    sink = AuditSink(ingest_url="", api_key="")
    rec = EgressRecord(
        host="api.anthropic.com",
        capture_mode="mitm",
        attrs={
            "flow_kind": "http",
            "path": "/api/event_logging/v2/batch",
            "session_id": "sess-test",
        },
    )
    asyncio.run(sink.record(rec))
    assert rec.attrs.get("agentic_skipped") == "http"
    assert enqueued == []


def test_dual_writes_failed_anthropic_llm(monkeypatch, tmp_path):
    """404 with model must still land in the causal graph (Flux IA Failed)."""
    monkeypatch.setattr("tokensaver_egress.wizard.CONFIG_DIR", tmp_path)
    enqueued: list[dict] = []

    class _Ship:
        def enqueue(self, envelope):
            enqueued.append(envelope)

    monkeypatch.setattr(
        "tokensaver_egress.claude_capture._SHIPPER",
        _Ship(),
        raising=False,
    )
    monkeypatch.setattr(
        "tokensaver_egress.claude_capture.current_claude_session_id",
        lambda: "sess-test",
    )
    monkeypatch.setattr(
        "tokensaver_egress.claude_capture.note_claude_session",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "tokensaver_egress.claude_capture.build_envelope",
        lambda **kwargs: {"seq": 42, **kwargs},
    )

    sink = AuditSink(ingest_url="", api_key="")
    rec = EgressRecord(
        host="api.anthropic.com",
        capture_mode="mitm",
        status_code=404,
        latency_ms=452,
        attrs={
            "flow_kind": "http",
            "model": "claude-opus-5-5",
            "path": "/v1/messages",
            "session_id": "sess-test",
        },
    )
    asyncio.run(sink.record(rec))
    assert rec.attrs.get("agentic_seq") == 42
    assert len(enqueued) == 1
    assert enqueued[0]["payload"].get("status_code") == 404
    assert enqueued[0]["payload"].get("model") == "claude-opus-5-5"
