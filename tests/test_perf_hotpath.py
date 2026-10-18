"""Hot-path latency guards for egress MITM (policies idle / observe mode)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

from tokensaver_egress.governance import (
    GovernanceSnapshot,
    apply_governance_snapshot,
    governance_preflight_needed,
    reset_governance_snapshot,
)
from tokensaver_egress.pipeline import (
    is_llm_governance_preflight_needed,
    reset_warm_state,
    warm_llm_policies,
)


def test_governance_idle_preflight_gate_is_instant():
    reset_governance_snapshot()
    apply_governance_snapshot(GovernanceSnapshot())

    t0 = time.perf_counter()
    for _ in range(5000):
        assert governance_preflight_needed() is False
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 50.0


def test_warm_after_snapshot_does_not_refetch():
    reset_governance_snapshot()
    reset_warm_state()
    apply_governance_snapshot(GovernanceSnapshot())

    with patch("tokensaver_egress.pipeline.refresh_governance_snapshot", new=AsyncMock()) as refresh_mock:
        asyncio.run(warm_llm_policies())
        asyncio.run(warm_llm_policies())

    refresh_mock.assert_not_called()


def test_is_preflight_needed_no_backend_when_idle():
    reset_governance_snapshot()
    reset_warm_state()
    apply_governance_snapshot(GovernanceSnapshot())

    with (
        patch("tokensaver_egress.pipeline.get_cache_client") as cache_mock,
        patch("tokensaver_egress.pipeline.get_rag_client") as rag_mock,
        patch("tokensaver_egress.pipeline.get_anonymizer") as anon_mock,
        patch("tokensaver_egress.pipeline.get_compression_client") as comp_mock,
    ):
        cache_mock.return_value.enabled = True
        cache_mock.return_value.cache_enabled = AsyncMock()
        rag_mock.return_value.enabled = True
        rag_mock.return_value.rag_enabled = AsyncMock()
        anon_mock.return_value.enabled = True
        anon_mock.return_value._pii_enabled = AsyncMock()
        comp_mock.return_value.enabled = True
        comp_mock.return_value.compression_enabled = AsyncMock()

        assert asyncio.run(is_llm_governance_preflight_needed()) is False

    cache_mock.return_value.cache_enabled.assert_not_called()
    rag_mock.return_value.rag_enabled.assert_not_called()
