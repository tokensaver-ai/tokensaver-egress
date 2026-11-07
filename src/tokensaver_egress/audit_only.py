"""Audit-only / journalisation seule — forward raw to Anthropic, still record I/O.

When ``EGRESS_AUDIT_ONLY=1`` (or ``EGRESS_PIPELINE_MODE=audit``), the MITM
skips cache / RAG / compression / PII / model-routing and sends the client body
unchanged to ``api.anthropic.com`` (or the original host), while still writing
egress_audit rows (bodies when ``EGRESS_CAPTURE_BODIES=1``).

Used for TNF bisection: two prompts through the proxy with zero TokenSaver
transforms, comparable to a direct Anthropic path.
"""

from __future__ import annotations

import os


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def audit_only_mode() -> bool:
    if _flag("EGRESS_AUDIT_ONLY"):
        return True
    mode = (os.environ.get("EGRESS_PIPELINE_MODE") or "").strip().lower()
    return mode in ("audit", "audit_only", "journal", "logging", "passthrough_audit")
