"""Shared HTTP client for egress → TokenSaver backend (connection pooling)."""

from __future__ import annotations

import os
from typing import Any

import httpx

_DEFAULT_TIMEOUT_S = 45.0
_client: httpx.AsyncClient | None = None


def backend_timeout_s() -> float:
    raw = (os.environ.get("EGRESS_BACKEND_TIMEOUT_S") or "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_S


def _backend_max_connections() -> int:
    raw = (os.environ.get("EGRESS_BACKEND_MAX_CONNECTIONS") or "").strip()
    if raw:
        try:
            return max(8, int(raw))
        except ValueError:
            pass
    return 64


def get_backend_client() -> httpx.AsyncClient:
    """Reuse one AsyncClient per process (parallel in-flight backend calls per flow)."""
    global _client
    max_conn = _backend_max_connections()
    if _client is None or _client.is_closed:
        # Default timeout; hot paths (compress) override per-request.
        _client = httpx.AsyncClient(
            timeout=backend_timeout_s(),
            limits=httpx.Limits(
                max_connections=max_conn,
                max_keepalive_connections=min(max_conn, 32),
            ),
        )
    return _client


async def backend_get(url: str, *, api_key: str, timeout: float | None = None, **kwargs: Any) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Authorization", f"Bearer {api_key}")
    if timeout is not None:
        kwargs["timeout"] = timeout
    return await get_backend_client().get(url, headers=headers, **kwargs)


async def backend_post(url: str, *, api_key: str, timeout: float | None = None, **kwargs: Any) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Authorization", f"Bearer {api_key}")
    if timeout is not None:
        kwargs["timeout"] = timeout
    return await get_backend_client().post(url, headers=headers, **kwargs)


def reset_backend_client() -> None:
    global _client
    _client = None
