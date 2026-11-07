"""SaaS endpoint defaults after CP/DP cutover (ADR-023 / D9).

- DP (``api.tokensaver.cloud``): ``POST /api/v1/egress/ingest`` + runtime
  (cache / PII / compress / authorize / budget-precheck).
- CP (``control.tokensaver.cloud``): auth, loops, ``POST …/egress/agentic-events``,
  admin egress APIs.

Env overrides:
- ``TOKENSAVER_INGEST_URL`` — DP ingest (required for MITM audit ship)
- ``TOKENSAVER_CONTROL_URL`` — CP origin ``https://control…`` (no path) or
  ``…/api/v1``
- ``TOKENSAVER_API_BASE`` — alias of control ``…/api/v1`` (loop precheck)
- ``TOKENSAVER_AGENTIC_INGEST_URL`` — full agentic-events URL (wins)
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_INGEST_URL = "https://api.tokensaver.cloud/api/v1/egress/ingest"
DEFAULT_CONTROL_ORIGIN = "https://control.tokensaver.cloud"
DEFAULT_API_ORIGIN = DEFAULT_CONTROL_ORIGIN  # signup / virtual-keys on CP
LOCAL_INGEST_URL = "http://localhost:8000/api/v1/egress/ingest"
LOCAL_CONTROL_ORIGIN = "http://localhost:8000"
LOCAL_API_ORIGIN = LOCAL_CONTROL_ORIGIN
PLATFORM_URL = "https://app.tokensaver.cloud"
DEFAULT_MCP_URL = "https://mcp.tokensaver.cloud/mcp"
DEFAULT_GATEWAY_URL = "https://mcp-gateway.tokensaver.cloud/mcp"

# Hosts the agent must not send through the local MITM proxy.
NO_PROXY_HOSTS = (
    "127.0.0.1,localhost,::1,"
    "api.tokensaver.cloud,control.tokensaver.cloud,app.tokensaver.cloud,"
    "auth.tokensaver.cloud,mcp.tokensaver.cloud,mcp-gateway.tokensaver.cloud,"
    # Legacy dual-host until .fr API retirement (ADR-023).
    "api.tokensaver.fr,platform.tokensaver.fr,control.tokensaver.fr,"
    "mcp.tokensaver.fr,gateway.tokensaver.fr,mcp-gateway.tokensaver.fr"
)

# Known DP LLM hostnames → matching CP control origin (scheme preserved).
_DP_TO_CP_NETLOC = {
    "api.tokensaver.cloud": "control.tokensaver.cloud",
    "api.tokensaver.fr": "control.tokensaver.cloud",
}


def _strip(s: str | None) -> str:
    return (s or "").strip()


def ingest_url(explicit: str | None = None) -> str:
    return _strip(explicit) or _strip(os.environ.get("TOKENSAVER_INGEST_URL")) or DEFAULT_INGEST_URL


def _origin_from_url(raw: str) -> str:
    parsed = urlparse(raw.strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _normalize_control_origin(raw: str) -> str:
    """Accept origin, ``…/api/v1``, or a full CP path; return scheme://host."""
    s = raw.strip().rstrip("/")
    if not s:
        return ""
    if s.endswith("/api/v1"):
        s = s[: -len("/api/v1")]
    origin = _origin_from_url(s)
    return origin or s


def control_origin(*, ingest: str | None = None) -> str:
    """Resolve CP origin for auth / loops / agentic-events."""
    for key in ("TOKENSAVER_CONTROL_URL", "TOKENSAVER_API_BASE"):
        raw = _strip(os.environ.get(key))
        if raw:
            return _normalize_control_origin(raw) or DEFAULT_CONTROL_ORIGIN

    agentic = _strip(os.environ.get("TOKENSAVER_AGENTIC_INGEST_URL"))
    if agentic:
        origin = _origin_from_url(agentic)
        if origin:
            return origin

    # Explicit empty ingest → SaaS CP default (do not inherit shell INGEST).
    if ingest is not None and not _strip(ingest):
        return DEFAULT_CONTROL_ORIGIN

    ing = ingest_url(ingest)
    origin = _origin_from_url(ing)
    if not origin:
        return DEFAULT_CONTROL_ORIGIN

    parsed = urlparse(origin)
    host = (parsed.netloc or "").lower()
    if host in _DP_TO_CP_NETLOC:
        return f"{parsed.scheme}://{_DP_TO_CP_NETLOC[host]}"
    if "localhost" in host or host.startswith("127.0.0.1"):
        return origin
    # Same-host monolith / custom deploy: CP shares the ingest host.
    return origin


def control_api_base(*, ingest: str | None = None) -> str:
    return f"{control_origin(ingest=ingest).rstrip('/')}/api/v1"


def agentic_events_url(*, ingest: str | None = None) -> str:
    explicit = _strip(os.environ.get("TOKENSAVER_AGENTIC_INGEST_URL"))
    if explicit:
        return explicit.rstrip("/")
    return f"{control_api_base(ingest=ingest)}/egress/agentic-events"


def loops_url(*, ingest: str | None = None) -> str:
    return f"{control_api_base(ingest=ingest)}/loops"


def api_origin_for_auth(*, ingest: str | None = None) -> str:
    """CP origin used by signup / signin / virtual-keys."""
    return control_origin(ingest=ingest)
