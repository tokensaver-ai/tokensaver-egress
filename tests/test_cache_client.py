"""Egress cache HTTP client (egress.cache) — thin, policy-driven."""

from __future__ import annotations

import asyncio
import json

import tokensaver_egress.cache as cache_mod


def test_url_derivation():
    assert (
        cache_mod._derive_url("https://api.tokensaver.fr/api/v1/egress/ingest", "cache-lookup")
        == "https://api.tokensaver.fr/api/v1/egress/cache-lookup"
    )
    assert (
        cache_mod._derive_url("https://api.tokensaver.fr/api/v1/egress/ingest", "cache-policy")
        == "https://api.tokensaver.fr/api/v1/egress/cache-policy"
    )


def _client():
    # No enable flag: configured (urls + key) is enough; behavior is gated by the policy.
    return cache_mod.EgressCacheClient(
        policy_url="https://x/cache-policy",
        lookup_url="https://x/cache-lookup",
        store_url="https://x/cache-store",
        api_key="ts_key",
    )


def test_enabled_tracks_configuration():
    assert cache_mod.EgressCacheClient(lookup_url="", store_url="", api_key="").enabled is False
    assert _client().enabled is True


def test_lookup_returns_none_when_not_configured():
    c = cache_mod.EgressCacheClient(lookup_url="", store_url="", api_key="")
    out = asyncio.run(
        c.lookup(b'{"messages":[]}', provider="anthropic", model="m", flow_kind="llm", content_type="application/json")
    )
    assert out is None


def test_lookup_hit_writes_backend_synth(monkeypatch):
    c = _client()

    class PolicyResp:
        status_code = 200

        @staticmethod
        def json():
            return {"cache_enabled": True, "exact_cache": True, "semantic_cache": True}

    class LookupResp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "hit": True,
                "hit_type": "exact",
                "model": "claude-sonnet-4-6",
                "tokens_input": 1,
                "tokens_output": 2,
                "synth": {
                    "status": 200,
                    "content_type": "application/json",
                    "body": '{"content":[{"type":"text","text":"cached"}]}',
                },
            }

    async def _get(url, *, api_key, **kwargs):
        return PolicyResp()

    async def _post(url, *, api_key, **kwargs):
        return LookupResp()

    monkeypatch.setattr(cache_mod, "backend_get", _get)
    monkeypatch.setattr(cache_mod, "backend_post", _post)
    body = json.dumps({"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]}).encode()
    out = asyncio.run(
        c.lookup(body, provider="anthropic", model="claude-sonnet-4-6", flow_kind="llm", content_type="application/json")
    )
    assert out is not None
    assert out.hit is True
    assert out.synth_content_type == "application/json"
    assert out.synth_status == 200
    assert b"cached" in out.synth_body
    assert out.tokens_output == 2


def test_lookup_miss_reports_embedding_error(monkeypatch):
    c = _client()

    class PolicyResp:
        status_code = 200

        @staticmethod
        def json():
            return {"cache_enabled": True, "exact_cache": True, "semantic_cache": True}

    class LookupResp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "hit": False,
                "cache_evaluated": True,
                "error_code": "EMBEDDING_PROVIDER_KEY_MISSING",
                "embedding_error": "no mistral key",
            }

    async def _get(url, *, api_key, **kwargs):
        return PolicyResp()

    async def _post(url, *, api_key, **kwargs):
        return LookupResp()

    monkeypatch.setattr(cache_mod, "backend_get", _get)
    monkeypatch.setattr(cache_mod, "backend_post", _post)
    out = asyncio.run(
        c.lookup(b'{"messages":[]}', provider="anthropic", model="m", flow_kind="llm", content_type="application/json")
    )
    assert out is not None
    assert out.hit is False
    assert out.cache_evaluated is True
    assert out.error_code == "EMBEDDING_PROVIDER_KEY_MISSING"
    assert out.embedding_error == "no mistral key"


def test_lookup_skips_when_policy_disabled(monkeypatch):
    c = _client()
    posts = {"n": 0}

    class PolicyResp:
        status_code = 200

        @staticmethod
        def json():
            return {"cache_enabled": False}

    async def _get(url, *, api_key, **kwargs):
        return PolicyResp()

    async def _post(url, *, api_key, **kwargs):  # pragma: no cover
        posts["n"] += 1
        raise AssertionError("lookup must not POST when policy disables cache")

    monkeypatch.setattr(cache_mod, "backend_get", _get)
    monkeypatch.setattr(cache_mod, "backend_post", _post)
    out = asyncio.run(
        c.lookup(b'{"messages":[]}', provider="anthropic", model="m", flow_kind="llm", content_type="application/json")
    )
    assert out is None
    assert posts["n"] == 0


def test_lookup_skips_non_llm():
    c = _client()
    out = asyncio.run(
        c.lookup(b"{}", provider="anthropic", model="m", flow_kind="mcp", content_type="application/json")
    )
    assert out is None


def test_policy_ttl_caches_enabled_result(monkeypatch):
    c = _client()
    calls = {"n": 0}

    class PolicyResp:
        status_code = 200

        @staticmethod
        def json():
            return {"cache_enabled": True, "exact_cache": True, "semantic_cache": True}

    async def _get(url, *, api_key, **kwargs):
        calls["n"] += 1
        return PolicyResp()

    monkeypatch.setattr(cache_mod, "backend_get", _get)
    assert asyncio.run(c._fetch_policy()) is not None
    assert asyncio.run(c._fetch_policy()) is not None
    assert calls["n"] == 1
