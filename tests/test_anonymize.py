"""ACP-4 §4.5 — egress inline prompt anonymization client (egress.anonymize)."""

from __future__ import annotations

import asyncio
import json
import time

import tokensaver_egress.anonymize as anon


def _make(monkeypatch):
    """A fully configured anonymizer (has key + urls → ``enabled`` is True)."""
    return anon.PromptAnonymizer(
        anonymize_url="https://x/anonymize",
        policy_url="https://x/pii-policy",
        api_key="ts_x",
    )


def _enable_pii(a: anon.PromptAnonymizer) -> None:
    a._policy_enabled = True
    a._policy_expires_at = time.monotonic() + 3600.0


def test_url_derivation():
    assert (
        anon._derive_url("https://api.tokensaver.fr/api/v1/egress/ingest", "anonymize")
        == "https://api.tokensaver.fr/api/v1/egress/anonymize"
    )
    assert (
        anon._derive_url("https://api.tokensaver.fr/api/v1/egress/ingest", "pii-policy")
        == "https://api.tokensaver.fr/api/v1/egress/pii-policy"
    )
    assert anon._derive_url("https://x/egress", "anonymize") == "https://x/egress/anonymize"
    assert anon._derive_url("", "anonymize") == ""


def test_enabled_tracks_configuration():
    # No key/url → not able to anonymize (no separate feature flag any more).
    assert anon.PromptAnonymizer(anonymize_url="", policy_url="", api_key="").enabled is False
    assert (
        anon.PromptAnonymizer(
            anonymize_url="https://x/anonymize", policy_url="https://x/pii-policy", api_key="ts_x"
        ).enabled
        is True
    )


def test_returns_none_when_not_configured():
    a = anon.PromptAnonymizer(anonymize_url="", policy_url="", api_key="")
    out = asyncio.run(
        a.anonymize(b'{"messages":[]}', provider="anthropic", flow_kind="llm", content_type="application/json")
    )
    assert out is None


def test_skips_non_llm_flows(monkeypatch):
    a = _make(monkeypatch)
    _enable_pii(a)
    out = asyncio.run(
        a.anonymize(b'{"x":1}', provider="anthropic", flow_kind="mcp", content_type="application/json")
    )
    assert out is None


def test_skips_when_pii_policy_disabled(monkeypatch):
    a = _make(monkeypatch)
    calls = {"policy": 0, "post": 0}

    async def _policy():
        calls["policy"] += 1
        return False

    async def _should_not_run(*args, **kwargs):  # pragma: no cover
        calls["post"] += 1
        raise AssertionError("anonymize endpoint must not be called when PII disabled")

    monkeypatch.setattr(a, "_pii_enabled", _policy)
    # No body should be POSTed when policy says PII is off.
    out = asyncio.run(
        a.anonymize(b'{"messages":[]}', provider="anthropic", flow_kind="llm", content_type="application/json")
    )
    assert out is None
    assert calls["policy"] == 1
    assert calls["post"] == 0


def test_policy_ttl_caches_enabled_result(monkeypatch):
    """Within TTL, a second _pii_enabled() must not re-GET the backend."""
    a = _make(monkeypatch)
    calls = {"n": 0}

    async def _fetch():
        calls["n"] += 1
        return True

    monkeypatch.setattr(a, "_fetch_policy", _fetch)

    async def _run():
        first = await a._pii_enabled()
        second = await a._pii_enabled()
        return first, second

    first, second = asyncio.run(_run())
    assert first is True and second is True
    assert calls["n"] == 1


def test_policy_refetch_after_ttl_expires(monkeypatch):
    a = _make(monkeypatch)
    a._policy_enabled = False
    a._policy_expires_at = time.monotonic() - 1.0  # expired
    calls = {"n": 0}

    async def _fetch():
        calls["n"] += 1
        return True

    monkeypatch.setattr(a, "_fetch_policy", _fetch)
    assert asyncio.run(a._pii_enabled()) is True
    assert calls["n"] == 1


def test_policy_within_ttl_keeps_cached_disable(monkeypatch):
    a = _make(monkeypatch)
    a._policy_enabled = True
    a._policy_expires_at = time.monotonic() + 3600.0
    calls = {"n": 0}

    async def _fetch():
        calls["n"] += 1
        return False

    monkeypatch.setattr(a, "_fetch_policy", _fetch)
    assert asyncio.run(a._pii_enabled()) is True
    assert calls["n"] == 0


def test_returns_masked_segments(monkeypatch):
    a = _make(monkeypatch)
    _enable_pii(a)

    posted = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "enabled": True,
                "modified": True,
                "segments": [{"path": ["messages", 0, "content"], "text": "Hi [PERSON]"}],
                "pii": {"detected": True, "total": 1, "types": {"PERSON": 1}},
            }

    async def _post(url, *, api_key, **kwargs):
        posted.update(kwargs.get("json") or {})
        return _Resp()

    import tokensaver_egress.anonymize as anon_mod

    monkeypatch.setattr(anon_mod, "backend_post", _post)

    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "Hi John Doe"}]})
    out = asyncio.run(
        a.anonymize(
            body.encode(),
            provider="anthropic",
            flow_kind="llm",
            content_type="application/json",
        )
    )
    assert out is not None
    masked, pii = out
    parsed = json.loads(masked.decode())
    assert parsed["messages"][0]["content"] == "Hi [PERSON]"
    assert pii["total"] == 1
    assert "segments" in posted


def test_fastpath_skips_backend_for_salut(monkeypatch):
    a = _make(monkeypatch)
    _enable_pii(a)
    called = {"post": 0}

    async def _post(*args, **k):
        called["post"] += 1
        raise AssertionError("should not POST")

    import tokensaver_egress.anonymize as anon_mod

    monkeypatch.setattr(anon_mod, "backend_post", _post)

    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "salut"}]})
    out = asyncio.run(
        a.anonymize(body.encode(), provider="anthropic", flow_kind="llm", content_type="application/json")
    )
    assert out is None
    assert called["post"] == 0


def test_fail_open_on_backend_error(monkeypatch):
    a = _make(monkeypatch)
    _enable_pii(a)

    async def _post(*a, **k):
        raise RuntimeError("backend down")

    import tokensaver_egress.anonymize as anon_mod

    monkeypatch.setattr(anon_mod, "backend_post", _post)

    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "Hi John Doe"}]})
    out = asyncio.run(
        a.anonymize(body.encode(), provider="anthropic", flow_kind="llm", content_type="application/json")
    )
    assert out is None


def test_not_modified_returns_none(monkeypatch):
    a = _make(monkeypatch)
    _enable_pii(a)

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"enabled": True, "modified": False}

    async def _post(*a, **k):
        return _Resp()

    import tokensaver_egress.anonymize as anon_mod

    monkeypatch.setattr(anon_mod, "backend_post", _post)

    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "Hi John Doe"}]})
    out = asyncio.run(
        a.anonymize(body.encode(), provider="anthropic", flow_kind="llm", content_type="application/json")
    )
    assert out is None
