"""Optional ACP-9 loop precheck before forwarding an LLM call (egress path).

When ``TOKENSAVER_LOOP_PRECHECK=1`` and the request carries ``X-Tokensaver-Loop-Id``,
the MITM asks ``POST /api/v1/loops/{id}/precheck`` and may return 403/429 to the
client **without** contacting the upstream provider.

Fail-open by default if the backend is unreachable (``TOKENSAVER_LOOP_PRECHECK_FAIL_OPEN=0``
to fail-closed).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("tokensaver-egress.loop_precheck")


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


def loop_precheck_enabled() -> bool:
    return _flag("TOKENSAVER_LOOP_PRECHECK")


def _fail_open() -> bool:
    raw = (os.environ.get("TOKENSAVER_LOOP_PRECHECK_FAIL_OPEN") or "1").strip().lower()
    return raw not in ("0", "false", "no")


def _api_base() -> str:
    """Derive API origin from TOKENSAVER_INGEST_URL (…/egress/ingest → …)."""
    ingest = (os.environ.get("TOKENSAVER_INGEST_URL") or "").strip()
    if "/api/v1/egress" in ingest:
        return ingest.split("/api/v1/egress")[0].rstrip("/")
    if ingest.endswith("/ingest"):
        # …/api/v1/egress/ingest
        return ingest.rsplit("/api/v1", 1)[0].rstrip("/") if "/api/v1" in ingest else ingest.rstrip("/")
    return (os.environ.get("TOKENSAVER_API_BASE") or "").rstrip("/")


@dataclass
class LoopPrecheckResult:
    allowed: bool
    effect: str
    status_code: int
    body: dict[str, Any]
    error: str | None = None


def precheck_loop_call(
    *,
    loop_id: str,
    iteration: int | None = None,
    timeout_s: float = 3.0,
) -> LoopPrecheckResult:
    """HTTP precheck; never raises — returns fail-open allow on transport errors."""
    lid = (loop_id or "").strip()
    if not lid:
        return LoopPrecheckResult(True, "allow", 200, {"reason": "NO_LOOP_ID"})

    base = _api_base()
    api_key = (os.environ.get("TOKENSAVER_API_KEY") or "").strip()
    if not base or not api_key:
        if _fail_open():
            return LoopPrecheckResult(True, "allow", 200, {"reason": "PRECHECK_UNCONFIGURED"})
        return LoopPrecheckResult(
            False,
            "deny",
            403,
            {"error": {"code": "LOOP_PRECHECK_UNCONFIGURED"}},
            error="missing API base or key",
        )

    url = f"{base}/api/v1/loops/{lid}/precheck"
    payload: dict[str, Any] = {}
    if iteration is not None:
        payload["next_iteration"] = int(iteration)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:
            data = {"error": {"message": str(exc), "code": "LOOP_PRECHECK_HTTP"}}
        effect = "deny"
        if exc.code == 429:
            effect = "throttle"
        return LoopPrecheckResult(False, effect, int(exc.code), data if isinstance(data, dict) else {})
    except Exception as exc:
        logger.warning("loop precheck failed open=%s: %s", _fail_open(), exc)
        if _fail_open():
            return LoopPrecheckResult(True, "allow", 200, {"reason": "PRECHECK_ERROR_FAIL_OPEN"})
        return LoopPrecheckResult(
            False,
            "deny",
            403,
            {"error": {"code": "LOOP_PRECHECK_ERROR", "message": str(exc)}},
            error=str(exc),
        )

    effect = str(data.get("effect") or "allow")
    if effect in {"deny", "require_approval"}:
        code = 403
        return LoopPrecheckResult(False, effect, code, data)
    if effect == "throttle":
        return LoopPrecheckResult(False, effect, 429, data)
    return LoopPrecheckResult(True, effect, 200, data)
