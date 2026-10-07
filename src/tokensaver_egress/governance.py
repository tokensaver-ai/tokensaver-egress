"""Unified egress governance snapshot — one backend round-trip, O(1) hot-path checks."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

from tokensaver_egress.http_backend import backend_get

logger = logging.getLogger("tokensaver-egress.governance")

_DEFAULT_POLICY_TTL_S = 120.0


def _derive_url(ingest_url: str, suffix: str) -> str:
    base = (ingest_url or "").strip()
    if base.endswith("/ingest"):
        return base[: -len("/ingest")] + "/" + suffix
    return base.rstrip("/") + "/" + suffix if base else ""


@dataclass(frozen=True)
class GovernanceSnapshot:
    cache_enabled: bool = False
    exact_cache: bool = True
    semantic_cache: bool = True
    rag_enabled: bool = False
    compression_enabled: bool = False
    pii_enabled: bool = False
    routing_enabled: bool = False
    force_provider: str | None = None
    force_model: str | None = None
    force_region: str | None = None

    @property
    def any_preflight_active(self) -> bool:
        return self.cache_enabled or self.rag_enabled or self.compression_enabled or self.pii_enabled


_snapshot: GovernanceSnapshot | None = None
_snapshot_expires_at: float = 0.0
_refresh_task: asyncio.Task[GovernanceSnapshot | None] | None = None


def get_governance_snapshot() -> GovernanceSnapshot | None:
    """Return cached snapshot when still valid (no I/O)."""
    if _snapshot is not None and _snapshot_expires_at > time.monotonic():
        return _snapshot
    return None


def governance_preflight_needed() -> bool | None:
    """Synchronous preflight gate when snapshot is warm; ``None`` if unknown."""
    snap = get_governance_snapshot()
    if snap is None:
        return None
    return snap.any_preflight_active


def governance_routing_enabled() -> bool | None:
    snap = get_governance_snapshot()
    if snap is None:
        return None
    return snap.routing_enabled


def governance_response_pii_enabled() -> bool | None:
    snap = get_governance_snapshot()
    if snap is None:
        return None
    return snap.pii_enabled


def _policy_ttl_s() -> float:
    raw = (os.environ.get("EGRESS_POLICY_TTL_S") or "").strip()
    if raw:
        try:
            return max(5.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_POLICY_TTL_S


def _snapshot_from_payload(data: dict) -> GovernanceSnapshot:
    return GovernanceSnapshot(
        cache_enabled=bool(data.get("cache_enabled")),
        exact_cache=bool(data.get("exact_cache", True)),
        semantic_cache=bool(data.get("semantic_cache", True)),
        rag_enabled=bool(data.get("rag_enabled")),
        compression_enabled=bool(data.get("compression_enabled")),
        pii_enabled=bool(data.get("pii_enabled")),
        routing_enabled=bool(data.get("routing_enabled")),
        force_provider=data.get("force_provider"),
        force_model=data.get("force_model"),
        force_region=data.get("force_region"),
    )


def apply_governance_snapshot(snap: GovernanceSnapshot, *, ttl_s: float | None = None) -> None:
    """Push snapshot into all egress policy clients (skip per-module GETs on hot path)."""
    global _snapshot, _snapshot_expires_at
    ttl = ttl_s if ttl_s is not None else _policy_ttl_s()
    expires = time.monotonic() + ttl
    _snapshot = snap
    _snapshot_expires_at = expires

    from tokensaver_egress.cache import CachePolicy, get_cache_client
    from tokensaver_egress.compression import CompressionPolicy, get_compression_client
    from tokensaver_egress.rag import RagPolicy, get_rag_client
    from tokensaver_egress.routing import ModelRoutingPolicy, get_routing_client

    cache = get_cache_client()
    cache._policy = CachePolicy(  # noqa: SLF001 — intentional shared snapshot
        cache_enabled=snap.cache_enabled,
        exact_cache=snap.exact_cache,
        semantic_cache=snap.semantic_cache,
    )
    cache._policy_expires_at = expires

    rag = get_rag_client()
    rag._policy = RagPolicy(rag_enabled=snap.rag_enabled)
    rag._policy_expires_at = expires

    comp = get_compression_client()
    comp._policy = CompressionPolicy(compression_enabled=snap.compression_enabled)
    comp._policy_expires_at = expires

    routing = get_routing_client()
    routing._policy = ModelRoutingPolicy(
        routing_enabled=snap.routing_enabled,
        force_provider=snap.force_provider,
        force_model=snap.force_model,
        force_region=snap.force_region,
    )
    routing._policy_expires_at = expires

    anon = __import__("tokensaver_egress.anonymize", fromlist=["get_anonymizer"]).get_anonymizer()
    anon._policy_enabled = snap.pii_enabled
    anon._policy_expires_at = expires


async def _legacy_warm_all_clients() -> GovernanceSnapshot:
    """Fallback when backend has no ``policies-bundle`` (6 parallel GETs)."""
    from tokensaver_egress.anonymize import get_anonymizer
    from tokensaver_egress.cache import get_cache_client
    from tokensaver_egress.compression import get_compression_client
    from tokensaver_egress.rag import get_rag_client
    from tokensaver_egress.response_filter import get_response_filter
    from tokensaver_egress.routing import get_routing_client

    cache_client = get_cache_client()
    rag_client = get_rag_client()
    compression_client = get_compression_client()
    routing_client = get_routing_client()
    anonymizer = get_anonymizer()
    response_filter = get_response_filter()
    await asyncio.gather(
        cache_client.warm_policy() if cache_client.enabled else asyncio.sleep(0),
        rag_client.warm_policy() if rag_client.enabled else asyncio.sleep(0),
        compression_client.warm_policy() if compression_client.enabled else asyncio.sleep(0),
        routing_client.warm_policy() if routing_client.enabled else asyncio.sleep(0),
        anonymizer.warm_policy() if anonymizer.enabled else asyncio.sleep(0),
        response_filter.warm_policy() if response_filter.enabled else asyncio.sleep(0),
    )
    snap = GovernanceSnapshot(
        cache_enabled=await cache_client.cache_enabled() if cache_client.enabled else False,
        rag_enabled=await rag_client.rag_enabled() if rag_client.enabled else False,
        compression_enabled=await compression_client.compression_enabled()
        if compression_client.enabled
        else False,
        pii_enabled=await anonymizer._pii_enabled() if anonymizer.enabled else False,
        routing_enabled=await routing_client.routing_enabled() if routing_client.enabled else False,
    )
    apply_governance_snapshot(snap)
    return snap


async def refresh_governance_snapshot(*, force: bool = False) -> GovernanceSnapshot | None:
    """Fetch ``GET …/policies-bundle`` once and fan out to all module caches."""
    global _refresh_task, _snapshot, _snapshot_expires_at

    if not force:
        cached = get_governance_snapshot()
        if cached is not None:
            return cached

    if _refresh_task is not None and not _refresh_task.done():
        return await _refresh_task

    async def _do_refresh() -> GovernanceSnapshot | None:
        ingest_url = os.environ.get("TOKENSAVER_INGEST_URL", "")
        api_key = os.environ.get("TOKENSAVER_API_KEY", "")
        bundle_url = _derive_url(ingest_url, "policies-bundle")
        if not bundle_url or not api_key:
            idle = GovernanceSnapshot()
            apply_governance_snapshot(idle, ttl_s=30.0)
            return idle
        try:
            resp = await backend_get(bundle_url, api_key=api_key)
            if resp.status_code == 404:
                logger.debug("policies-bundle unavailable — legacy warm")
                return await _legacy_warm_all_clients()
            if resp.status_code != 200:
                if resp.status_code == 401:
                    logger.warning(
                        "policies-bundle 401 Unauthorized — check TOKENSAVER_API_KEY "
                        "(invalid key, or accidental double-paste in ~/.tokensaver-egress/env). "
                        "Re-run: tokensaver-egress setup"
                    )
                else:
                    logger.debug("policies-bundle HTTP %s", resp.status_code)
                return get_governance_snapshot()
            snap = _snapshot_from_payload(resp.json())
            apply_governance_snapshot(snap)
            logger.debug(
                "governance snapshot refreshed cache=%s rag=%s compression=%s pii=%s routing=%s",
                snap.cache_enabled,
                snap.rag_enabled,
                snap.compression_enabled,
                snap.pii_enabled,
                snap.routing_enabled,
            )
            return snap
        except Exception:
            logger.debug("policies-bundle fetch failed", exc_info=True)
            if _snapshot is not None:
                return _snapshot
            idle = GovernanceSnapshot()
            apply_governance_snapshot(idle, ttl_s=15.0)
            return idle

    _refresh_task = asyncio.create_task(_do_refresh(), name="egress-governance-refresh")
    try:
        return await _refresh_task
    finally:
        if _refresh_task is not None and _refresh_task.done():
            _refresh_task = None


def reset_governance_snapshot() -> None:
    global _snapshot, _snapshot_expires_at, _refresh_task
    _snapshot = None
    _snapshot_expires_at = 0.0
    _refresh_task = None
