"""Claude Code / SDK utility LLM endpoints — fast-path exclusions for egress MITM.

These calls are not user chat turns: token estimates and small internal classifiers
(Bash safety / autocomplete on Haiku). They must stay on the native Anthropic model
and skip model_routing plus heavy preflight modules (cache / RAG / compression).

Important: Claude Code often uses ``/v1/messages?beta=true`` for the **main agent
chat** as well. Path alone must not skip routing — only ``count_tokens`` and
Haiku (or equivalently tiny) classifier bodies qualify.
"""

from __future__ import annotations

import json
from typing import Any


def is_client_utility_llm_path(path: str | None) -> bool:
    """Return True for path-only utility endpoints (``count_tokens``)."""
    if not path:
        return False
    return "count_tokens" in path.strip().lower()


def _model_from_body(body: bytes | dict[str, Any] | None) -> str:
    if body is None:
        return ""
    if isinstance(body, dict):
        return str(body.get("model") or "")
    if not body:
        return ""
    try:
        parsed = json.loads(body.decode("utf-8", errors="ignore"))
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return ""
    if isinstance(parsed, dict):
        return str(parsed.get("model") or "")
    return ""


def is_client_utility_llm_request(
    path: str | None,
    *,
    body: bytes | dict[str, Any] | None = None,
    model: str | None = None,
) -> bool:
    """True for utility LLM calls that must bypass routing / heavy preflight.

    - ``count_tokens`` — always
    - ``/v1/messages?beta=true`` **only** when the requested model is Haiku
      (internal classifiers). Sonnet/Opus on the same path are main chat and
      must still receive ``model_routing``.
    """
    if is_client_utility_llm_path(path):
        return True
    if not path:
        return False
    p = path.strip().lower()
    if "/v1/messages" not in p or "beta=true" not in p:
        return False
    resolved = (model or "").strip() or _model_from_body(body)
    return "haiku" in resolved.lower()
