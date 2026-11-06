"""Gateway budget precheck for Keep my Claude / tokensaver-egress MITM.

Calls ``POST /api/v1/egress/budget-precheck`` (same Redis policies as Via
TokenSaver). Always on when ingest credentials are present; fail-open on
transport errors (``TOKENSAVER_BUDGET_PRECHECK_FAIL_OPEN=0`` to fail-closed).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("tokensaver-egress.budget_precheck")


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


def budget_precheck_enabled() -> bool:
    """Default ON; set TOKENSAVER_BUDGET_PRECHECK=0 to disable."""
    raw = (os.environ.get("TOKENSAVER_BUDGET_PRECHECK") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _fail_open() -> bool:
    raw = (os.environ.get("TOKENSAVER_BUDGET_PRECHECK_FAIL_OPEN") or "1").strip().lower()
    return raw not in ("0", "false", "no")


def _api_base() -> str:
    ingest = (os.environ.get("TOKENSAVER_INGEST_URL") or "").strip()
    if "/api/v1/egress" in ingest:
        return ingest.split("/api/v1/egress")[0].rstrip("/")
    if "/api/v1" in ingest:
        return ingest.split("/api/v1")[0].rstrip("/")
    return (os.environ.get("TOKENSAVER_API_BASE") or "").rstrip("/")


def _auth_headers() -> dict[str, str]:
    api_key = (os.environ.get("TOKENSAVER_API_KEY") or "").strip()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    vk = (os.environ.get("TOKENSAVER_API_KEY_ID") or os.environ.get("TOKENSAVER_SELECTED_API_KEY_ID") or "").strip()
    if vk:
        headers["X-Selected-Api-Key-Id"] = vk
    return headers


@dataclass
class BudgetPrecheckResult:
    allowed: bool
    status_code: int
    body: dict[str, Any]
    error_code: str | None = None
    message: str | None = None


def precheck_gateway_budget(
    *,
    provider: str | None,
    model: str | None,
    host: str | None = None,
    projected_tokens: float = 0.0,
    timeout_s: float = 3.0,
) -> BudgetPrecheckResult:
    """Never raises — fail-open on transport errors when configured."""
    if not budget_precheck_enabled():
        return BudgetPrecheckResult(True, 200, {"reason": "BUDGET_PRECHECK_DISABLED"})

    base = _api_base()
    api_key = (os.environ.get("TOKENSAVER_API_KEY") or "").strip()
    if not base or not api_key:
        if _fail_open():
            return BudgetPrecheckResult(True, 200, {"reason": "BUDGET_UNCONFIGURED"})
        return BudgetPrecheckResult(
            False,
            403,
            {"error": {"code": "BUDGET_PRECHECK_UNCONFIGURED"}},
            error_code="BUDGET_PRECHECK_UNCONFIGURED",
            message="missing API base or key",
        )

    url = f"{base}/api/v1/egress/budget-precheck"
    payload: dict[str, Any] = {
        "flow_kind": "llm",
        "provider": provider,
        "model": model,
        "host": host,
    }
    if projected_tokens and projected_tokens > 0:
        payload["projected_tokens"] = float(projected_tokens)

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_auth_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return BudgetPrecheckResult(True, 200, data if isinstance(data, dict) else {})
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:
            data = {"error": {"message": str(exc), "code": "BUDGET_PRECHECK_HTTP"}}
        if not isinstance(data, dict):
            data = {}
        detail_raw = data.get("detail")
        detail = detail_raw if isinstance(detail_raw, dict) else {}
        code = str(
            (detail or {}).get("error_code") or data.get("error_code") or ""
        ).strip()
        if isinstance(detail_raw, str) and detail_raw.strip():
            msg = detail_raw.strip()
        else:
            msg = str(
                (detail or {}).get("message") or data.get("message") or exc.reason or ""
            )
        # Hard deny: budget + VK revoked/expired/auth. Fail-open must not apply —
        # Keep my Claude would otherwise still reach Anthropic with user creds.
        if exc.code in (401, 402, 403, 429):
            low = msg.lower()
            if "revoked" in low:
                code = "VIRTUAL_KEY_REVOKED"
            elif "expired" in low:
                code = "VIRTUAL_KEY_EXPIRED"
            elif not code and exc.code in (401, 403):
                code = "VIRTUAL_KEY_UNUSABLE"
            elif not code and exc.code == 402:
                code = "GATEWAY_BUDGET_EXCEEDED"
            elif not code and exc.code == 429:
                code = "GATEWAY_BUDGET_THROTTLED"
            return BudgetPrecheckResult(False, int(exc.code), data, error_code=code, message=msg)
        if _fail_open():
            logger.warning("budget precheck HTTP %s → fail-open", exc.code)
            return BudgetPrecheckResult(True, 200, data, error_code="BUDGET_PRECHECK_FAIL_OPEN")
        return BudgetPrecheckResult(False, int(exc.code), data, error_code=code, message=msg)
    except Exception as exc:
        logger.warning("budget precheck failed open=%s: %s", _fail_open(), exc)
        if _fail_open():
            return BudgetPrecheckResult(True, 200, {"reason": "PRECHECK_ERROR_FAIL_OPEN"})
        return BudgetPrecheckResult(
            False,
            403,
            {"error": {"code": "BUDGET_PRECHECK_ERROR", "message": str(exc)}},
            error_code="BUDGET_PRECHECK_ERROR",
            message=str(exc),
        )
