"""Lightweight PII detection for egress capture (ACP-4 §4.5).

Regex-only v1 — no ML models in the client proxy. Flags emails, phones, cards,
SSN-style patterns so ingest can set ``pii_detected`` without shipping raw text.
"""

from __future__ import annotations

import re

_PII_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b(?:sk-|pk_live_|pk_test_|AKIA[0-9A-Z]{16}|ghp_|glpat-)[A-Za-z0-9_-]{8,}\b"),
]


def scan_text_for_pii(text: str | None) -> bool:
    if not text or not text.strip():
        return False
    sample = text[:50000]
    return any(p.search(sample) for p in _PII_PATTERNS)


def scan_json_for_pii(body: object | None) -> bool:
    if body is None:
        return False
    import json

    try:
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str)
    except Exception:
        text = str(body)
    return scan_text_for_pii(text) or scan_text_for_mask_placeholders(text)


_MASK_PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z0-9_]+\]")


def scan_text_for_mask_placeholders(text: str | None) -> bool:
    """True when captured bodies already contain PII mask markers (prior-turn history)."""
    if not text or not text.strip():
        return False
    return bool(_MASK_PLACEHOLDER_RE.search(text[:50000]))
