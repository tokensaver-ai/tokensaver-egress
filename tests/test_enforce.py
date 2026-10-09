"""ACP-4 enforce — egress pre-flight policy client (egress.enforce)."""

from __future__ import annotations

import asyncio

import tokensaver_egress.enforce as enforce


def test_enforce_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EGRESS_ENFORCE_ENABLED", raising=False)
    assert enforce.enforce_enabled() is False


def test_authorize_url_derivation():
    assert (
        enforce._authorize_url("https://api.tokensaver.fr/api/v1/egress/ingest")
        == "https://api.tokensaver.fr/api/v1/egress/authorize"
    )
    assert enforce._authorize_url("https://x/egress") == "https://x/egress/authorize"
    assert enforce._authorize_url("") == ""


def test_authorize_allows_when_disabled(monkeypatch):
    monkeypatch.delenv("EGRESS_ENFORCE_ENABLED", raising=False)
    e = enforce.PolicyEnforcer(authorize_url="https://x/authorize", api_key="ts_x")
    assert e.enabled is False
    assert asyncio.run(e.authorize(flow_kind="llm", provider="anthropic", model="m", host="h")) is True


def test_authorize_denies_and_caches(monkeypatch):
    monkeypatch.setenv("EGRESS_ENFORCE_ENABLED", "1")
    e = enforce.PolicyEnforcer(authorize_url="https://x/authorize", api_key="ts_x")
    e.enabled = True
    calls = {"n": 0}

    async def _fake_fetch(flow_kind, provider, model, host):
        calls["n"] += 1
        return False  # deny

    monkeypatch.setattr(e, "_fetch_decision", _fake_fetch)

    async def _run():
        first = await e.authorize(flow_kind="llm", provider="anthropic", model="m", host="h")
        # Second identical call is served from cache (no extra fetch).
        second = await e.authorize(flow_kind="llm", provider="anthropic", model="m", host="h")
        return first, second

    first, second = asyncio.run(_run())
    assert first is False
    assert second is False
    assert calls["n"] == 1


def test_deny_uses_short_ttl(monkeypatch):
    monkeypatch.setenv("EGRESS_ENFORCE_ENABLED", "1")
    monkeypatch.delenv("EGRESS_ENFORCE_DENY_TTL_S", raising=False)
    e = enforce.PolicyEnforcer(authorize_url="https://x/authorize", api_key="ts_x")
    # Deny TTL must be much shorter than the allow TTL so re-approval is fast.
    assert e.deny_ttl_s < e.ttl_s
    assert e.deny_ttl_s <= 5.0


def test_clear_cache_forces_refetch(monkeypatch):
    monkeypatch.setenv("EGRESS_ENFORCE_ENABLED", "1")
    e = enforce.PolicyEnforcer(authorize_url="https://x/authorize", api_key="ts_x")
    e.enabled = True
    calls = {"n": 0}

    async def _fake_fetch(flow_kind, provider, model, host):
        calls["n"] += 1
        return False

    monkeypatch.setattr(e, "_fetch_decision", _fake_fetch)

    async def _run():
        await e.authorize(flow_kind="llm", provider="anthropic", model="m", host="h")
        e.clear_cache()
        await e.authorize(flow_kind="llm", provider="anthropic", model="m", host="h")

    asyncio.run(_run())
    assert calls["n"] == 2


def test_authorize_fail_open_on_misconfig(monkeypatch):
    monkeypatch.setenv("EGRESS_ENFORCE_ENABLED", "1")
    # Enabled but no url/key → must not silently block.
    e = enforce.PolicyEnforcer(authorize_url="", api_key="")
    e.enabled = True
    assert asyncio.run(e.authorize(flow_kind="llm", provider="p", model="m", host="h")) is True
