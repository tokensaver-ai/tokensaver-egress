"""Inline RAG enrichment for egress MITM (ACP-4) — thin proxy client.

Policy-driven like cache/PII: when MITM runs with an API key, the proxy asks the
backend whether RAG is enabled (``GET …/rag-policy``) and enriches the outbound
request body before upstream when applicable.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from tokensaver_egress.http_backend import backend_get, backend_post

logger = logging.getLogger(__name__)

_DEFAULT_POLICY_TTL_S = 120.0
_DEFAULT_TIMEOUT_S = 45.0


def _derive_url(ingest_url: str, suffix: str) -> str:
    base = (ingest_url or "").strip()
    if base.endswith("/ingest"):
        return base[: -len("/ingest")] + "/" + suffix
    return base.rstrip("/") + "/" + suffix if base else ""


@dataclass(frozen=True)
class RagPolicy:
    rag_enabled: bool


@dataclass(frozen=True)
class RagEnrichResult:
    modified: bool
    body: bytes | None = None
    rag_evaluated: bool = False
    chunks_count: int = 0
    embedding_error: str | None = None
    error_code: str | None = None
    skipped_reason: str | None = None
    detail: dict | None = None


class EgressRagClient:
    """Backend RAG enrich with a short-lived policy cache."""

    def __init__(
        self,
        *,
        policy_url: str | None = None,
        enrich_url: str | None = None,
        api_key: str | None = None,
        policy_ttl_s: float = _DEFAULT_POLICY_TTL_S,
        fail_open: bool | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        ingest_url = os.environ.get("TOKENSAVER_INGEST_URL", "")
        self.policy_url = policy_url if policy_url is not None else _derive_url(ingest_url, "rag-policy")
        self.enrich_url = enrich_url if enrich_url is not None else _derive_url(ingest_url, "rag-enrich")
        self.api_key = api_key if api_key is not None else os.environ.get("TOKENSAVER_API_KEY", "")
        self.policy_ttl_s = policy_ttl_s
        self.timeout_s = timeout_s
        if fail_open is not None:
            self.fail_open = fail_open
        else:
            raw = (os.environ.get("EGRESS_RAG_FAIL_OPEN") or "").strip().lower()
            self.fail_open = raw not in ("0", "false", "no")
        self._policy: RagPolicy | None = None
        self._policy_expires_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._configured()

    def _configured(self) -> bool:
        return bool(self.enrich_url and self.policy_url and self.api_key)

    async def warm_policy(self) -> None:
        await self._fetch_policy()

    async def rag_enabled(self) -> bool:
        policy = await self._fetch_policy()
        return bool(policy and policy.rag_enabled)

    async def _fetch_policy(self) -> RagPolicy | None:
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
            policy = RagPolicy(rag_enabled=bool(data.get("rag_enabled")))
            self._policy = policy
            self._policy_expires_at = now + self.policy_ttl_s
            return policy
        except Exception:
            logger.debug("rag-policy fetch failed → fail-%s", "open" if self.fail_open else "closed", exc_info=True)
            return RagPolicy(rag_enabled=False) if self.fail_open else None

    async def enrich(
        self,
        body: bytes,
        *,
        provider: str | None,
        model: str | None,
        flow_kind: str | None,
        content_type: str | None,
    ) -> RagEnrichResult | None:
        if not self.enabled or not body:
            return None
        policy = await self._fetch_policy()
        if policy is None or not policy.rag_enabled:
            return None
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        try:
            payload = {
                "provider": provider,
                "model": model,
                "flow_kind": flow_kind or "llm",
                "content_type": content_type,
                "body": text,
            }
            resp = await backend_post(self.enrich_url, api_key=self.api_key or "", json=payload)
            resp.raise_for_status()
            data = resp.json()
            out_body = data.get("body")
            body_bytes = out_body.encode("utf-8") if isinstance(out_body, str) else None
            return RagEnrichResult(
                modified=bool(data.get("modified")),
                body=body_bytes if data.get("modified") else None,
                rag_evaluated=bool(data.get("rag_evaluated")),
                chunks_count=int(data.get("chunks_count") or 0),
                embedding_error=data.get("embedding_error"),
                error_code=data.get("error_code"),
                skipped_reason=data.get("skipped_reason"),
                detail=data.get("detail") if isinstance(data.get("detail"), dict) else None,
            )
        except Exception:
            logger.debug("rag-enrich failed → fail-%s", "open" if self.fail_open else "closed", exc_info=True)
            return None


_RAG_CLIENT: EgressRagClient | None = None


def get_rag_client() -> EgressRagClient:
    global _RAG_CLIENT
    if _RAG_CLIENT is None:
        _RAG_CLIENT = EgressRagClient()
    return _RAG_CLIENT
