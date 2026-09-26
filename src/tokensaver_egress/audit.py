"""Egress audit buffer + batched shipping to the TokenSaver SaaS (ACP-4)."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("tokensaver-egress.audit")


@dataclass
class EgressRecord:
    host: str | None = None
    capture_mode: str = "tunnel"
    request_bytes: int | None = None
    response_bytes: int | None = None
    latency_ms: int | None = None
    status_code: int | None = None
    execution_trace_id: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


def record_to_ingest_dict(rec: EgressRecord) -> dict[str, Any]:
    """Map local record → SaaS ingest payload.

    Classified metadata is always forwarded. Full request/response bodies and
    headers are forwarded only when payload capture is enabled (the proxy only
    attaches them to ``attrs`` in that case — see ``egress.bodies``).
    """
    payload = asdict(rec)
    attrs = dict(payload.pop("attrs") or {})
    attrs.pop("body", None)  # internal classification copy, never shipped
    for key in (
        "flow_kind",
        "provider",
        "model",
        "method",
        "args_hash",
        "tokens_input",
        "tokens_output",
        "content_type",
        "path",
        "request_body",
        "response_body",
        "request_headers",
        "response_headers",
        # Error / policy observability — forwarded so the SaaS can surface blocked or
        # failed egress flows in the Flux IA dashboard and the security feed.
        "blocked",
        "block_reason",
        "error_type",
        "error_detail",
        "cache_hit",
        "cache_hit_type",
        "cache_layer",
        "cache_evaluated",
        "cache_embedding_error",
        "similarity_score",
        "rag_evaluated",
        "rag_enriched",
        "rag_chunks_count",
        "rag_embedding_error",
        "rag_skipped_reason",
        "rag",
        # Sync MITM pipeline observability (console Flow AI graph + PII counts).
        "pipeline_sync",
        "pipeline_modules",
        "pii_anonymized",
        "pii_response_filtered",
        "loop_id",
        "loop_iteration",
    ):
        if key in attrs and attrs[key] is not None:
            payload[key] = attrs[key]
    if attrs.get("pii_detected"):
        payload["pii_detected"] = True
    # Preserve non-flattened attrs (compression, etc.) for ingest.
    leftover = {k: v for k, v in attrs.items() if k not in payload and v is not None}
    # Also keep loop_* inside attrs: older API schemas that omit top-level loop_id
    # would otherwise drop the Boucle link entirely (iters stuck at 0).
    for key in ("loop_id", "loop_iteration"):
        if key in attrs and attrs[key] is not None:
            leftover[key] = attrs[key]
    if leftover:
        payload["attrs"] = leftover
    return payload


class AuditSink:
    """Buffers records and flushes them in batches to the ingest endpoint.

    Best-effort: shipping failures never block or break the proxied traffic.
    """

    def __init__(
        self,
        *,
        ingest_url: str | None = None,
        api_key: str | None = None,
        batch_size: int = 20,
        flush_interval_s: float = 5.0,
    ) -> None:
        self.ingest_url = ingest_url or os.environ.get("TOKENSAVER_INGEST_URL", "")
        self.api_key = api_key or os.environ.get("TOKENSAVER_API_KEY", "")
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self._buffer: list[EgressRecord] = []
        self._lock = asyncio.Lock()
        if self.ingest_url and self.api_key:
            logger.info("egress ingest → %s (api key set)", self.ingest_url)
        elif self.ingest_url:
            logger.warning("egress ingest URL set but TOKENSAVER_API_KEY missing — records dropped")
        else:
            logger.warning("egress ingest disabled (TOKENSAVER_INGEST_URL unset) — records dropped")

    async def record(self, rec: EgressRecord) -> None:
        # Belt-and-suspenders: stamp BusinessLoop from current-loop.env / env
        # when the capture path forgot (tunnel / empty headers / older sites).
        # File is hot-reloaded so Claude MCP can open/close a Boucle while serve runs.
        try:
            from tokensaver_egress.business_loop import refresh_loop_env_from_file

            refresh_loop_env_from_file()
        except Exception:
            pass
        attrs = dict(rec.attrs or {})
        changed = False
        if not str(attrs.get("loop_id") or "").strip():
            lid = (os.environ.get("TOKENSAVER_LOOP_ID") or "").strip()
            if lid:
                attrs["loop_id"] = lid[:128]
                changed = True
        if attrs.get("loop_iteration") is None:
            raw = (os.environ.get("TOKENSAVER_LOOP_ITERATION") or "").strip()
            if raw:
                try:
                    attrs["loop_iteration"] = int(raw)
                    changed = True
                except ValueError:
                    pass
        if changed:
            rec.attrs = attrs
        batch: list[EgressRecord] = []
        async with self._lock:
            self._buffer.append(rec)
            if len(self._buffer) >= self.batch_size:
                batch = self._buffer
                self._buffer = []
        if batch:
            # Ne pas bloquer le flux MITM pendant l'ingest : expédition en parallèle.
            asyncio.create_task(self._ship(batch), name="egress-audit-ship")

    async def flush(self) -> None:
        async with self._lock:
            batch = self._buffer
            self._buffer = []
        if batch:
            await self._ship(batch)

    async def run_periodic_flush(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval_s)
            try:
                await self.flush()
            except Exception:
                logger.debug("periodic flush failed", exc_info=True)

    async def _ship(self, batch: list[EgressRecord]) -> None:
        if not self.ingest_url or not self.api_key:
            logger.debug("ingest disabled (missing url/key); dropping %d records", len(batch))
            return
        try:
            import httpx

            payload = {"records": [record_to_ingest_dict(r) for r in batch]}
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    self.ingest_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except Exception:
            logger.debug("failed to ship %d egress records", len(batch), exc_info=True)
