"""Egress pre-flight enforcement client (ACP-4 enforce ‖ ACP-7).

Opt-in. When ``EGRESS_ENFORCE_ENABLED`` is set, the MITM relay asks the backend
(``POST {ingest_base}/authorize``) whether a destination is allowed *before*
forwarding the request upstream. A ``deny`` means the proxy returns a 4xx to the
client and never contacts the provider — so the call consumes zero tokens.

Design notes:
- **Zero-trust by default** (backend ``EGRESS_ENFORCE_MODE=zerotrust``): only catalog
  assets with status ``approved`` pass. ``quarantined`` / ``discovered`` / ``disabled``
  / not-yet-registered destinations are denied until an admin approves them.
- **Permissive mode** (``EGRESS_ENFORCE_MODE=permissive``): legacy default-allow /
  explicit-deny — only ``disabled`` blocks.
- **Fail-open** by default (``EGRESS_ENFORCE_FAIL_OPEN=0`` to fail-closed): if the
  backend is unreachable we don't break the user's traffic.
- **Short TTL cache** keyed by (flow_kind, provider, model, host) to avoid a
  round-trip on every request of a hot keep-alive connection.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger("tokensaver-egress.enforce")

_DEFAULT_TTL_S = 30.0
# Deny decisions use a short TTL so that approving an asset in the console takes
# effect almost immediately (no stale 403 after re-approval). Allow decisions keep
# the longer TTL to avoid a round-trip on every request of a hot connection.
_DEFAULT_DENY_TTL_S = 5.0
_DEFAULT_TIMEOUT_S = 3.0


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


def enforce_enabled() -> bool:
    return _flag("EGRESS_ENFORCE_ENABLED")


def _authorize_url(ingest_url: str) -> str:
    """Derive the authorize endpoint from the ingest URL (…/egress/ingest → …/egress/authorize)."""
    base = (ingest_url or "").strip()
    if base.endswith("/ingest"):
        return base[: -len("/ingest")] + "/authorize"
    return base.rstrip("/") + "/authorize" if base else ""


@dataclass
class _CacheEntry:
    allowed: bool
    expires_at: float


class PolicyEnforcer:
    """Caches backend authorization decisions for egress destinations."""

    def __init__(
        self,
        *,
        authorize_url: str | None = None,
        api_key: str | None = None,
        ttl_s: float = _DEFAULT_TTL_S,
        deny_ttl_s: float | None = None,
        fail_open: bool | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.enabled = enforce_enabled()
        ingest_url = os.environ.get("TOKENSAVER_INGEST_URL", "")
        self.authorize_url = authorize_url if authorize_url is not None else _authorize_url(ingest_url)
        self.api_key = api_key if api_key is not None else os.environ.get("TOKENSAVER_API_KEY", "")
        self.ttl_s = ttl_s
        if deny_ttl_s is not None:
            self.deny_ttl_s = deny_ttl_s
        else:
            raw = (os.environ.get("EGRESS_ENFORCE_DENY_TTL_S") or "").strip()
            try:
                self.deny_ttl_s = float(raw) if raw else _DEFAULT_DENY_TTL_S
            except ValueError:
                self.deny_ttl_s = _DEFAULT_DENY_TTL_S
        self.timeout_s = timeout_s
        if fail_open is not None:
            self.fail_open = fail_open
        else:
            # Default fail-open unless explicitly set to 0/false.
            raw = (os.environ.get("EGRESS_ENFORCE_FAIL_OPEN") or "").strip().lower()
            self.fail_open = raw not in ("0", "false", "no")
        self._cache: dict[tuple[str, str, str, str], _CacheEntry] = {}

    def _cache_key(
        self, flow_kind: str | None, provider: str | None, model: str | None, host: str | None
    ) -> tuple[str, str, str, str]:
        return (
            (flow_kind or "").lower(),
            (provider or "").lower(),
            (model or ""),
            (host or "").lower(),
        )

    async def authorize(
        self,
        *,
        flow_kind: str | None,
        provider: str | None,
        model: str | None,
        host: str | None,
    ) -> bool:
        """Return True if the destination is allowed, False to block (deny)."""
        if not self.enabled:
            return True
        if not self.authorize_url or not self.api_key:
            # Misconfigured enforcement → don't silently block.
            return True

        key = self._cache_key(flow_kind, provider, model, host)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and cached.expires_at > now:
            return cached.allowed

        allowed = await self._fetch_decision(flow_kind, provider, model, host)
        ttl = self.ttl_s if allowed else self.deny_ttl_s
        self._cache[key] = _CacheEntry(allowed=allowed, expires_at=now + ttl)
        return allowed

    def clear_cache(self) -> None:
        """Drop all cached decisions (e.g. after a catalog status change)."""
        self._cache.clear()

    async def _fetch_decision(
        self, flow_kind: str | None, provider: str | None, model: str | None, host: str | None
    ) -> bool:
        try:
            import httpx

            payload = {"flow_kind": flow_kind, "provider": provider, "model": model, "host": host}
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(
                    self.authorize_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if resp.status_code != 200:
                logger.debug("authorize HTTP %s → fail-%s", resp.status_code, "open" if self.fail_open else "closed")
                return self.fail_open
            data = resp.json()
            decision = str(data.get("decision") or "allow").lower()
            if decision == "deny":
                logger.info(
                    "egress DENY host=%s provider=%s model=%s reason=%s",
                    host,
                    provider,
                    model,
                    data.get("reason"),
                )
                return False
            return True
        except Exception:
            logger.debug("authorize call failed → fail-%s", "open" if self.fail_open else "closed", exc_info=True)
            return self.fail_open


_ENFORCER: PolicyEnforcer | None = None


def get_enforcer() -> PolicyEnforcer:
    """Lazy module-level enforcer configured from env (mirrors bodies/mitm helpers)."""
    global _ENFORCER
    if _ENFORCER is None:
        _ENFORCER = PolicyEnforcer()
    return _ENFORCER


def reset_enforcer() -> None:
    """Test helper — drop the cached singleton so env changes take effect."""
    global _ENFORCER
    _ENFORCER = None
