"""MITM cache hit path — thin proxy writes backend-synthesized response verbatim."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from tokensaver_egress.cache import CacheLookupResult
from tokensaver_egress.mitm import _emit_cached_response
from tokensaver_egress.rag import RagEnrichResult


class FakeWriter:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        return None


class FakeSink:
    def __init__(self):
        self.records = []

    async def record(self, rec):
        self.records.append(rec)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _req(stream: bool):
    return SimpleNamespace(
        body=json.dumps({"model": "claude-sonnet-4-6", "stream": stream, "messages": []}).encode(),
        path="/v1/messages",
        content_type="application/json",
        headers={},
    )


def _pre_cls():
    return SimpleNamespace(
        flow_kind="llm",
        provider="anthropic",
        model="claude-sonnet-4-6",
        method="POST",
        args_hash="abc",
    )


def test_emit_cached_json_response(monkeypatch):
    monkeypatch.setattr("tokensaver_egress.mitm.bodies_enabled", lambda: False)
    writer = FakeWriter()
    sink = FakeSink()
    hit = CacheLookupResult(
        hit=True,
        hit_type="exact",
        synth_status=200,
        synth_content_type="application/json",
        synth_body=b'{"content":[{"type":"text","text":"hello cached"}]}',
        model="claude-sonnet-4-6",
        tokens_input=5,
        tokens_output=3,
    )
    _run(
        _emit_cached_response(
            "api.anthropic.com", _req(False), _pre_cls(), writer, sink, time.monotonic(), 100, hit
        )
    )
    raw = bytes(writer.buf).decode("latin-1", errors="replace")
    assert "HTTP/1.1 200 OK" in raw
    assert "application/json" in raw.lower()
    assert "hello cached" in raw
    assert sink.records
    attrs = sink.records[0].attrs
    assert attrs["cache_hit"] is True
    assert attrs["cache_hit_type"] == "exact"
    assert attrs["tokens_output"] == 3


def test_emit_cached_sse_response(monkeypatch):
    monkeypatch.setattr("tokensaver_egress.mitm.bodies_enabled", lambda: False)
    writer = FakeWriter()
    sink = FakeSink()
    sse_body = (
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"sse text"}}\n\n'
        "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
    ).encode()
    hit = CacheLookupResult(
        hit=True,
        hit_type="exact",
        synth_status=200,
        synth_content_type="text/event-stream",
        synth_body=sse_body,
        tokens_input=1,
        tokens_output=2,
    )
    _run(
        _emit_cached_response(
            "api.anthropic.com", _req(True), _pre_cls(), writer, sink, time.monotonic(), 50, hit
        )
    )
    raw = bytes(writer.buf).decode("utf-8", errors="replace")
    assert "text/event-stream" in raw
    assert "content_block_delta" in raw
    assert "sse text" in raw


def test_emit_cached_response_preserves_rag_audit_attrs(monkeypatch):
    monkeypatch.setattr("tokensaver_egress.mitm.bodies_enabled", lambda: False)
    writer = FakeWriter()
    sink = FakeSink()
    hit = CacheLookupResult(
        hit=True,
        hit_type="exact",
        synth_status=200,
        synth_content_type="application/json",
        synth_body=b'{"ok":true}',
        model="claude-sonnet-4-6",
        tokens_input=10,
        tokens_output=5,
    )
    rag = RagEnrichResult(
        modified=False,
        rag_evaluated=True,
        chunks_count=0,
        skipped_reason="no_results",
        detail={"has_similarity": False, "skipped_reason": "no_results", "query_length": 18},
    )
    _run(
        _emit_cached_response(
            "api.anthropic.com",
            _req(False),
            _pre_cls(),
            writer,
            sink,
            time.monotonic(),
            100,
            hit,
            rag_result=rag,
        )
    )
    attrs = sink.records[0].attrs
    assert attrs["cache_hit"] is True
    assert attrs["rag_evaluated"] is True
    assert attrs["rag_skipped_reason"] == "no_results"
    assert isinstance(attrs.get("rag"), dict)


def test_emit_cached_response_merges_response_pii_audit(monkeypatch):
    monkeypatch.setattr("tokensaver_egress.mitm.bodies_enabled", lambda: False)
    writer = FakeWriter()
    sink = FakeSink()
    hit = CacheLookupResult(
        hit=True,
        hit_type="exact",
        synth_status=200,
        synth_content_type="application/json",
        synth_body=b'{"content":[{"type":"text","text":"masked"}]}',
        model="claude-sonnet-4-6",
        tokens_input=10,
        tokens_output=5,
        pii_response={"detected": True, "total": 2, "types": {"PERSON": 2}, "response_filtered": True},
    )
    _run(
        _emit_cached_response(
            "api.anthropic.com", _req(False), _pre_cls(), writer, sink, time.monotonic(), 100, hit
        )
    )
    attrs = sink.records[0].attrs
    assert attrs["pii_detected"] is True
    assert attrs["pii_response_filtered"] is True
    assert attrs["pii"]["response"] == {"PERSON": 2}
    assert attrs["pii"]["total"] == 2
