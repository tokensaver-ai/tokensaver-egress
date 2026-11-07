"""Concurrency: independent egress flows must not block each other."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from tokensaver_egress.pipeline import run_egress_llm_preflight, warm_llm_policies


def _req():
    req = MagicMock()
    req.body = b'{"messages":[]}'
    req.headers = {"Content-Length": "16"}
    req.path = "/v1/messages"
    req.content_type = "application/json"
    return req


def _req_cls():
    cls = MagicMock()
    cls.provider = "anthropic"
    cls.model = "claude-sonnet-4-6"
    cls.flow_kind = "llm"
    cls.method = "POST"
    return cls


def test_two_preflight_runs_execute_in_parallel():
    started: list[float] = []
    released = asyncio.Event()

    async def _slow_lookup(*a, **k):
        started.append(time.perf_counter())
        await released.wait()
        return MagicMock(hit=False, cache_evaluated=True)

    with (
        patch("tokensaver_egress.pipeline.get_cache_client") as cache_mock,
        patch("tokensaver_egress.pipeline.get_rag_client") as rag_mock,
        patch("tokensaver_egress.pipeline.get_anonymizer") as anon_mock,
    ):
        cache_mock.return_value.enabled = True
        cache_mock.return_value.cache_enabled = AsyncMock(return_value=True)
        cache_mock.return_value.lookup = AsyncMock(side_effect=_slow_lookup)
        rag_mock.return_value.enabled = False
        anon_mock.return_value.enabled = False

        async def _run_pair():
            t0 = time.perf_counter()
            await asyncio.gather(
                run_egress_llm_preflight(
                    _req(),
                    req_cls=_req_cls(),
                    host="api.anthropic.com",
                    raw_body_for_cache=b"x",
                    request_bytes=10,
                ),
                run_egress_llm_preflight(
                    _req(),
                    req_cls=_req_cls(),
                    host="api.anthropic.com",
                    raw_body_for_cache=b"y",
                    request_bytes=10,
                ),
            )
            return time.perf_counter() - t0

        async def _main():
            task = asyncio.create_task(_run_pair())
            await asyncio.sleep(0.05)
            assert len(started) == 2
            released.set()
            return await task

        elapsed = asyncio.run(_main())

    assert len(started) == 2
    assert elapsed < 0.5


def test_warm_policies_coalesced_across_parallel_flows():
    from tokensaver_egress.governance import reset_governance_snapshot
    from tokensaver_egress.pipeline import reset_warm_state

    reset_governance_snapshot()
    reset_warm_state()
    calls = 0

    async def _count_refresh(*a, **k):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        from tokensaver_egress.governance import GovernanceSnapshot, apply_governance_snapshot

        apply_governance_snapshot(GovernanceSnapshot())

    with patch("tokensaver_egress.pipeline.refresh_governance_snapshot", side_effect=_count_refresh):

        async def _main():
            await asyncio.gather(warm_llm_policies(), warm_llm_policies(), warm_llm_policies())
            await warm_llm_policies()

        asyncio.run(_main())

    assert calls == 1


def test_warm_policies_skips_when_snapshot_warm():
    from tokensaver_egress.governance import GovernanceSnapshot, apply_governance_snapshot, reset_governance_snapshot
    from tokensaver_egress.pipeline import reset_warm_state

    reset_governance_snapshot()
    reset_warm_state()
    apply_governance_snapshot(GovernanceSnapshot())
    calls = 0

    async def _count_refresh(*a, **k):
        nonlocal calls
        calls += 1

    with patch("tokensaver_egress.pipeline.refresh_governance_snapshot", side_effect=_count_refresh):
        asyncio.run(warm_llm_policies())
        asyncio.run(warm_llm_policies())

    assert calls == 0
