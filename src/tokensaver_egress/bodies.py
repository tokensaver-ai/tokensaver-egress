"""Optional full payload capture for egress (ACP-4 §4.6).

Disabled by default (metadata-only). When ``EGRESS_CAPTURE_BODIES`` is enabled,
the request/response bodies and (sanitized) headers are attached to the audit
record so the console can show the real content (prompts, completions, tool I/O).

Safety defaults:
- Bodies are truncated to ``EGRESS_BODY_MAX_BYTES`` (default 1 MiB) per side.
- Credential headers (Authorization, x-api-key, cookie…) are redacted unless
  ``EGRESS_CAPTURE_RAW_HEADERS`` is set — we never want to persist the operator's
  own provider API keys in the audit store.
"""

from __future__ import annotations

import os

_DEFAULT_MAX = 1_048_576
_REDACTED = "[REDACTED]"
_SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "cookie",
    "set-cookie",
    "x-goog-api-key",
}


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def bodies_enabled() -> bool:
    """True when full request/response body capture is opted in."""
    return _flag("EGRESS_CAPTURE_BODIES")


def body_max_bytes() -> int:
    try:
        return max(0, int(os.environ.get("EGRESS_BODY_MAX_BYTES") or _DEFAULT_MAX))
    except (TypeError, ValueError):
        return _DEFAULT_MAX


def _raw_headers() -> bool:
    return _flag("EGRESS_CAPTURE_RAW_HEADERS")


def prepare_body(raw: bytes | bytearray | str | None) -> str | None:
    """Decode + truncate a body to a UTF-8 (lossy) string for storage, or None."""
    if not raw:
        return None
    data = raw.encode("utf-8", errors="ignore") if isinstance(raw, str) else bytes(raw)
    if not data:
        return None
    cap = body_max_bytes()
    if cap <= 0:
        return None
    truncated = len(data) > cap
    text = data[:cap].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n…[truncated {len(data) - cap} bytes]"
    return text


def sanitize_headers(headers: object | None) -> dict[str, str] | None:
    """Return a plain dict of headers with credential values redacted by default."""
    if not headers:
        return None
    raw = _raw_headers()
    items = headers.items() if hasattr(headers, "items") else headers
    out: dict[str, str] = {}
    try:
        for k, v in items:  # type: ignore[misc]
            key = str(k)
            if not raw and key.lower() in _SENSITIVE_HEADERS:
                out[key] = _REDACTED
            else:
                out[key] = str(v)
    except (TypeError, ValueError):
        return None
    return out or None


def headers_from_lines(lines: list[bytes] | None) -> dict[str, str] | None:
    """Parse raw ``b"Name: value\\r\\n"`` header lines (skipping the first request/status line)."""
    if not lines:
        return None
    parsed: dict[str, str] = {}
    for line in lines:
        s = line.decode("latin-1", errors="ignore").strip()
        if ":" not in s:
            continue
        name, value = s.split(":", 1)
        parsed[name.strip()] = value.strip()
    return sanitize_headers(parsed)
