"""Inline LLM response cache for egress MITM (ACP-4 §4.6) — thin proxy client.

Policy-driven, like PII anonymization: there is **no enable flag**. Whenever the
proxy runs in MITM with an API key, it asks the backend whether the key has a
cache policy (``GET {ingest_base}/cache-policy``, short-lived cache). If so, it
looks up a cached response **first** (pipeline parity: before RAG / PII / LLM).
On a hit the backend returns the **ready-to-emit HTTP response** (status + content-type + body) which
the proxy writes verbatim — no provider-format synthesis lives in the proxy.

Fail-open by default (``EGRESS_CACHE_FAIL_OPEN=0`` to fail-closed) — a safety knob,
not a feature switch.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from tokensaver_egress.http_backend import backend_get, backend_post
from tokensaver_egress.llm_body import build_cache_key_hint

logger = logging.getLogger(__name__)

_DEFAULT_POLICY_TTL_S = 120.0
_DEFAULT_TIMEOUT_S = 15.0
_COMPACT_BODY_THRESHOLD = 32_768


def _derive_url(ingest_url: str, suffix: str) -> str:
    base = (ingest_url or "").strip()
    if base.endswith("/ingest"):
        return base[: -len("/ingest")] + "/" + suffix
    return base.rstrip("/") + "/" + suffix if base else ""


@dataclass(frozen=True)
class CachePolicy:
    cache_enabled: bool
    exact_cache: bool = True
    semantic_cache: bool = True


@dataclass(frozen=True)
class CacheLookupResult:
    hit: bool
    hit_type: str | None = None
    # Ready-to-emit HTTP response synthesized by the backend (proxy writes verbatim).
    synth_status: int = 200
    synth_content_type: str | None = None
    synth_body: bytes | None = None
    # Audit-only metadata.
    model: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    similarity_score: float | None = None
    cache_evaluated: bool = False
    embedding_error: str | None = None
    error_code: str | None = None
    pii_response: dict | None = None


class EgressCacheClient:
    """Backend cache lookup/store with a short-lived policy cache."""

    def __init__(
        self,
        *,
        policy_url: str | None = None,
        lookup_url: str | None = None,
        store_url: str | None = None,
        api_key: str | None = None,
        policy_ttl_s: float = _DEFAULT_POLICY_TTL_S,
        fail_open: bool | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        ingest_url = os.environ.get("TOKENSAVER_INGEST_URL", "")
        self.policy_url = policy_url if policy_url is not None else _derive_url(ingest_url, "cache-policy")
        self.lookup_url = lookup_url if lookup_url is not None else _derive_url(ingest_url, "cache-lookup")
        self.store_url = store_url if store_url is not None else _derive_url(ingest_url, "cache-store")
        self.api_key = api_key if api_key is not None else os.environ.get("TOKENSAVER_API_KEY", "")
        self.policy_ttl_s = policy_ttl_s
        self.timeout_s = timeout_s
        if fail_open is not None:
            self.fail_open = fail_open
        else:
            raw = (os.environ.get("EGRESS_CACHE_FAIL_OPEN") or "").strip().lower()
            self.fail_open = raw not in ("0", "false", "no")
        self._policy: CachePolicy | None = None
        self._policy_expires_at: float = 0.0

    @property
    def enabled(self) -> bool:
        """Configured to talk to the backend. Whether cache *applies* is policy-driven."""
        return self._configured()

    def _configured(self) -> bool:
        return bool(self.lookup_url and self.store_url and self.api_key)

    async def warm_policy(self) -> None:
        await self._fetch_policy()

    async def cache_enabled(self) -> bool:
        policy = await self._fetch_policy()
        return bool(policy and policy.cache_enabled)

    async def _fetch_policy(self) -> CachePolicy | None:
        now = time.monotonic()
        cached = self._policy
        if cached is not None and self._policy_expires_at > now:
            return cached
        if not self.policy_url or not self.api_key:
            return None
        try:
            resp = await backend_get(self.policy_url, api_key=self.api_key)
            if resp.status_code != 200:
                return None
            data = resp.json()
            pol = CachePolicy(
                cache_enabled=bool(data.get("cache_enabled")),
                exact_cache=bool(data.get("exact_cache", True)),
                semantic_cache=bool(data.get("semantic_cache", True)),
            )
            self._policy = pol
            self._policy_expires_at = now + self.policy_ttl_s
            return pol
        except Exception:
            logger.debug("egress cache-policy call failed", exc_info=True)
            return None

    async def lookup(
        self,
        body: bytes,
        *,
        provider: str | None,
        model: str | None,
        flow_kind: str | None,
        content_type: str | None,
        host: str | None = None,
        path: str | None = None,
        method: str | None = None,
        pii_anonymized: bool = False,
    ) -> CacheLookupResult | None:
        if not self.enabled or not body:
            return None
        policy = await self._fetch_policy()
        if policy is None or not policy.cache_enabled:
            return None
        if (flow_kind or "").lower() not in ("", "llm"):
            return None
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        try:
            payload: dict = {
                "provider": provider,
                "model": model,
                "flow_kind": flow_kind or "llm",
                "content_type": content_type,
                "host": host,
                "path": path,
                "method": method,
                "pii_anonymized": pii_anonymized,
            }
            hint = build_cache_key_hint(text, provider=provider)
            use_compact = len(text) >= _COMPACT_BODY_THRESHOLD and hint is not None
            if use_compact and hint is not None:
                payload["key_hint"] = {
                    "user_prompt": hint.user_prompt,
                    "hist_sig": hint.hist_sig,
                    "instr_sig": hint.instr_sig,
                    "model": hint.model,
                    "provider": hint.provider,
                    "temperature": hint.temperature,
                    "stream": hint.stream,
                    "has_tool_use": hint.has_tool_use,
                }
            else:
                payload["body"] = text
            resp = await backend_post(self.lookup_url, api_key=self.api_key or "", json=payload)
            if resp.status_code != 200:
                logger.debug("cache-lookup HTTP %s", resp.status_code)
                return None
            data = resp.json()
            if not data.get("hit"):
                return CacheLookupResult(
                hit=False,
                cache_evaluated=bool(data.get("cache_evaluated")),
                embedding_error=data.get("embedding_error"),
                error_code=data.get("error_code"),
            )
            synth = data.get("synth") or {}
            synth_body = synth.get("body")
            return CacheLookupResult(
                hit=True,
                hit_type=str(data.get("hit_type") or "exact"),
                synth_status=int(synth.get("status") or 200),
                synth_content_type=synth.get("content_type"),
                synth_body=synth_body.encode("utf-8") if isinstance(synth_body, str) else None,
                model=data.get("model"),
                tokens_input=data.get("tokens_input"),
                tokens_output=data.get("tokens_output"),
                similarity_score=data.get("similarity_score"),
                pii_response=data.get("pii_response") if isinstance(data.get("pii_response"), dict) else None,
            )
        except Exception:
            logger.debug("cache-lookup failed → fail-%s", "open" if self.fail_open else "closed", exc_info=True)
            return None

    async def store(
        self,
        body: bytes,
        *,
        provider: str | None,
        model: str | None,
        flow_kind: str | None,
        content_type: str | None,
        response_sample: bytes,
        tokens_input: int | None,
        tokens_output: int | None,
        pii_anonymized: bool = False,
        host: str | None = None,
        path: str | None = None,
        method: str | None = None,
    ) -> None:
        if not self.enabled or not body or not response_sample:
            return
        policy = await self._fetch_policy()
        if policy is None or not policy.cache_enabled:
            return
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return
        sample_text = response_sample.decode("utf-8", errors="replace")
        try:
            payload = {
                "provider": provider,
                "model": model,
                "flow_kind": flow_kind or "llm",
                "content_type": content_type,
                "body": text,
                "host": host,
                "path": path,
                "method": method,
                "pii_anonymized": pii_anonymized,
                "response_sample": sample_text,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
            }
            await backend_post(self.store_url, api_key=self.api_key or "", json=payload)
        except Exception:
            logger.debug("cache-store failed (best-effort)", exc_info=True)


_CACHE_CLIENT: EgressCacheClient | None = None


def get_cache_client() -> EgressCacheClient:
    global _CACHE_CLIENT
    if _CACHE_CLIENT is None:
        _CACHE_CLIENT = EgressCacheClient()
    return _CACHE_CLIENT


def reset_cache_client() -> None:
    global _CACHE_CLIENT
    _CACHE_CLIENT = None
