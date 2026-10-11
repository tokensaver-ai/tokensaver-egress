"""Synchronous egress LLM pipeline (MITM) — parité orchestrateur pipeline.

Modèle **unité de flux + parallélisme inter-flux** :

- Chaque flux IA est traité **unitairement** : enchaînement synchrone
  Model routing → Cache → RAG → compression content-aware → PII requête → LLM → PII réponse.
- Plusieurs flux simultanés sont **parallélisés** : une tâche asyncio indépendante
  par session client ; les ``await`` backend d'un flux A n'empêchent pas le
  traitement du flux B (pool HTTP partagé, pas de verrou global pipeline).

Seuls ``cache.store`` et l'ingest audit restent best-effort async (sans impact
sur la réponse client).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from tokensaver_egress.anonymize import get_anonymizer
from tokensaver_egress.cache import get_cache_client
from tokensaver_egress.compression import get_compression_client
from tokensaver_egress.governance import (
    get_governance_snapshot,
    governance_preflight_needed,
    governance_response_pii_enabled,
    governance_routing_enabled,
    refresh_governance_snapshot,
)
from tokensaver_egress.llm_utility import is_client_utility_llm_request
from tokensaver_egress.rag import get_rag_client
from tokensaver_egress.routing import get_routing_client
from tokensaver_egress.response_filter import FilteredResponse, get_response_filter

logger = logging.getLogger("tokensaver-egress.pipeline")

_warm_task: asyncio.Task[None] | None = None


@dataclass
class EgressPreflightResult:
    """Result of the synchronous pre-upstream pipeline."""

    cache_result: Any | None = None
    rag_result: Any | None = None
    compression_result: Any | None = None
    anonymized_pii: dict[str, Any] | None = None
    pii_anonymized: bool = False
    cache_hit: bool = False
    request_bytes: int = 0
    elapsed_ms: float = 0.0
    modules_run: list[str] = field(default_factory=list)


async def warm_llm_policies() -> None:
    """Ensure governance snapshot is warm (coalesced single ``policies-bundle`` GET)."""
    global _warm_task
    if get_governance_snapshot() is not None:
        return
    if _warm_task is not None and not _warm_task.done():
        await _warm_task
        return

    async def _do_warm() -> None:
        await refresh_governance_snapshot()

    _warm_task = asyncio.create_task(_do_warm(), name="egress-warm-policies")
    await _warm_task


def reset_warm_state() -> None:
    """Test helper — allow re-warm after policy changes."""
    global _warm_task
    _warm_task = None
    from tokensaver_egress.governance import reset_governance_snapshot

    reset_governance_snapshot()


async def is_llm_governance_preflight_needed() -> bool:
    """Return True when any sync pre-upstream module may hit the backend or short-circuit."""
    cached = governance_preflight_needed()
    if cached is not None:
        return cached
    cache_client = get_cache_client()
    rag_client = get_rag_client()
    compression_client = get_compression_client()
    anonymizer = get_anonymizer()
    if not any(
        client.enabled for client in (cache_client, rag_client, compression_client, anonymizer)
    ):
        return False
    await warm_llm_policies()
    cached = governance_preflight_needed()
    if cached is not None:
        return cached
    cache_on, rag_on, compression_on, pii_on = await asyncio.gather(
        cache_client.cache_enabled(),
        rag_client.rag_enabled(),
        compression_client.compression_enabled(),
        anonymizer._pii_enabled(),
    )
    return bool(cache_on or rag_on or compression_on or pii_on)


async def response_pii_filter_active() -> bool:
    """Whether response-side PII buffering is required (uses governance snapshot when warm)."""
    from tokensaver_egress.response_filter import get_response_filter

    rf = get_response_filter()
    if not rf.enabled:
        return False
    cached = governance_response_pii_enabled()
    if cached is not None:
        return cached
    return bool(await rf._pii_enabled())


async def run_egress_llm_postflight(
    *,
    response_body: bytes,
    request_body: bytes | None,
    provider: str | None,
    model: str | None,
    tokens_input: int | None,
    tokens_output: int | None,
) -> FilteredResponse | None:
    """PII réponse synchrone pour un flux (post-upstream, avant émission client)."""
    response_filter = get_response_filter()
    if not response_filter.enabled or not await response_pii_filter_active():
        return None
    return await response_filter.filter(
        response_sample=response_body,
        request_body=request_body,
        provider=provider,
        model=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
    )


async def run_egress_llm_preflight(
    req: Any,
    *,
    req_cls: Any,
    host: str,
    raw_body_for_cache: bytes,
    request_bytes: int,
) -> EgressPreflightResult:
    """Run Cache → RAG → compression content-aware → PII requête before upstream LLM (pipeline parity)."""
    t0 = time.perf_counter()
    result = EgressPreflightResult(request_bytes=request_bytes)
    if is_client_utility_llm_request(
        req.path if req else None,
        body=getattr(req, "body", None),
        model=getattr(req_cls, "model", None),
    ):
        result.modules_run.append("utility_fast_path")
        result.elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return result

    cache_client = get_cache_client()

    if cache_client.enabled and raw_body_for_cache and await cache_client.cache_enabled():
        result.modules_run.append("cache")
        result.cache_result = await cache_client.lookup(
            raw_body_for_cache,
            provider=req_cls.provider,
            model=req_cls.model,
            flow_kind=req_cls.flow_kind,
            content_type=req.content_type,
            host=host,
            path=req.path if req else None,
            method=req_cls.method,
        )
        if result.cache_result and result.cache_result.hit:
            result.cache_hit = True
            result.elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return result

    rag_client = get_rag_client()
    rag_similarity_threshold: float | None = None
    rag_chunks_with_sources: list[dict] | None = None
    if rag_client.enabled and req.body and await rag_client.rag_enabled():
        result.modules_run.append("rag")
        result.rag_result = await rag_client.enrich(
            req.body,
            provider=req_cls.provider,
            model=req_cls.model,
            flow_kind=req_cls.flow_kind,
            content_type=req.content_type,
        )
        if result.rag_result and result.rag_result.modified and result.rag_result.body:
            _set_request_body(req, result.rag_result.body)
            result.request_bytes = len(req.body or b"") + sum(
                len(f"{k}: {v}\r\n") for k, v in (req.headers or {}).items()
            )
        rag_detail = getattr(result.rag_result, "detail", None) if result.rag_result else None
        if isinstance(rag_detail, dict):
            raw_thr = rag_detail.get("similarity_threshold")
            if raw_thr is not None:
                try:
                    rag_similarity_threshold = float(raw_thr)
                except (TypeError, ValueError):
                    rag_similarity_threshold = None
            retrieved = rag_detail.get("retrieved_chunks")
            if isinstance(retrieved, list) and retrieved:
                rag_chunks_with_sources = [c for c in retrieved if isinstance(c, dict)]

    compression_client = get_compression_client()
    if compression_client.enabled and req.body and await compression_client.compression_enabled():
        result.compression_result = await compression_client.compress_tool_outputs(
            req.body,
            provider=req_cls.provider,
            model=req_cls.model,
            flow_kind=req_cls.flow_kind,
            content_type=req.content_type,
            rag_similarity_threshold=rag_similarity_threshold,
            rag_chunks_with_sources=rag_chunks_with_sources,
        )
        if result.compression_result and getattr(result.compression_result, "evaluated", False):
            result.modules_run.append("compression")
        if (
            result.compression_result
            and result.compression_result.modified
            and result.compression_result.body
        ):
            _set_request_body(req, result.compression_result.body)
            result.request_bytes = len(req.body or b"") + sum(
                len(f"{k}: {v}\r\n") for k, v in (req.headers or {}).items()
            )

    anonymizer = get_anonymizer()
    if anonymizer.enabled and req.body and await anonymizer._pii_enabled():
        result.modules_run.append("pii")
        masked = await anonymizer.anonymize(
            req.body,
            provider=req_cls.provider,
            flow_kind=req_cls.flow_kind,
            content_type=req.content_type,
        )
        if masked is not None:
            masked_body, anonymized_pii = masked
            result.anonymized_pii = anonymized_pii
            result.pii_anonymized = True
            _set_request_body(req, masked_body)
            result.request_bytes = len(req.body or b"") + sum(
                len(f"{k}: {v}\r\n") for k, v in (req.headers or {}).items()
            )

    result.elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return result


def _set_request_body(req: Any, new_body: bytes) -> None:
    req.body = new_body
    headers = req.headers or {}
    for key in [k for k in headers if k.lower() == "transfer-encoding"]:
        headers.pop(key, None)
    cl_key = next((k for k in headers if k.lower() == "content-length"), None)
    if cl_key is not None:
        headers[cl_key] = str(len(new_body))
    else:
        headers["Content-Length"] = str(len(new_body))
    req.headers = headers
