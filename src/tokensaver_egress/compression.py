"""Inline content-aware compression for egress MITM (post-PII request path)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from tokensaver_egress.http_backend import backend_get, backend_post

logger = logging.getLogger(__name__)

_DEFAULT_POLICY_TTL_S = 120.0
# Compress can exceed the shared backend timeout on ~1MB Claude Code bodies.
_DEFAULT_TIMEOUT_S = 120.0


def compression_timeout_s(default: float = _DEFAULT_TIMEOUT_S) -> float:
    raw = (os.environ.get("EGRESS_COMPRESSION_TIMEOUT_S") or "").strip()
    if raw:
        try:
            return max(5.0, float(raw))
        except ValueError:
            pass
    return max(5.0, float(default))


def _derive_url(ingest_url: str, suffix: str) -> str:
    base = (ingest_url or "").strip()
    if base.endswith("/ingest"):
        return base[: -len("/ingest")] + "/" + suffix
    return base.rstrip("/") + "/" + suffix if base else ""


@dataclass(frozen=True)
class CompressionPolicy:
    compression_enabled: bool


@dataclass(frozen=True)
class ToolOutputsCompressResult:
    modified: bool
    body: bytes | None = None
    evaluated: bool = False
    segments_count: int = 0
    skipped_reason: str | None = None
    detail: dict | None = None


class EgressCompressionClient:
    """Backend tool-output compression with a short-lived policy cache."""

    def __init__(
        self,
        *,
        policy_url: str | None = None,
        compress_url: str | None = None,
        api_key: str | None = None,
        policy_ttl_s: float = _DEFAULT_POLICY_TTL_S,
        fail_open: bool | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        ingest_url = os.environ.get("TOKENSAVER_INGEST_URL", "")
        self.policy_url = policy_url if policy_url is not None else _derive_url(ingest_url, "compression-policy")
        self.compress_url = compress_url if compress_url is not None else _derive_url(
            ingest_url, "compress-tool-outputs"
        )
        self.api_key = api_key if api_key is not None else os.environ.get("TOKENSAVER_API_KEY", "")
        self.policy_ttl_s = policy_ttl_s
        self.timeout_s = compression_timeout_s(timeout_s)
        if fail_open is not None:
            self.fail_open = fail_open
        else:
            raw = (os.environ.get("EGRESS_COMPRESSION_FAIL_OPEN") or "true").strip().lower()
            self.fail_open = raw not in ("0", "false", "no")
        self._policy: CompressionPolicy | None = None
        self._policy_expires_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._configured()

    def _configured(self) -> bool:
        return bool(self.compress_url and self.policy_url and self.api_key)

    async def warm_policy(self) -> None:
        await self._fetch_policy()

    async def compression_enabled(self) -> bool:
        policy = await self._fetch_policy()
        return bool(policy and policy.compression_enabled)

    async def _fetch_policy(self) -> CompressionPolicy | None:
        now = time.monotonic()
        cached = self._policy
        if cached is not None and self._policy_expires_at > now:
            return cached
        if not self.policy_url or not self.api_key:
            return None
        try:
            resp = await backend_get(self.policy_url, api_key=self.api_key)
            resp.raise_for_status()
            data = resp.json()
            policy = CompressionPolicy(compression_enabled=bool(data.get("compression_enabled")))
            self._policy = policy
            self._policy_expires_at = now + self.policy_ttl_s
            return policy
        except Exception:
            logger.debug(
                "compression-policy fetch failed → fail-%s",
                "open" if self.fail_open else "closed",
                exc_info=True,
            )
            return CompressionPolicy(compression_enabled=False) if self.fail_open else None

    async def compress_tool_outputs(
        self,
        body: bytes,
        *,
        provider: str | None,
        model: str | None,
        flow_kind: str | None,
        content_type: str | None,
        rag_similarity_threshold: float | None = None,
        rag_chunks_with_sources: list[dict] | None = None,
    ) -> ToolOutputsCompressResult | None:
        if not self.enabled or not body:
            return None
        policy = await self._fetch_policy()
        if policy is None or not policy.compression_enabled:
            logger.debug("compression skipped: policy disabled or unavailable")
            return None
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            logger.info("compression skipped: request body is not valid UTF-8")
            return None
        try:
            payload: dict[str, object] = {
                "provider": provider,
                "model": model,
                "flow_kind": flow_kind or "llm",
                "content_type": content_type,
                "body": text,
            }
            if rag_similarity_threshold is not None:
                payload["rag_similarity_threshold"] = rag_similarity_threshold
            if rag_chunks_with_sources:
                payload["rag_chunks_with_sources"] = rag_chunks_with_sources
            resp = await backend_post(
                self.compress_url,
                api_key=self.api_key or "",
                json=payload,
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            out_body = data.get("body")
            body_bytes = out_body.encode("utf-8") if isinstance(out_body, str) and data.get("modified") else None
            return ToolOutputsCompressResult(
                modified=bool(data.get("modified")),
                body=body_bytes,
                evaluated=bool(data.get("evaluated")),
                segments_count=int(data.get("segments_count") or 0),
                skipped_reason=data.get("skipped_reason"),
                detail=data.get("detail") if isinstance(data.get("detail"), dict) else None,
            )
        except Exception:
            logger.warning(
                "compress-tool-outputs failed → fail-%s",
                "open" if self.fail_open else "closed",
                exc_info=True,
            )
            return None


_COMPRESSION_CLIENT: EgressCompressionClient | None = None


def get_compression_client() -> EgressCompressionClient:
    global _COMPRESSION_CLIENT
    if _COMPRESSION_CLIENT is None:
        _COMPRESSION_CLIENT = EgressCompressionClient()
    return _COMPRESSION_CLIENT
