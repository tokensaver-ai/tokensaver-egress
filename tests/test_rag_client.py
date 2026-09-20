"""Tests for egress proxy RAG client."""

from __future__ import annotations

import pytest

import tokensaver_egress.rag as rag_mod
from tokensaver_egress.rag import EgressRagClient


@pytest.mark.asyncio
async def test_rag_client_skips_when_policy_disabled(monkeypatch):
    client = EgressRagClient(
        policy_url="http://test/rag-policy",
        enrich_url="http://test/rag-enrich",
        api_key="ts_test",
        fail_open=True,
    )

    async def _policy(*_a, **_k):
        return type("P", (), {"rag_enabled": False})()

    monkeypatch.setattr(client, "_fetch_policy", _policy)
    out = await client.enrich(
        b'{"messages":[{"role":"user","content":"hi"}]}',
        provider="openai",
        model="m",
        flow_kind="llm",
        content_type="application/json",
    )
    assert out is None


@pytest.mark.asyncio
async def test_rag_client_enrich_modified(monkeypatch):
    client = EgressRagClient(
        policy_url="http://test/rag-policy",
        enrich_url="http://test/rag-enrich",
        api_key="ts_test",
        fail_open=True,
    )

    async def _policy(*_a, **_k):
        return type("P", (), {"rag_enabled": True})()

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "modified": True,
                "body": '{"messages":[{"role":"user","content":"<RAG documents>\\nx\\n\\nhi"}]}',
                "rag_evaluated": True,
                "chunks_count": 1,
                "detail": {
                    "has_similarity": True,
                    "chunks_count": 1,
                    "similarity_threshold": 0.5,
                    "top_k": 5,
                    "closest_similarity": 0.596045,
                },
            }

    async def _post(url, *, api_key, **kwargs):
        return _Resp()

    monkeypatch.setattr(client, "_fetch_policy", _policy)
    monkeypatch.setattr(rag_mod, "backend_post", _post)

    out = await client.enrich(
        b'{"messages":[{"role":"user","content":"hi"}]}',
        provider="openai",
        model="m",
        flow_kind="llm",
        content_type="application/json",
    )
    assert out is not None
    assert out.modified is True
    assert out.chunks_count == 1
    assert out.detail is not None
    assert out.detail["has_similarity"] is True
    assert b"<RAG documents>" in (out.body or b"")


@pytest.mark.asyncio
async def test_rag_policy_ttl_caches(monkeypatch):
    client = EgressRagClient(
        policy_url="http://test/rag-policy",
        enrich_url="http://test/rag-enrich",
        api_key="ts_test",
        fail_open=True,
    )
    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"rag_enabled": True}

    async def _get(url, *, api_key, **kwargs):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(rag_mod, "backend_get", _get)
    assert (await client._fetch_policy()).rag_enabled is True
    assert (await client._fetch_policy()).rag_enabled is True
    assert calls["n"] == 1
