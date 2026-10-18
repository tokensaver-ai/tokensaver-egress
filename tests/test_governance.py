"""Governance snapshot — single policies-bundle fetch."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import tokensaver_egress.governance as gov
from tokensaver_egress.governance import (
    GovernanceSnapshot,
    apply_governance_snapshot,
    governance_preflight_needed,
    governance_routing_enabled,
    refresh_governance_snapshot,
    reset_governance_snapshot,
)


def test_apply_snapshot_sets_sync_gates():
    reset_governance_snapshot()
    snap = GovernanceSnapshot(
        cache_enabled=False,
        rag_enabled=False,
        compression_enabled=False,
        pii_enabled=False,
        routing_enabled=True,
        force_provider="openrouter",
        force_model="stealth/ox-alpha",
    )
    apply_governance_snapshot(snap)
    assert governance_preflight_needed() is False
    assert governance_routing_enabled() is True


def test_refresh_bundle_single_http_call(monkeypatch):
    reset_governance_snapshot()
    monkeypatch.setenv("TOKENSAVER_INGEST_URL", "http://localhost:8000/api/v1/egress/ingest")
    monkeypatch.setenv("TOKENSAVER_API_KEY", "ts_test")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "cache_enabled": True,
                "rag_enabled": False,
                "compression_enabled": False,
                "pii_enabled": False,
                "routing_enabled": False,
            }

    with patch("tokensaver_egress.governance.backend_get", new=AsyncMock(return_value=_Resp())) as get_mock:

        async def _run():
            snap = await refresh_governance_snapshot()
            await refresh_governance_snapshot()
            return snap

        snap = asyncio.run(_run())

    assert snap is not None
    assert snap.cache_enabled is True
    assert governance_preflight_needed() is True
    assert get_mock.await_count == 1


def test_refresh_coalesces_parallel_waiters(monkeypatch):
    reset_governance_snapshot()
    monkeypatch.setenv("TOKENSAVER_INGEST_URL", "http://localhost:8000/api/v1/egress/ingest")
    monkeypatch.setenv("TOKENSAVER_API_KEY", "ts_test")
    calls = 0

    async def _slow_get(*a, **k):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"cache_enabled": False, "rag_enabled": False}

        return _Resp()

    with patch("tokensaver_egress.governance.backend_get", side_effect=_slow_get):

        async def _run():
            await asyncio.gather(refresh_governance_snapshot(), refresh_governance_snapshot())

        asyncio.run(_run())

    assert calls == 1
