"""Request-body integrity for MITM → provider (truncation / unexpected rewrite).

Claude Code (Bun) has been observed sending exactly 2 MiB of truncated JSON
through an HTTP proxy (Anthropic: EOF at column 2097152). Our MITM must:

1. Detect invalid JSON / Content-Length mismatches before contacting Anthropic
   (avoids poisoning conversation state → short-gap ``thread_not_found``).
2. Stamp ``proxy_body_altered`` when outbound bytes ≠ inbound bytes *without*
   an intentional transform (routing / RAG / compression / PII). That metric
   must stay at 0.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger("tokensaver-egress.body_integrity")

# Bun Readable highWaterMark ceiling — exact body size + invalid JSON ⇒ suspect.
_BUN_HWM_CAP = 2 * 1024 * 1024


def body_sha256_hex(data: bytes | bytearray | memoryview | None) -> str | None:
    if not data:
        return None
    return hashlib.sha256(bytes(data)).hexdigest()


def content_length_declared(headers: dict[str, str] | None) -> int | None:
    if not headers:
        return None
    for k, v in headers.items():
        if k.lower() == "content-length":
            try:
                return int(str(v).strip())
            except (TypeError, ValueError):
                return None
    return None


def json_body_valid(body: bytes | None) -> bool:
    if not body:
        return True
    stripped = body.lstrip()
    if not stripped or stripped[:1] not in (b"{", b"["):
        return True  # non-JSON LLM paths are rare; don't block
    try:
        json.loads(body)
        return True
    except Exception:
        return False


def intentional_body_transform(
    *,
    routing_applied: bool = False,
    rag_modified: bool = False,
    compression_modified: bool = False,
    pii_anonymized: bool = False,
) -> bool:
    return bool(routing_applied or rag_modified or compression_modified or pii_anonymized)


def assess_request_body(
    *,
    body_in: bytes | None,
    body_out: bytes | None,
    headers_out: dict[str, str] | None,
    intentional_transform: bool,
) -> dict[str, Any]:
    """Return audit attrs describing body integrity for one MITM exchange."""
    bin_ = bytes(body_in) if body_in else b""
    bout = bytes(body_out) if body_out else b""
    sha_in = body_sha256_hex(bin_)
    sha_out = body_sha256_hex(bout)
    cl = content_length_declared(headers_out)
    bytes_out = len(bout)
    cl_mismatch = cl is not None and cl != bytes_out
    json_ok = json_body_valid(bout) if bout else True
    truncated_suspect = (
        bytes_out == _BUN_HWM_CAP and not json_ok and bout.lstrip()[:1] in (b"{", b"[")
    )
    proxy_altered = bool(sha_in and sha_out and sha_in != sha_out and not intentional_transform)

    attrs: dict[str, Any] = {
        "request_body_bytes_in": len(bin_),
        "request_body_bytes_out": bytes_out,
        "request_body_sha256_in": sha_in,
        "request_body_sha256_out": sha_out,
        "content_length_declared": cl,
        "content_length_mismatch": cl_mismatch,
        "client_json_valid": json_ok,
        "intentional_body_transform": bool(intentional_transform),
        "proxy_body_altered": proxy_altered,
        "client_body_truncation_suspect": truncated_suspect,
    }
    if proxy_altered:
        logger.error(
            "proxy_body_altered in=%s out=%s intentional=%s",
            len(bin_),
            bytes_out,
            intentional_transform,
        )
    elif truncated_suspect:
        logger.warning(
            "client_body_truncation_suspect bytes=%s (Bun 2MiB HWM?) — blocking upstream",
            bytes_out,
        )
    elif not json_ok and bout.lstrip()[:1] in (b"{", b"["):
        logger.warning("client_json_invalid bytes=%s — blocking upstream", bytes_out)
    return attrs


def should_block_invalid_llm_body(attrs: dict[str, Any]) -> bool:
    """True when forwarding would only produce Anthropic invalid_request_error."""
    if attrs.get("content_length_mismatch"):
        return True
    if attrs.get("proxy_body_altered"):
        return True
    if attrs.get("client_json_valid") is False:
        return True
    return False


def integrity_block_payload(attrs: dict[str, Any]) -> bytes:
    """JSON body returned to the client when we refuse to forward a bad request."""
    if attrs.get("proxy_body_altered"):
        reason = "proxy_body_altered"
        message = (
            "TokenSaver egress blocked this request: the body was altered without an "
            "intentional transform (routing/RAG/compression/PII). "
            "Metric proxy_body_altered must stay at 0."
        )
    elif attrs.get("content_length_mismatch"):
        reason = "content_length_mismatch"
        message = (
            "TokenSaver egress blocked this request: Content-Length does not match "
            f"the body size (declared={attrs.get('content_length_declared')}, "
            f"bytes={attrs.get('request_body_bytes_out')})."
        )
    elif attrs.get("client_body_truncation_suspect"):
        reason = "client_body_truncated"
        message = (
            "TokenSaver egress blocked this request: body is exactly 2 MiB and is not "
            "valid JSON (likely truncated by the HTTP client through the proxy). "
            "Compact the conversation or shrink pasted images, then retry."
        )
    else:
        reason = "client_json_invalid"
        message = (
            "TokenSaver egress blocked this request: the JSON body is incomplete or "
            "invalid. Retry after reducing context size or image payloads."
        )
    return json.dumps(
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": message,
                "tokensaver_reason": reason,
                "request_body_bytes": attrs.get("request_body_bytes_out"),
            },
        }
    ).encode("utf-8")
