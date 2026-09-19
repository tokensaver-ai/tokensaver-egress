"""Start ACP-9 BusinessLoops from egress (no tokensaver-cli dependency)."""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx

ENV_LOOP_ID = "TOKENSAVER_LOOP_ID"
ENV_LOOP_KIND = "TOKENSAVER_LOOP_KIND"
ENV_LOOP_ITERATION = "TOKENSAVER_LOOP_ITERATION"
ENV_LOOP_PRECHECK = "TOKENSAVER_LOOP_PRECHECK"

DEFAULT_KIND = "goal_based"


def loops_url_from_ingest(ingest_url: str | None = None) -> str:
    """Derive ``…/api/v1/loops`` from ``TOKENSAVER_INGEST_URL`` (…/egress/ingest)."""
    raw = (ingest_url or os.environ.get("TOKENSAVER_INGEST_URL") or "").strip()
    if not raw:
        raw = "https://api.tokensaver.fr/api/v1/egress/ingest"
    parsed = urlparse(raw)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/egress/ingest"):
        path = path[: -len("/egress/ingest")] + "/loops"
    elif path.endswith("/api/v1"):
        path = path + "/loops"
    elif "/api/v1/" in path:
        # generic: replace trailing segment with loops under api/v1
        idx = path.find("/api/v1/")
        path = path[: idx + len("/api/v1")] + "/loops"
    else:
        path = "/api/v1/loops"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def apply_loop_env(*, loop_id: str, loop_kind: str, iteration: int = 1, precheck: bool = True) -> None:
    os.environ[ENV_LOOP_ID] = loop_id
    os.environ[ENV_LOOP_KIND] = loop_kind
    os.environ[ENV_LOOP_ITERATION] = str(max(1, int(iteration)))
    if precheck:
        os.environ[ENV_LOOP_PRECHECK] = "1"


def start_business_loop(
    *,
    kind: str = DEFAULT_KIND,
    max_turns: int | None = 5,
    goal: str | None = None,
    api_key: str | None = None,
    ingest_url: str | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """POST ``/api/v1/loops`` + start event. Returns the loop row (must include loop_id)."""
    key = (api_key or os.environ.get("TOKENSAVER_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("TOKENSAVER_API_KEY required to start a BusinessLoop")

    kind = (kind or DEFAULT_KIND).strip() or DEFAULT_KIND
    lid = f"loop_{uuid.uuid4().hex[:24]}"
    goal_obj: dict[str, Any] | None = None
    if goal or max_turns:
        goal_obj = {}
        if goal:
            goal_obj["expression"] = goal
        if max_turns is not None:
            goal_obj["max_turns"] = int(max_turns)
    budgets: dict[str, Any] | None = None
    if max_turns is not None:
        budgets = {"max_iterations": int(max_turns)}

    body: dict[str, Any] = {
        "loop_id": lid,
        "loop_kind": kind,
        "status": "running",
        "trigger": {"type": "egress", "source": "tokensaver_egress"},
        "attrs": {"project_source": "egress_cli"},
    }
    if goal_obj:
        body["goal"] = goal_obj
    if budgets:
        body["budgets"] = budgets

    base = loops_url_from_ingest(ingest_url)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(base, headers=headers, json=body)
        if r.status_code >= 400:
            detail = r.text[:400]
            raise RuntimeError(f"HTTP {r.status_code} starting loop at {base}: {detail}")
        row = r.json() if r.content else {}
        if not isinstance(row, dict):
            row = {}
        # Anchor lifecycle (best-effort)
        try:
            client.post(
                f"{base.rstrip('/')}/events",
                headers=headers,
                json={
                    "event": "start",
                    "loop_id": row.get("loop_id") or lid,
                    "loop_kind": kind,
                    "decision": "continue",
                    "goal": goal_obj,
                    "budgets": budgets,
                },
            )
        except Exception:
            pass

    if not row.get("loop_id"):
        row["loop_id"] = lid
    if not row.get("loop_kind"):
        row["loop_kind"] = kind
    return row
