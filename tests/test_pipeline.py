"""Synchronous egress LLM preflight pipeline."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from tokensaver_egress.pipeline import run_egress_llm_preflight


def _req(body: bytes):
    req = MagicMock()
    req.body = body
    req.headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    req.path = "/v1/messages"
    req.content_type = "application/json"
    return req


def _req_cls(model: str = "claude-sonnet-4-6"):
    cls = MagicMock()
    cls.provider = "anthropic"
    cls.model = model
    cls.flow_kind = "llm"
    cls.method = "POST"
    return cls


def test_preflight_runs_cache_rag_pii_compression_in_order():
    body = json.dumps({"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = _req(body)
    order: list[str] = []

    async def _lookup(*a, **k):
        order.append("cache")
        return MagicMock(hit=False, cache_evaluated=True)

    async def _enrich(*a, **k):
        order.append("rag")
        return MagicMock(modified=False, rag_evaluated=True)

    async def _anonymize(*a, **k):
        order.append("pii")
        return None

    async def _compress(*a, **k):
        order.append("compression")
        return MagicMock(modified=False, evaluated=True)

    with (
        patch("tokensaver_egress.pipeline.get_cache_client") as cache_mock,
        patch("tokensaver_egress.pipeline.get_rag_client") as rag_mock,
        patch("tokensaver_egress.pipeline.get_anonymizer") as anon_mock,
        patch("tokensaver_egress.pipeline.get_compression_client") as comp_mock,
    ):
        cache_mock.return_value.enabled = True
        cache_mock.return_value.cache_enabled = AsyncMock(return_value=True)
        cache_mock.return_value.lookup = AsyncMock(side_effect=_lookup)
        rag_mock.return_value.enabled = True
        rag_mock.return_value.rag_enabled = AsyncMock(return_value=True)
        rag_mock.return_value.enrich = AsyncMock(side_effect=_enrich)
        anon_mock.return_value.enabled = True
        anon_mock.return_value._pii_enabled = AsyncMock(return_value=True)
        anon_mock.return_value.anonymize = AsyncMock(side_effect=_anonymize)
        comp_mock.return_value.enabled = True
        comp_mock.return_value.compression_enabled = AsyncMock(return_value=True)
        comp_mock.return_value.compress_tool_outputs = AsyncMock(side_effect=_compress)

        result = asyncio.run(
            run_egress_llm_preflight(
                req,
                req_cls=_req_cls(),
                host="api.anthropic.com",
                raw_body_for_cache=body,
                request_bytes=100,
            )
        )

    # Pipeline parity: Cache → RAG → compression → PII (request) → LLM
    assert order == ["cache", "rag", "compression", "pii"]
    assert result.cache_hit is False
    assert "cache" in result.modules_run
    assert "rag" in result.modules_run
    assert "pii" in result.modules_run
    assert "compression" in result.modules_run


def test_preflight_runs_cache_rag_pii_in_order():
    body = json.dumps({"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = _req(body)
    order: list[str] = []

    async def _lookup(*a, **k):
        order.append("cache")
        return MagicMock(hit=False, cache_evaluated=True)

    async def _enrich(*a, **k):
        order.append("rag")
        return MagicMock(modified=False, rag_evaluated=True)

    async def _anonymize(*a, **k):
        order.append("pii")
        return None

    with (
        patch("tokensaver_egress.pipeline.get_cache_client") as cache_mock,
        patch("tokensaver_egress.pipeline.get_rag_client") as rag_mock,
        patch("tokensaver_egress.pipeline.get_anonymizer") as anon_mock,
        patch("tokensaver_egress.pipeline.get_compression_client") as comp_mock,
    ):
        cache_mock.return_value.enabled = True
        cache_mock.return_value.cache_enabled = AsyncMock(return_value=True)
        cache_mock.return_value.lookup = AsyncMock(side_effect=_lookup)
        rag_mock.return_value.enabled = True
        rag_mock.return_value.rag_enabled = AsyncMock(return_value=True)
        rag_mock.return_value.enrich = AsyncMock(side_effect=_enrich)
        anon_mock.return_value.enabled = True
        anon_mock.return_value._pii_enabled = AsyncMock(return_value=True)
        anon_mock.return_value.anonymize = AsyncMock(side_effect=_anonymize)
        comp_mock.return_value.enabled = False

        result = asyncio.run(
            run_egress_llm_preflight(
                req,
                req_cls=_req_cls(),
                host="api.anthropic.com",
                raw_body_for_cache=body,
                request_bytes=100,
            )
        )

    assert order == ["cache", "rag", "pii"]
    assert result.cache_hit is False
    assert "cache" in result.modules_run
    assert "rag" in result.modules_run
    assert "pii" in result.modules_run


def test_preflight_stops_on_cache_hit():
    body = b"{}"
    req = _req(body)
    hit = MagicMock(hit=True, hit_type="exact")

    with patch("tokensaver_egress.pipeline.get_cache_client") as cache_mock:
        cache_mock.return_value.enabled = True
        cache_mock.return_value.cache_enabled = AsyncMock(return_value=True)
        cache_mock.return_value.lookup = AsyncMock(return_value=hit)

        result = asyncio.run(
            run_egress_llm_preflight(
                req,
                req_cls=_req_cls(),
                host="api.anthropic.com",
                raw_body_for_cache=body,
                request_bytes=50,
            )
        )

    assert result.cache_hit is True
    assert result.cache_result is hit
    assert result.modules_run == ["cache"]


def test_preflight_skips_pii_module_when_policy_disabled():
    body = json.dumps({"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = _req(body)

    with (
        patch("tokensaver_egress.pipeline.get_cache_client") as cache_mock,
        patch("tokensaver_egress.pipeline.get_rag_client") as rag_mock,
        patch("tokensaver_egress.pipeline.get_anonymizer") as anon_mock,
        patch("tokensaver_egress.pipeline.get_compression_client") as comp_mock,
    ):
        cache_mock.return_value.enabled = False
        rag_mock.return_value.enabled = False
        anon_mock.return_value.enabled = True
        anon_mock.return_value._pii_enabled = AsyncMock(return_value=False)
        anon_mock.return_value.anonymize = AsyncMock(return_value=None)
        comp_mock.return_value.enabled = False

        result = asyncio.run(
            run_egress_llm_preflight(
                req,
                req_cls=_req_cls(),
                host="api.anthropic.com",
                raw_body_for_cache=body,
                request_bytes=100,
            )
        )

    assert "pii" not in result.modules_run
    anon_mock.return_value.anonymize.assert_not_called()


def test_preflight_utility_fast_path_skips_cache_rag_compression_pii():
    body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 16}).encode()
    req = _req(body)
    req.path = "/v1/messages?beta=true"

    with (
        patch("tokensaver_egress.pipeline.get_cache_client") as cache_mock,
        patch("tokensaver_egress.pipeline.get_rag_client") as rag_mock,
        patch("tokensaver_egress.pipeline.get_anonymizer") as anon_mock,
        patch("tokensaver_egress.pipeline.get_compression_client") as comp_mock,
    ):
        cache_mock.return_value.enabled = True
        cache_mock.return_value.lookup = AsyncMock()
        rag_mock.return_value.enabled = True
        rag_mock.return_value.enrich = AsyncMock()
        anon_mock.return_value.enabled = True
        anon_mock.return_value._pii_enabled = AsyncMock(return_value=True)
        comp_mock.return_value.enabled = True

        result = asyncio.run(
            run_egress_llm_preflight(
                req,
                req_cls=_req_cls(model="claude-haiku-4-5-20251001"),
                host="api.anthropic.com",
                raw_body_for_cache=body,
                request_bytes=100,
            )
        )

    assert result.modules_run == ["utility_fast_path"]
    cache_mock.return_value.lookup.assert_not_called()
    rag_mock.return_value.enrich.assert_not_called()
    anon_mock.return_value.anonymize.assert_not_called()
    comp_mock.return_value.compress_tool_outputs.assert_not_called()


def test_preflight_beta_sonnet_main_chat_runs_cache_rag():
    """Main agent on ?beta=true must not take the utility fast-path."""
    body = json.dumps({"model": "claude-sonnet-5", "max_tokens": 16000}).encode()
    req = _req(body)
    req.path = "/v1/messages?beta=true"

    with (
        patch("tokensaver_egress.pipeline.get_cache_client") as cache_mock,
        patch("tokensaver_egress.pipeline.get_rag_client") as rag_mock,
        patch("tokensaver_egress.pipeline.get_anonymizer") as anon_mock,
        patch("tokensaver_egress.pipeline.get_compression_client") as comp_mock,
    ):
        cache_mock.return_value.enabled = True
        cache_mock.return_value.cache_enabled = AsyncMock(return_value=True)
        cache_mock.return_value.lookup = AsyncMock(return_value=None)
        rag_mock.return_value.enabled = True
        rag_mock.return_value.rag_enabled = AsyncMock(return_value=True)
        rag_mock.return_value.enrich = AsyncMock(return_value=None)
        anon_mock.return_value.enabled = False
        anon_mock.return_value._pii_enabled = AsyncMock(return_value=False)
        comp_mock.return_value.enabled = False

        result = asyncio.run(
            run_egress_llm_preflight(
                req,
                req_cls=_req_cls(model="claude-sonnet-5"),
                host="api.anthropic.com",
                raw_body_for_cache=body,
                request_bytes=100,
            )
        )

    assert "utility_fast_path" not in result.modules_run
    assert "cache" in result.modules_run
    assert "rag" in result.modules_run
    cache_mock.return_value.lookup.assert_awaited()
    rag_mock.return_value.enrich.assert_awaited()


def test_preflight_skips_cache_rag_when_policies_disabled():
    body = json.dumps({"model": "claude-sonnet-5", "messages": []}).encode()
    req = _req(body)

    with (
        patch("tokensaver_egress.pipeline.get_cache_client") as cache_mock,
        patch("tokensaver_egress.pipeline.get_rag_client") as rag_mock,
        patch("tokensaver_egress.pipeline.get_anonymizer") as anon_mock,
        patch("tokensaver_egress.pipeline.get_compression_client") as comp_mock,
    ):
        cache_mock.return_value.enabled = True
        cache_mock.return_value.cache_enabled = AsyncMock(return_value=False)
        cache_mock.return_value.lookup = AsyncMock()
        rag_mock.return_value.enabled = True
        rag_mock.return_value.rag_enabled = AsyncMock(return_value=False)
        rag_mock.return_value.enrich = AsyncMock()
        anon_mock.return_value.enabled = True
        anon_mock.return_value._pii_enabled = AsyncMock(return_value=False)
        comp_mock.return_value.enabled = True
        comp_mock.return_value.compression_enabled = AsyncMock(return_value=False)
        comp_mock.return_value.compress_tool_outputs = AsyncMock()

        result = asyncio.run(
            run_egress_llm_preflight(
                req,
                req_cls=_req_cls(model="claude-sonnet-5"),
                host="api.anthropic.com",
                raw_body_for_cache=body,
                request_bytes=100,
            )
        )

    assert result.modules_run == []
    cache_mock.return_value.lookup.assert_not_called()
    rag_mock.return_value.enrich.assert_not_called()
    comp_mock.return_value.compress_tool_outputs.assert_not_called()

