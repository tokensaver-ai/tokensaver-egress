"""MITM TLS relay for known LLM/MCP/A2A hosts (ACP-4 opt-in)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import ssl
import time
from collections.abc import Awaitable, Callable
from typing import Any

from tokensaver_egress.audit import AuditSink, EgressRecord
from tokensaver_egress.bodies import bodies_enabled, prepare_body, sanitize_headers
from tokensaver_egress.cache import get_cache_client
from tokensaver_egress.ca import MitmCA
from tokensaver_egress.classify import classify_egress, is_mcp_host, is_mitm_candidate_host
from tokensaver_egress.enforce import get_enforcer
from tokensaver_egress.http_parse import read_http_message, write_http_message
from tokensaver_egress.llm_utility import is_client_utility_llm_request
from tokensaver_egress.otel import egress_span
from tokensaver_egress.pipeline import (
    EgressPreflightResult,
    _set_request_body,
    is_llm_governance_preflight_needed,
    response_pii_filter_active,
    run_egress_llm_postflight,
    run_egress_llm_preflight,
    warm_llm_policies,
)
from tokensaver_egress.governance import governance_preflight_needed, governance_routing_enabled
from tokensaver_egress.rag import get_rag_client
from tokensaver_egress.routing import get_routing_client
from tokensaver_egress.stream_translate import OpenAIToAnthropicSSETranslator, request_body_wants_stream

logger = logging.getLogger("tokensaver-egress.mitm")

_BUF = 64 * 1024
# Cap the bytes we accumulate per response for classification (streaming SSE can be
# arbitrarily long); the full body is still streamed through to the client.
_CLASSIFY_CAP = 512 * 1024

# Deduplicate noisy "client rejected MITM CA" logs (one warning + DEBUG thereafter).
_tls_abort_hosts_logged: set[str] = set()
_tls_abort_hint_shown = False

_CLIENT_ABORT_ERRORS = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    asyncio.IncompleteReadError,
)


def _is_client_abort(exc: BaseException) -> bool:
    """True when the client dropped the MITM connection mid-request."""
    if isinstance(exc, _CLIENT_ABORT_ERRORS):
        return True
    if isinstance(exc, OSError):
        msg = str(exc).lower()
        if "broken pipe" in msg or "connection reset" in msg:
            return True
    return _is_client_abort_tls(exc)


def _is_client_abort_tls(exc: BaseException) -> bool:
    """True when the peer closed mid-handshake (untrusted CA, cancel, ALPN mismatch)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, _CLIENT_ABORT_ERRORS):
            return True
        # SSLWant* often wraps / precedes BrokenPipe when the client hangs up.
        if type(cur).__name__ in ("SSLWantReadError", "SSLWantWriteError", "SSLEOFError"):
            ctx = getattr(cur, "__context__", None)
            if isinstance(ctx, _CLIENT_ABORT_ERRORS):
                return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    msg = str(exc).lower()
    return "broken pipe" in msg or "connection reset" in msg

_RE_MODEL = re.compile(rb'"model"\s*:\s*"([^"]+)"')
# Anthropic streams use input_tokens/output_tokens; OpenAI uses prompt_tokens/completion_tokens.
# With Anthropic prompt caching, ``input_tokens`` is *uncached-only* — real context size is
# input_tokens + cache_creation_input_tokens + cache_read_input_tokens (Anthropic docs).
_RE_INPUT_TOKENS = re.compile(rb'"(?:input_tokens|prompt_tokens)"\s*:\s*(\d+)')
_RE_CACHE_CREATION_TOKENS = re.compile(rb'"cache_creation_input_tokens"\s*:\s*(\d+)')
_RE_CACHE_READ_TOKENS = re.compile(rb'"cache_read_input_tokens"\s*:\s*(\d+)')
_RE_OUTPUT_TOKENS = re.compile(rb'"(?:output_tokens|completion_tokens)"\s*:\s*(\d+)')


async def _read_response_head(reader: asyncio.StreamReader) -> tuple[bytes, dict[str, str]] | None:
    """Read status line + headers; return (raw_head_bytes, headers) or None on EOF."""
    first = await reader.readline()
    if not first:
        return None
    raw = [first]
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            raw.append(b"\r\n")
            break
        raw.append(line)
        s = line.decode("latin-1", errors="ignore")
        if ":" in s:
            k, v = s.split(":", 1)
            headers[k.strip()] = v.strip()
    return b"".join(raw), headers


def _strip_accept_encoding(headers: dict[str, str] | None) -> None:
    """Remove Accept-Encoding so the upstream replies uncompressed (readable for scrape)."""
    if not headers:
        return
    for key in [k for k in headers if k.lower() == "accept-encoding"]:
        headers.pop(key, None)


def _set_request_body(req: Any, new_body: bytes) -> None:
    """Replace the outbound request body and fix framing headers.

    After anonymization the body length changes, so we set an explicit
    Content-Length and drop any chunked Transfer-Encoding (we re-send the whole
    body in one piece via ``write_http_message``).
    """
    req.body = new_body
    headers = req.headers or {}
    for key in [k for k in headers if k.lower() == "transfer-encoding"]:
        headers.pop(key, None)
    cl_key = next((k for k in headers if k.lower() == "content-length"), None)
    if cl_key is not None:
        headers[cl_key] = str(len(new_body))
    else:
        headers["Content-Length"] = str(len(new_body))
    req.headers = headers


def _anthropic_total_input_tokens(
    input_tokens: int | None,
    cache_creation: int | None,
    cache_read: int | None,
) -> int:
    """Billed/context input size for Anthropic (uncached + cache write + cache read)."""
    return int(input_tokens or 0) + int(cache_creation or 0) + int(cache_read or 0)


def _scan_usage(meta: dict[str, Any], data: bytes) -> None:
    """Update model / token counts from a raw response piece (JSON or SSE event).

    Called incrementally while streaming, so it works for arbitrarily long streams:
    Anthropic emits usage in ``message_start`` / ``message_delta``; OpenAI emits ``usage``
    in the last chunk before ``[DONE]``.

    Critical: Claude Code / Anthropic prompt caching often reports ``input_tokens`` as a
    tiny uncached tail (e.g. 2) while the real prompt lives in
    ``cache_read_input_tokens`` / ``cache_creation_input_tokens``. Counting only
    ``input_tokens`` under-reports dashboard "tokens billed" by orders of magnitude.
    """
    if "model" not in meta:
        m = _RE_MODEL.search(data)
        if m:
            meta["model"] = m.group(1).decode("latin-1", errors="ignore")[:256]

    input_hit = False
    for value in _RE_INPUT_TOKENS.findall(data):
        meta["_usage_input"] = int(value)
        input_hit = True
    for value in _RE_CACHE_CREATION_TOKENS.findall(data):
        meta["cache_creation_input_tokens"] = int(value)
        input_hit = True
    for value in _RE_CACHE_READ_TOKENS.findall(data):
        meta["cache_read_input_tokens"] = int(value)
        input_hit = True
    if input_hit:
        total = _anthropic_total_input_tokens(
            meta.get("_usage_input"),
            meta.get("cache_creation_input_tokens"),
            meta.get("cache_read_input_tokens"),
        )
        # Keep the max seen total so a later delta that only repeats a small
        # ``input_tokens`` without cache fields cannot wipe a fuller snapshot.
        prev = int(meta.get("tokens_input") or 0)
        if total > prev:
            meta["tokens_input"] = total

    for value in _RE_OUTPUT_TOKENS.findall(data):
        v = int(value)
        if v > meta.get("tokens_output", 0):
            meta["tokens_output"] = v


def _mark_first_byte_ttft(meta: dict[str, Any], ttft_anchor: float | None) -> None:
    """Record time-to-first-body-byte (ms) once — dashboard TTFT KPI for egress."""
    if ttft_anchor is None or meta.get("ttft_ms") is not None:
        return
    meta["ttft_ms"] = round((time.monotonic() - ttft_anchor) * 1000.0, 2)


async def _stream_response_body(
    up_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    headers: dict[str, str],
    *,
    ttft_anchor: float | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Forward the response body to the client in real time and scrape metadata live.

    Preserves the original framing (chunked / content-length / close-delimited) so the
    client (Claude Code, SDK…) sees a valid streamed HTTP response — fixes the hang
    caused by buffering SSE responses fully before forwarding. Returns
    ``(scraped_meta, head_sample)`` where head_sample is a bounded prefix used to parse
    plain (non-streamed) JSON responses precisely.
    """
    meta: dict[str, Any] = {}
    head = bytearray()

    def _on_piece(data: bytes) -> None:
        if data:
            _mark_first_byte_ttft(meta, ttft_anchor)
        if len(head) < _CLASSIFY_CAP:
            head.extend(data[: _CLASSIFY_CAP - len(head)])
        _scan_usage(meta, data)

    te = (headers.get("Transfer-Encoding") or headers.get("transfer-encoding") or "").lower()
    cl = headers.get("Content-Length") or headers.get("content-length")

    if "chunked" in te:
        while True:
            size_line = await up_reader.readline()
            if not size_line:
                break
            client_writer.write(size_line)
            size_hex = size_line.split(b";", 1)[0].strip()
            try:
                size = int(size_hex, 16)
            except ValueError:
                break
            if size == 0:
                trailer = await up_reader.readline()
                client_writer.write(trailer or b"\r\n")
                await client_writer.drain()
                break
            data = await up_reader.readexactly(size)
            trailing = await up_reader.readexactly(2)  # CRLF after chunk data
            client_writer.write(data)
            client_writer.write(trailing)
            await client_writer.drain()
            _on_piece(data)
    elif cl is not None:
        try:
            remaining = int(cl)
        except ValueError:
            remaining = 0
        while remaining > 0:
            chunk = await up_reader.read(min(_BUF, remaining))
            if not chunk:
                break
            client_writer.write(chunk)
            await client_writer.drain()
            _on_piece(chunk)
            remaining -= len(chunk)
    else:
        # Close-delimited: stream until upstream EOF.
        while True:
            chunk = await up_reader.read(_BUF)
            if not chunk:
                break
            client_writer.write(chunk)
            await client_writer.drain()
            _on_piece(chunk)

    return meta, bytes(head)


async def _buffer_response_body(
    up_reader: asyncio.StreamReader,
    headers: dict[str, str],
    *,
    ttft_anchor: float | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Read the full upstream response body without forwarding (for sync PII filter)."""
    meta: dict[str, Any] = {}
    body = bytearray()

    def _on_piece(data: bytes) -> None:
        if data:
            _mark_first_byte_ttft(meta, ttft_anchor)
        body.extend(data)
        _scan_usage(meta, data)

    te = (headers.get("Transfer-Encoding") or headers.get("transfer-encoding") or "").lower()
    cl = headers.get("Content-Length") or headers.get("content-length")

    if "chunked" in te:
        while True:
            size_line = await up_reader.readline()
            size_hex = size_line.split(b";", 1)[0].strip()
            try:
                size = int(size_hex, 16)
            except ValueError:
                break
            if size == 0:
                await up_reader.readline()
                break
            data = await up_reader.readexactly(size)
            await up_reader.readexactly(2)
            _on_piece(data)
    elif cl is not None:
        try:
            remaining = int(cl)
        except ValueError:
            remaining = 0
        while remaining > 0:
            chunk = await up_reader.read(min(_BUF, remaining))
            if not chunk:
                break
            _on_piece(chunk)
            remaining -= len(chunk)
    else:
        while True:
            chunk = await up_reader.read(_BUF)
            if not chunk:
                break
            _on_piece(chunk)

    full = bytes(body)
    return meta, full


async def _safe_client_drain(client_writer: asyncio.StreamWriter) -> bool:
    """Drain client socket; return False when the peer closed (BrokenPipe, etc.)."""
    try:
        await client_writer.drain()
        return True
    except Exception as exc:
        if _is_client_abort(exc):
            return False
        raise


async def _foreach_response_body_piece(
    up_reader: asyncio.StreamReader,
    headers: dict[str, str],
    on_piece: Callable[[bytes], Awaitable[bool]],
) -> None:
    """Invoke ``on_piece`` for each deframed body chunk.

    Uses a plain loop (not an async generator) so client abort / task cancel
    never triggers ``aclose(): asynchronous generator is already running`` on
    the OpenAI→Anthropic stream-translate hot path.
    """
    te = (headers.get("Transfer-Encoding") or headers.get("transfer-encoding") or "").lower()
    cl = headers.get("Content-Length") or headers.get("content-length")

    async def _deliver(data: bytes) -> bool:
        if not data:
            return True
        return await on_piece(data)

    if "chunked" in te:
        while True:
            size_line = await up_reader.readline()
            if not size_line:
                break
            size_hex = size_line.split(b";", 1)[0].strip()
            try:
                size = int(size_hex, 16)
            except ValueError:
                break
            if size == 0:
                await up_reader.readline()
                break
            data = await up_reader.readexactly(size)
            await up_reader.readexactly(2)
            if not await _deliver(data):
                break
    elif cl is not None:
        try:
            remaining = int(cl)
        except ValueError:
            remaining = 0
        while remaining > 0:
            chunk = await up_reader.read(min(_BUF, remaining))
            if not chunk:
                break
            if not await _deliver(chunk):
                break
            remaining -= len(chunk)
    else:
        while True:
            chunk = await up_reader.read(_BUF)
            if not chunk:
                break
            if not await _deliver(chunk):
                break


def _write_chunked_piece(client_writer: asyncio.StreamWriter, data: bytes) -> int:
    """Write one HTTP/1.1 chunk; returns bytes written (framing + payload)."""
    if not data:
        return 0
    frame = f"{len(data):x}\r\n".encode("ascii") + data + b"\r\n"
    client_writer.write(frame)
    return len(frame)


async def _stream_openai_to_anthropic(
    up_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_headers: dict[str, str],
    *,
    client_model: str | None,
    ttft_anchor: float | None = None,
) -> tuple[dict[str, Any], bytes, int]:
    """Translate OpenAI SSE → Anthropic SSE while streaming to the client.

    Returns ``(scraped_meta, anthropic_head_sample, response_bytes)``.
    """
    translator = OpenAIToAnthropicSSETranslator(client_model=client_model)
    meta: dict[str, Any] = {}
    head = bytearray()
    response_bytes = 0
    client_alive = True

    status_line = b"HTTP/1.1 200 OK\r\n"
    hdr = (
        b"Content-Type: text/event-stream\r\n"
        b"Cache-Control: no-cache\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    client_writer.write(status_line + hdr)
    client_alive = await _safe_client_drain(client_writer)
    response_bytes += len(status_line) + len(hdr)

    async def _emit_events(events: list[bytes]) -> bool:
        nonlocal response_bytes, client_alive
        if not client_alive or not events:
            return client_alive
        for event in events:
            response_bytes += _write_chunked_piece(client_writer, event)
            if len(head) < _CLASSIFY_CAP:
                head.extend(event[: _CLASSIFY_CAP - len(head)])
        return await _safe_client_drain(client_writer)

    async def _on_upstream_piece(piece: bytes) -> bool:
        nonlocal client_alive
        if not client_alive:
            return False
        if piece:
            _mark_first_byte_ttft(meta, ttft_anchor)
        _scan_usage(meta, piece)
        client_alive = await _emit_events(translator.feed(piece))
        return client_alive

    try:
        await _foreach_response_body_piece(up_reader, upstream_headers, _on_upstream_piece)
    finally:
        if client_alive:
            try:
                client_alive = await _emit_events(translator.finish())
                if client_alive:
                    client_writer.write(b"0\r\n\r\n")
                    client_alive = await _safe_client_drain(client_writer)
                    response_bytes += 5
            except Exception as exc:
                if not _is_client_abort(exc):
                    raise

    if translator.model and not meta.get("model"):
        meta["model"] = translator.model
    if translator.input_tokens and not meta.get("tokens_input"):
        meta["tokens_input"] = translator.input_tokens
    if translator.output_tokens and not meta.get("tokens_output"):
        meta["tokens_output"] = translator.output_tokens
    return meta, bytes(head), response_bytes


async def _write_http_response(
    client_writer: asyncio.StreamWriter,
    *,
    status_code: int,
    content_type: str,
    body: bytes,
) -> int:
    """Write a complete HTTP/1.1 response; returns total bytes written."""
    head = (
        f"HTTP/1.1 {status_code} OK\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("latin-1")
    client_writer.write(head + body)
    await client_writer.drain()
    return len(head) + len(body)


def _maybe_decompress(body: bytes, content_encoding: str | None) -> bytes:
    """Best-effort decompress for scanning (we still stream the original bytes to client)."""
    if not body or not content_encoding:
        return body
    enc = content_encoding.strip().lower()
    try:
        if enc in ("gzip", "x-gzip"):
            import gzip

            return gzip.decompress(body)
        if enc == "deflate":
            import zlib

            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
        if enc == "br":
            try:
                import brotli  # type: ignore

                return brotli.decompress(body)
            except Exception:
                return body
    except Exception:
        return body
    return body


def _classify_response_payload(host: str, body: bytes) -> dict[str, Any]:
    """Classify an accumulated response body: precise JSON first, then SSE scrape."""
    if not body:
        return {}
    stripped = body.lstrip()
    if stripped[:1] in (b"{", b"["):
        try:
            import json

            parsed = json.loads(body.decode("utf-8", errors="ignore"))
            if isinstance(parsed, dict):
                cls = classify_egress(host=host, content_type="application/json", body=parsed)
                return {
                    "model": cls.model,
                    "tokens_output": cls.tokens_output,
                    "tokens_input": cls.tokens_input,
                    "flow_kind": cls.flow_kind,
                }
        except Exception:
            pass
    meta: dict[str, Any] = {}
    _scan_usage(meta, body)
    return meta


def default_execution_trace_id() -> str | None:
    raw = (os.environ.get("TOKENSAVER_EXECUTION_TRACE_ID") or "").strip()
    return raw[:128] or None


def _trace_from_headers(headers: dict[str, str] | None) -> str | None:
    if not headers:
        return None
    for k, v in headers.items():
        if k.lower() == "x-tokensaver-execution-trace-id" and v.strip():
            return v.strip()[:128]
    return default_execution_trace_id()


def _loop_from_headers(headers: dict[str, str] | None) -> str | None:
    """ACP-9 — propagate client loop id into egress audit attrs.

    Prefer ``X-Tokensaver-Loop-Id`` when present; always fall back to
    ``TOKENSAVER_LOOP_ID`` (serve --loop / sourced env). An empty/None
    headers dict must not skip the env fallback — that left Boucles at
    iters=0 while Flux still showed captures.
    """
    if headers:
        for k, v in headers.items():
            if k.lower() == "x-tokensaver-loop-id" and str(v).strip():
                return str(v).strip()[:128]
    env = (os.environ.get("TOKENSAVER_LOOP_ID") or "").strip()
    return env[:128] or None


def _attach_loop_meta(meta: dict[str, Any], headers: dict[str, str] | None) -> None:
    loop_id = _loop_from_headers(headers)
    if loop_id:
        meta["loop_id"] = loop_id
    iter_val: int | None = None
    if headers:
        for k, v in headers.items():
            if k.lower() == "x-tokensaver-loop-iteration" and str(v).strip():
                try:
                    iter_val = int(str(v).strip())
                except ValueError:
                    pass
                break
    if iter_val is None:
        env_iter = (os.environ.get("TOKENSAVER_LOOP_ITERATION") or "").strip()
        if env_iter:
            try:
                iter_val = int(env_iter)
            except ValueError:
                pass
    if iter_val is not None:
        meta["loop_iteration"] = iter_val

async def _start_tls_server(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ssl_context: ssl.SSLContext,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    protocol = writer._protocol
    tls_transport = await loop.start_tls(
        writer.transport,
        protocol,
        ssl_context,
        server_side=True,
    )
    # After start_tls(), the SSL layer delivers DECRYPTED application data to the
    # SAME `protocol` instance, which keeps feeding the ORIGINAL `reader`. We must
    # therefore keep reading from `reader` (not a fresh StreamReader) and just
    # re-point the writer/protocol at the new TLS transport. Returning a brand-new
    # StreamReader here would hang forever because nothing would ever feed it.
    writer._transport = tls_transport
    try:
        protocol._transport = tls_transport
    except Exception:
        pass
    return reader, writer


async def handle_connect_mitm(
    host: str,
    port: int,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
    ca: MitmCA,
) -> None:
    """Terminate TLS locally and relay HTTP exchanges.

    Each TLS session runs in its own asyncio task (see ``proxy.serve``). Multiple
    concurrent sessions execute their sync pipeline (cache/RAG/PII) in parallel;
    only requests on the *same* keep-alive connection are serialized.
    """
    logger.debug("MITM connect host=%s port=%s", host, port)
    client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_writer.drain()

    try:
        client_ssl_ctx = ca.server_ssl_context(host)
        client_reader, client_writer = await _start_tls_server(client_reader, client_writer, client_ssl_ctx)
    except Exception as exc:
        client_abort = _is_client_abort_tls(exc)
        if client_abort:
            # Routine: client rejected MITM leaf (CA not trusted) or cancelled CONNECT.
            global _tls_abort_hint_shown
            host_key = (host or "").lower()
            if host_key not in _tls_abort_hosts_logged:
                _tls_abort_hosts_logged.add(host_key)
                logger.warning(
                    "mitm TLS handshake aborted by client host=%s (%s) — "
                    "Claude Code does not use the system keychain; launch it with:  "
                    "tokensaver-egress claude --no-start",
                    host,
                    type(exc).__name__,
                )
                if not _tls_abort_hint_shown:
                    _tls_abort_hint_shown = True
                    logger.warning(
                        "  (further handshake aborts are logged at DEBUG; "
                        "do not run bare `claude` with only HTTPS_PROXY set)"
                    )
            else:
                logger.debug(
                    "mitm TLS handshake aborted by client host=%s (%s)",
                    host,
                    type(exc).__name__,
                )
        else:
            logger.warning("mitm TLS handshake failed host=%s", host, exc_info=True)
        try:
            detail = str(exc)[:800] or type(exc).__name__
            if client_abort:
                detail = (
                    f"{type(exc).__name__}: client closed during MITM handshake "
                    f"(untrusted local CA or cancelled CONNECT). {detail}"
                )[:1000]
            await sink.record(
                EgressRecord(
                    host=host,
                    capture_mode="mitm",
                    status_code=525,  # SSL handshake failed (Cloudflare-style)
                    execution_trace_id=default_execution_trace_id(),
                    attrs={
                        "flow_kind": "mcp" if is_mcp_host(host) else "unknown",
                        "error_type": "tls_handshake_failed",
                        "error_detail": detail,
                        "tls_client_abort": client_abort,
                    },
                )
            )
        except Exception:
            logger.debug("failed to audit TLS handshake failure host=%s", host, exc_info=True)
        try:
            client_writer.close()
        except Exception:
            pass
        return

    while True:
        await _relay_one_exchange(host, port, client_reader, client_writer, sink, ca)
        # On client EOF or error, _relay_one_exchange marks the reader at_eof();
        # otherwise we loop to serve the next keep-alive request on this connection.
        if client_reader.at_eof():
            break

    try:
        client_writer.close()
    except Exception:
        pass


_BLOCKED_STATUS = 403
_BLOCKED_BODY = (
    b'{"error":{"type":"forbidden","message":'
    b'"Blocked by TokenSaver egress policy: this destination is not approved in the catalog."}}'
)


async def _emit_loop_blocked(
    host: str,
    req: Any,
    pre_cls: Any,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
    started: float,
    request_bytes: int,
    verdict: Any,
) -> None:
    """Return 403/429 for ACP-9 loop policy without contacting the upstream LLM."""
    code = int(getattr(verdict, "status_code", 403) or 403)
    body_obj = getattr(verdict, "body", {}) or {}
    reason = ""
    if isinstance(body_obj, dict):
        reason = str(body_obj.get("reason") or "").strip()
    effect = str(getattr(verdict, "effect", "deny") or "deny")
    detail = f"{effect}" + (f" ({reason})" if reason else "")
    hint = ""
    if reason == "GOAL_REQUIRED":
        hint = (
            " Restart with: tokensaver-egress serve --loop --goal \"…\""
            " (or --loop-kind turn_based)."
        )
    payload = {
        "error": {
            "type": "loop_policy_error",
            "code": "LOOP_POLICY_DENIED"
            if getattr(verdict, "effect", "") != "throttle"
            else "LOOP_THROTTLED",
            "message": f"Blocked by TokenSaver loop precheck: {detail}.{hint}",
            "precheck": body_obj,
        }
    }
    body = json.dumps(payload).encode("utf-8")
    http_reason = "Forbidden" if code != 429 else "Too Many Requests"
    head = (
        f"HTTP/1.1 {code} {http_reason}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("latin-1")
    try:
        client_writer.write(head + body)
        await client_writer.drain()
    except Exception:
        logger.debug("failed to write loop-blocked response host=%s", host, exc_info=True)

    meta: dict[str, Any] = {
        "flow_kind": pre_cls.flow_kind,
        "provider": pre_cls.provider,
        "model": pre_cls.model,
        "method": pre_cls.method,
        "path": req.path if req else None,
        "blocked": True,
        "block_reason": "loop_precheck",
        "loop_precheck": getattr(verdict, "body", {}) or {},
    }
    _attach_loop_meta(meta, req.headers if req else None)
    trace_id = _trace_from_headers(req.headers if req else None) or default_execution_trace_id()
    with egress_span(
        "tokensaver_egress.mitm.loop_blocked",
        host=host,
        capture_mode="mitm",
        execution_trace_id=trace_id,
        attrs=meta,
    ):
        await sink.record(
            EgressRecord(
                host=host,
                capture_mode="mitm",
                request_bytes=request_bytes,
                response_bytes=len(head) + len(body),
                latency_ms=int((time.monotonic() - started) * 1000),
                status_code=code,
                attrs=meta,
                execution_trace_id=trace_id,
            )
        )


async def _emit_blocked(
    host: str,
    req: Any,
    pre_cls: Any,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
    started: float,
    request_bytes: int,
) -> None:
    """Return a 403 to the client (no upstream contact) and audit the blocked flow."""
    body = _BLOCKED_BODY
    head = (
        f"HTTP/1.1 {_BLOCKED_STATUS} Forbidden\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("latin-1")
    try:
        client_writer.write(head + body)
        await client_writer.drain()
    except Exception:
        logger.debug("failed to write blocked response host=%s", host, exc_info=True)

    meta: dict[str, Any] = {
        "flow_kind": pre_cls.flow_kind,
        "provider": pre_cls.provider,
        "model": pre_cls.model,
        "method": pre_cls.method,
        "path": req.path if req else None,
        "args_hash": pre_cls.args_hash,
        "content_type": req.content_type if req else None,
        "blocked": True,
        "block_reason": "egress_policy_deny",
    }
    if bodies_enabled():
        meta["request_body"] = prepare_body(req.body if req else None)
        meta["request_headers"] = sanitize_headers(req.headers if req else None)
    trace_id = _trace_from_headers(req.headers if req else None) or default_execution_trace_id()
    _attach_loop_meta(meta, req.headers if req else None)
    with egress_span("tokensaver_egress.mitm.blocked", host=host, capture_mode="mitm", execution_trace_id=trace_id, attrs=meta):
        await sink.record(
            EgressRecord(
                host=host,
                capture_mode="mitm",
                request_bytes=request_bytes,
                response_bytes=len(head) + len(body),
                latency_ms=int((time.monotonic() - started) * 1000),
                status_code=_BLOCKED_STATUS,
                execution_trace_id=trace_id,
                attrs=meta,
            )
        )
    logger.info("MITM blocked host=%s path=%s provider=%s model=%s", host, req.path if req else None, pre_cls.provider, pre_cls.model)


def _merge_rag_audit_attrs(meta: dict[str, Any], rag_result: Any | None) -> None:
    """Attach RAG enrichment metadata from a backend rag-enrich result."""
    if rag_result is None:
        return
    if getattr(rag_result, "rag_evaluated", False):
        meta["rag_evaluated"] = True
    if getattr(rag_result, "modified", False):
        meta["rag_enriched"] = True
    chunks = getattr(rag_result, "chunks_count", None)
    if chunks is not None:
        meta["rag_chunks_count"] = int(chunks)
        if int(chunks) > 0:
            meta["rag_enriched"] = True
    embedding_error = getattr(rag_result, "embedding_error", None)
    error_code = getattr(rag_result, "error_code", None)
    if embedding_error:
        meta["rag_embedding_error"] = str(embedding_error)
    if error_code:
        meta["error_type"] = error_code
    skipped = getattr(rag_result, "skipped_reason", None)
    if skipped:
        meta["rag_skipped_reason"] = str(skipped)
    detail = getattr(rag_result, "detail", None)
    if isinstance(detail, dict) and detail:
        meta["rag"] = detail
        comp = detail.get("compression")
        if isinstance(comp, dict) and comp:
            meta["compression"] = comp


def _compression_has_effect(detail: dict[str, Any]) -> bool:
    if detail.get("tokens_before") is not None and detail.get("tokens_after") is not None:
        return int(detail["tokens_after"]) < int(detail["tokens_before"])
    if detail.get("blocks"):
        return True
    ratio = detail.get("compression_ratio")
    if ratio is not None:
        try:
            return float(ratio) < 1.0
        except (TypeError, ValueError):
            pass
    return False


def _merge_compression_audit_attrs(meta: dict[str, Any], compression_result: Any | None) -> None:
    """Attach tool-output / content-aware compression metadata from compress-tool-outputs."""
    if compression_result is None:
        return
    if getattr(compression_result, "evaluated", False):
        meta["compression_evaluated"] = True
    detail = getattr(compression_result, "detail", None)
    skipped = getattr(compression_result, "skipped_reason", None)
    new_detail: dict[str, Any] | None = None
    if isinstance(detail, dict) and detail:
        new_detail = detail
    elif skipped:
        new_detail = {
            "mode": "content_aware",
            "skipped_reason": str(skipped),
        }
    if not new_detail:
        return
    existing = meta.get("compression")
    if isinstance(existing, dict) and _compression_has_effect(existing):
        if not _compression_has_effect(new_detail):
            return
    meta["compression"] = new_detail
    segments = getattr(compression_result, "segments_count", None)
    if segments is not None and int(segments) > 0:
        meta["compression"].setdefault("segments_count", int(segments))


def _finalize_rag_audit_from_body(meta: dict[str, Any], req_body: bytes | None) -> None:
    """Flag enriched flows when the captured request contains injected RAG context.

    Full pipeline-grade detail is produced by the backend rag-enrich API (and hydrated
    on egress detail read). Do not synthesize weak parse-only detail here — it would
    overwrite real retrieval metrics.
    """
    if not req_body or b"<RAG documents>" not in req_body:
        return
    meta["rag_enriched"] = True
    meta["rag_evaluated"] = True
    stored = meta.get("rag") if isinstance(meta.get("rag"), dict) else {}
    chunks = int(stored.get("chunks_count") or meta.get("rag_chunks_count") or 0)
    if chunks > 0:
        meta["rag_chunks_count"] = chunks


def _merge_cache_audit_attrs(meta: dict[str, Any], cache_result: Any | None) -> None:
    """Attach cache evaluation / error metadata from a backend cache-lookup result."""
    if cache_result is None:
        return
    if getattr(cache_result, "cache_evaluated", False):
        meta["cache_evaluated"] = True
    if getattr(cache_result, "hit", False):
        meta["cache_hit"] = True
        meta["cache_hit_type"] = getattr(cache_result, "hit_type", None) or "exact"
        meta["cache_layer"] = meta["cache_hit_type"]
    embedding_error = getattr(cache_result, "embedding_error", None)
    error_code = getattr(cache_result, "error_code", None)
    if embedding_error:
        meta["cache_embedding_error"] = str(embedding_error)
        meta["error_type"] = str(error_code or "CACHE_EMBEDDING_ERROR")
        meta["error_detail"] = str(embedding_error)
    if getattr(cache_result, "similarity_score", None) is not None:
        meta["similarity_score"] = cache_result.similarity_score


def _merge_pii_response_meta(meta: dict[str, Any], pii_response: dict[str, Any] | None) -> None:
    """Attach response-side PII filtering metadata (live filter or cache-hit parity)."""
    if not isinstance(pii_response, dict) or not pii_response.get("detected"):
        return
    types = pii_response.get("types") if isinstance(pii_response.get("types"), dict) else {}
    resp_counts = {str(k): int(v) for k, v in types.items() if int(v or 0) > 0}
    total = int(pii_response.get("total") or sum(resp_counts.values()))
    if total <= 0:
        return
    meta["pii_detected"] = True
    meta["pii_response_filtered"] = True
    existing = meta.get("pii") if isinstance(meta.get("pii"), dict) else {}
    req_counts = existing.get("request") if isinstance(existing.get("request"), dict) else {}
    merged_types: dict[str, int] = {}
    for src in (existing.get("types"), req_counts, resp_counts):
        if isinstance(src, dict):
            for k, v in src.items():
                merged_types[str(k)] = merged_types.get(str(k), 0) + int(v or 0)
    meta["pii"] = {
        **existing,
        "detected": True,
        "total": int(existing.get("total") or 0) + total,
        "types": merged_types or resp_counts,
        "response": resp_counts,
        "response_filtered": True,
    }


def _merge_cache_hit_pii_response(meta: dict[str, Any], cache_result: Any | None) -> None:
    """Attach response-side PII filtering metadata (pipeline cache-hit ``filter_llm_response``)."""
    if cache_result is None:
        return
    pii_response = getattr(cache_result, "pii_response", None)
    _merge_pii_response_meta(meta, pii_response if isinstance(pii_response, dict) else None)


async def _emit_cached_response(
    host: str,
    req: Any,
    pre_cls: Any,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
    started: float,
    request_bytes: int,
    cache_result: Any,
    *,
    pii_anonymized: bool = False,
    rag_result: Any | None = None,
    pipeline_modules: list[str] | None = None,
) -> None:
    """Write a backend-synthesized cached response to the client (no upstream contact).

    The proxy is a thin transport here: the backend already produced the ready-to-emit
    HTTP body (JSON or Anthropic SSE), so we only frame the HTTP headers and write it.
    """
    body_bytes = cache_result.synth_body or b""
    content_type = cache_result.synth_content_type or "application/json"
    status_code = int(cache_result.synth_status or 200)
    head = (
        f"HTTP/1.1 {status_code} OK\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("latin-1")
    try:
        client_writer.write(head + body_bytes)
        await client_writer.drain()
    except Exception:
        logger.debug("failed to write cached response host=%s", host, exc_info=True)

    meta: dict[str, Any] = {
        "flow_kind": pre_cls.flow_kind,
        "provider": pre_cls.provider,
        "model": cache_result.model or pre_cls.model,
        "method": pre_cls.method,
        "path": req.path if req else None,
        "args_hash": pre_cls.args_hash,
        "content_type": req.content_type if req else None,
        "cache_hit": True,
        "cache_hit_type": cache_result.hit_type or "exact",
        "cache_layer": cache_result.hit_type or "exact",
        "cache_evaluated": True,
        "tokens_input": int(cache_result.tokens_input or 0),
        "tokens_output": int(cache_result.tokens_output or 0),
        "pipeline_sync": True,
    }
    if pipeline_modules:
        meta["pipeline_modules"] = pipeline_modules
    if cache_result.similarity_score is not None:
        meta["similarity_score"] = cache_result.similarity_score
    if pii_anonymized:
        meta["pii_detected"] = True
    _merge_cache_hit_pii_response(meta, cache_result)
    _merge_rag_audit_attrs(meta, rag_result)
    if bodies_enabled():
        meta["request_body"] = prepare_body(req.body if req else None)
        meta["response_body"] = prepare_body(body_bytes)
        meta["request_headers"] = sanitize_headers(req.headers if req else None)
        _finalize_rag_audit_from_body(meta, req.body if req else None)
    trace_id = _trace_from_headers(req.headers if req else None) or default_execution_trace_id()
    _attach_loop_meta(meta, req.headers if req else None)
    with egress_span("tokensaver_egress.mitm.cache_hit", host=host, capture_mode="mitm", execution_trace_id=trace_id, attrs=meta):
        await sink.record(
            EgressRecord(
                host=host,
                capture_mode="mitm",
                request_bytes=request_bytes,
                response_bytes=len(head) + len(body_bytes),
                latency_ms=int((time.monotonic() - started) * 1000),
                status_code=status_code,
                execution_trace_id=trace_id,
                attrs=meta,
            )
        )
    logger.info(
        "MITM cache hit host=%s path=%s type=%s model=%s",
        host,
        req.path if req else None,
        meta.get("cache_hit_type"),
        meta.get("model"),
    )


async def _open_upstream_connection(
    host: str,
    port: int,
    ca: MitmCA,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    upstream_ssl = ca.client_ssl_context()
    return await asyncio.open_connection(host, port, ssl=upstream_ssl, server_hostname=host)


async def _relay_one_exchange(
    host: str,
    port: int,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
    ca: MitmCA,
) -> None:
    """One request→response cycle with streamed response. Closes the client on EOF/error."""
    started = time.monotonic()
    status_code = 502
    up_writer: asyncio.StreamWriter | None = None

    try:
        req = await read_http_message(client_reader)
        if req is None:
            client_reader.feed_eof()
            return

        request_bytes = len(req.body or b"") + sum(len(f"{k}: {v}\r\n") for k, v in (req.headers or {}).items())

        # Classify the request once; reused for enforcement, anonymization and audit.
        req_cls = classify_egress(
            host=host,
            content_type=req.content_type,
            body=req.json_body(),
            path=req.path if req else None,
        )

        utility_llm = is_client_utility_llm_request(
            req.path if req else None,
            body=req.body if req else None,
            model=getattr(req_cls, "model", None),
        )
        llm_flow_kind = (req_cls.flow_kind or "") == "llm"
        if llm_flow_kind:
            await warm_llm_policies()

        routing_meta: dict[str, Any] | None = None
        routing_applied = False
        if llm_flow_kind and not utility_llm:
            routing_client = get_routing_client()
            routing_on = governance_routing_enabled()
            if routing_on is None:
                routing_on = routing_client.enabled and await routing_client.routing_enabled()
            elif not routing_client.enabled:
                routing_on = False
            if routing_on:
                try:
                    accept = ""
                    if req.headers:
                        accept = (req.headers.get("Accept") or req.headers.get("accept") or "").lower()
                    client_wanted_stream = bool(
                        request_body_wants_stream(req.body if req else None)
                        or "text/event-stream" in accept
                    )
                    routed = await routing_client.apply(
                        bytes(req.body) if req.body else b"",
                        host=host,
                        path=req.path if req else None,
                        content_type=req.content_type,
                        flow_kind=req_cls.flow_kind,
                    )
                    if routed and routed.applied and routed.body:
                        client_model_before = req_cls.model
                        new_body = routed.body
                        # Cross-provider: keep streaming when Claude Code asked for SSE.
                        if (
                            client_wanted_stream
                            and routed.client_format == "anthropic"
                            and routed.upstream_format == "openai"
                        ):
                            try:
                                parsed = json.loads(new_body.decode("utf-8"))
                                if isinstance(parsed, dict):
                                    parsed["stream"] = True
                                    # Ask OpenAI-compat providers for usage on the final chunk.
                                    parsed.setdefault("stream_options", {"include_usage": True})
                                    new_body = json.dumps(parsed, ensure_ascii=False).encode("utf-8")
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                pass
                        _set_request_body(req, new_body)
                        if routed.force_provider:
                            req_cls.provider = routed.force_provider
                        if routed.force_model:
                            req_cls.model = routed.force_model
                        if routed.upstream_host:
                            host = routed.upstream_host
                            if req.headers:
                                req.headers["Host"] = routed.upstream_host
                        if routed.upstream_path and req:
                            req.path = routed.upstream_path
                        if routed.upstream_headers and req.headers is not None:
                            for hk in list(req.headers):
                                lk = hk.lower()
                                if lk in ("authorization", "x-api-key", "api-key"):
                                    req.headers.pop(hk, None)
                            for hk, hv in routed.upstream_headers.items():
                                req.headers[hk] = hv
                        routing_applied = True
                        routing_meta = {
                            "force_region": routed.force_region,
                            "client_format": routed.client_format,
                            "upstream_format": routed.upstream_format,
                            "client_model": client_model_before or routed.force_model,
                            "client_stream": client_wanted_stream,
                        }
                        request_bytes = len(req.body or b"") + sum(
                            len(f"{k}: {v}\r\n") for k, v in (req.headers or {}).items()
                        )
                except Exception:
                    logger.debug("egress model_routing apply failed (fail-open)", exc_info=True)

        # Pre-flight enforcement (ACP-4 enforce / ACP-7): ask the backend whether this
        # destination is allowed BEFORE forwarding. A deny returns 4xx to the client
        # and never contacts the provider (0 tokens).
        connect_task: asyncio.Task[tuple[asyncio.StreamReader, asyncio.StreamWriter]] | None = None
        preflight_known_idle = utility_llm or governance_preflight_needed() is False
        if llm_flow_kind and not routing_applied and preflight_known_idle:
            connect_task = asyncio.create_task(
                _open_upstream_connection(host, port, ca),
                name="egress-upstream-connect-early",
            )

        enforcer = get_enforcer()
        if enforcer.enabled and (req_cls.flow_kind or "") in ("llm", "mcp"):
            allowed = await enforcer.authorize(
                flow_kind=req_cls.flow_kind,
                provider=req_cls.provider,
                model=req_cls.model,
                host=host,
            )
            if not allowed:
                if connect_task is not None and not connect_task.done():
                    connect_task.cancel()
                await _emit_blocked(
                    host,
                    req,
                    req_cls,
                    client_writer,
                    sink,
                    started,
                    request_bytes,
                )
                client_reader.feed_eof()
                return

        # ACP-9 — optional soft gate: honour loop precheck before upstream LLM call
        if req_cls.flow_kind == "llm":
            try:
                from tokensaver_egress.loop_precheck import loop_precheck_enabled, precheck_loop_call

                if loop_precheck_enabled():
                    loop_id = _loop_from_headers(req.headers)
                    iter_n = None
                    if req.headers:
                        for k, v in req.headers.items():
                            if k.lower() == "x-tokensaver-loop-iteration" and str(v).strip():
                                try:
                                    iter_n = int(str(v).strip())
                                except ValueError:
                                    pass
                                break
                    if loop_id:
                        verdict = await asyncio.to_thread(
                            precheck_loop_call, loop_id=loop_id, iteration=iter_n
                        )
                        if not verdict.allowed:
                            await _emit_loop_blocked(
                                host,
                                req,
                                req_cls,
                                client_writer,
                                sink,
                                started,
                                request_bytes,
                                verdict,
                            )
                            client_reader.feed_eof()
                            return
            except Exception:
                logger.debug("loop precheck skipped", exc_info=True)

        # Pipeline-parity MITM orchestration (sync): Model routing → Cache → RAG → Compression → PII → LLM.
        raw_body_for_cache = bytes(req.body) if req.body else b""
        anonymized_pii: dict[str, Any] | None = None
        pii_anonymized = False
        rag_result: Any | None = None
        compression_result: Any | None = None
        cache_client = get_cache_client()
        cache_result: Any | None = None
        preflight_modules: list[str] = []

        if llm_flow_kind:
            if utility_llm:
                preflight = EgressPreflightResult(request_bytes=request_bytes)
                preflight.modules_run.append("utility_fast_path")
            else:
                preflight_needed = governance_preflight_needed()
                if preflight_needed is None:
                    preflight_needed = await is_llm_governance_preflight_needed()
                if preflight_needed:
                    preflight = await run_egress_llm_preflight(
                        req,
                        req_cls=req_cls,
                        host=host,
                        raw_body_for_cache=raw_body_for_cache,
                        request_bytes=request_bytes,
                    )
                else:
                    preflight = EgressPreflightResult(request_bytes=request_bytes)
                    preflight.modules_run.append("governance_idle")
            cache_result = preflight.cache_result
            rag_result = preflight.rag_result
            compression_result = preflight.compression_result
            anonymized_pii = preflight.anonymized_pii
            pii_anonymized = preflight.pii_anonymized
            request_bytes = preflight.request_bytes
            preflight_modules = list(preflight.modules_run)
            if routing_applied:
                preflight_modules.insert(0, "model_routing")

            if preflight.cache_hit and cache_result:
                await _emit_cached_response(
                    host,
                    req,
                    req_cls,
                    client_writer,
                    sink,
                    started,
                    request_bytes,
                    cache_result,
                    rag_result=None,
                    pii_anonymized=pii_anonymized,
                    pipeline_modules=preflight_modules,
                )
                client_reader.feed_eof()
                return

            logger.info(
                "MITM preflight host=%s path=%s modules=%s pii=%s elapsed_ms=%.0f body_bytes=%s",
                host,
                req.path if req else None,
                preflight_modules,
                pii_anonymized,
                preflight.elapsed_ms,
                len(req.body or b""),
            )

        # Force identity encoding so the response body is readable for classification.
        # Without this, providers compress the SSE/JSON (gzip/br) and model + token
        # usage can't be scraped from the stream.
        _strip_accept_encoding(req.headers)

        if connect_task is not None:
            try:
                up_reader, up_writer = await connect_task
            except asyncio.CancelledError:
                up_reader, up_writer = await _open_upstream_connection(host, port, ca)
        else:
            up_reader, up_writer = await _open_upstream_connection(host, port, ca)
        write_http_message(up_writer, req, is_response=False)
        await up_writer.drain()
        # TTFT clock: after the provider request is on the wire (excludes local preflight).
        ttft_anchor = time.monotonic()

        head = await _read_response_head(up_reader)
        if head is None:
            client_reader.feed_eof()
            return
        raw_head, resp_headers = head
        try:
            status_code = int(raw_head.split()[1])
        except (IndexError, ValueError):
            status_code = 200

        # LLM flows: buffer only when response PII filtering may rewrite the body.
        # Otherwise stream (SSE / progressive tokens) — critical for Claude Code latency.
        # Env EGRESS_LLM_BUFFER=1 forces the old sync buffer path for debugging.
        llm_flow = (req_cls.flow_kind or "") == "llm" and status_code < 400
        force_buffer = (os.environ.get("EGRESS_LLM_BUFFER") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        need_routing_translate = bool(
            routing_meta
            and routing_meta.get("client_format")
            and routing_meta.get("upstream_format")
            and routing_meta.get("client_format") != routing_meta.get("upstream_format")
        )
        need_response_pii = False
        if llm_flow and not force_buffer:
            try:
                need_response_pii = await response_pii_filter_active()
            except Exception:
                need_response_pii = False

        ct_up = (resp_headers.get("Content-Type") or resp_headers.get("content-type") or "").lower()
        upstream_is_sse = "text/event-stream" in ct_up
        client_wants_stream = bool(
            (routing_meta and routing_meta.get("client_stream"))
            or request_body_wants_stream(req.body if req else None)
            or upstream_is_sse
            or (
                req
                and req.headers
                and "text/event-stream" in (req.headers.get("Accept") or req.headers.get("accept") or "").lower()
            )
        )
        live_stream_translate = bool(
            llm_flow
            and need_routing_translate
            and not force_buffer
            and not need_response_pii
            and client_wants_stream
            and routing_meta
            and routing_meta.get("client_format") == "anthropic"
            and routing_meta.get("upstream_format") == "openai"
        )
        buffer_llm = llm_flow and (force_buffer or need_response_pii or (need_routing_translate and not live_stream_translate))
        pii_filter_result = None
        emitted_body = b""
        response_bytes = len(raw_head)

        if live_stream_translate:
            logger.info(
                "MITM LLM stream translate openai→anthropic host=%s path=%s",
                host,
                req.path if req else None,
            )
            stream_meta, head_sample, response_bytes = await _stream_openai_to_anthropic(
                up_reader,
                client_writer,
                resp_headers,
                client_model=routing_meta.get("client_model") if routing_meta else req_cls.model,
                ttft_anchor=ttft_anchor if llm_flow else None,
            )
            emitted_body = head_sample
            preflight_modules.append("model_routing_stream")
        elif buffer_llm:
            stream_meta, full_body = await _buffer_response_body(
                up_reader, resp_headers, ttft_anchor=ttft_anchor if llm_flow else None
            )
            body_for_client = full_body
            if need_routing_translate and routing_meta:
                try:
                    tr = await get_routing_client().translate_response(
                        response_sample=full_body,
                        request_body=req.body if req else None,
                        client_format=routing_meta.get("client_format"),
                        upstream_format=routing_meta.get("upstream_format"),
                        upstream_provider=req_cls.provider,
                        client_model=routing_meta.get("client_model"),
                        tokens_input=stream_meta.get("tokens_input") or req_cls.tokens_input,
                        tokens_output=stream_meta.get("tokens_output") or req_cls.tokens_output,
                    )
                    if tr and not tr.passthrough and tr.synth_body is not None:
                        body_for_client = tr.synth_body
                        status_code = tr.synth_status
                        resp_headers = {
                            **(resp_headers or {}),
                            "Content-Type": tr.synth_content_type or "application/json",
                        }
                        preflight_modules.append("model_routing_response")
                except Exception:
                    logger.debug("egress model_routing response translate failed", exc_info=True)
            pii_filter_result = await run_egress_llm_postflight(
                response_body=body_for_client,
                request_body=req.body if req else None,
                provider=req_cls.provider,
                model=stream_meta.get("model") or req_cls.model,
                tokens_input=stream_meta.get("tokens_input") or req_cls.tokens_input,
                tokens_output=stream_meta.get("tokens_output") or req_cls.tokens_output,
            )
            if pii_filter_result is not None:
                preflight_modules.append("pii_response")
            if (
                pii_filter_result is not None
                and not pii_filter_result.passthrough
                and pii_filter_result.synth_body is not None
            ):
                emitted_body = pii_filter_result.synth_body
                response_bytes = await _write_http_response(
                    client_writer,
                    status_code=int(pii_filter_result.synth_status or status_code),
                    content_type=str(pii_filter_result.synth_content_type or "application/json"),
                    body=emitted_body,
                )
            else:
                content_type = (
                    resp_headers.get("Content-Type")
                    or resp_headers.get("content-type")
                    or "application/octet-stream"
                )
                emitted_body = body_for_client
                response_bytes = await _write_http_response(
                    client_writer,
                    status_code=status_code,
                    content_type=content_type,
                    body=emitted_body,
                )
            head_sample = full_body[:_CLASSIFY_CAP] if len(full_body) > _CLASSIFY_CAP else full_body
        else:
            client_writer.write(raw_head)
            await client_writer.drain()
            stream_meta, head_sample = await _stream_response_body(
                up_reader,
                client_writer,
                resp_headers,
                ttft_anchor=ttft_anchor if llm_flow else None,
            )
            response_bytes = len(raw_head) + len(head_sample)
            emitted_body = head_sample
            if llm_flow:
                logger.debug(
                    "MITM LLM stream (no response-PII buffer) host=%s path=%s",
                    host,
                    req.path if req else None,
                )

        # Classify (request already classified above as req_cls; merge scraped response usage).
        # Prefer live-scraped stream metadata; fall back to a precise JSON parse of the head.
        # If the upstream compressed the body anyway (ignoring our stripped Accept-Encoding),
        # decompress a bounded copy so model/usage stay scrapable.
        content_encoding = resp_headers.get("Content-Encoding") or resp_headers.get("content-encoding")
        scan_sample = head_sample
        if content_encoding and not stream_meta.get("tokens_output") and not stream_meta.get("model"):
            scan_sample = _maybe_decompress(head_sample, content_encoding)
        resp_meta = dict(stream_meta)
        if not resp_meta.get("model") or not resp_meta.get("tokens_output"):
            for k, v in _classify_response_payload(host, scan_sample).items():
                if v is not None and not resp_meta.get(k):
                    resp_meta[k] = v
        if req_cls.provider and not resp_meta.get("tokens_output"):
            content_type = resp_headers.get("Content-Type") or resp_headers.get("content-type")
            logger.info(
                "MITM scrape miss host=%s ct=%s enc=%s sample=%r",
                host,
                content_type,
                content_encoding,
                bytes(scan_sample[:160]),
            )
        meta: dict[str, Any] = {
            "flow_kind": resp_meta.get("flow_kind") or req_cls.flow_kind,
            "provider": req_cls.provider,
            "model": resp_meta.get("model") or req_cls.model,
            "method": req_cls.method,
            "path": req.path if req else None,
            "args_hash": req_cls.args_hash,
            "tokens_input": resp_meta.get("tokens_input") or req_cls.tokens_input,
            "tokens_output": resp_meta.get("tokens_output") or req_cls.tokens_output,
            "content_type": req.content_type if req else None,
        }
        for cache_key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            if resp_meta.get(cache_key) is not None:
                meta[cache_key] = resp_meta[cache_key]
        # Drop scrape scratch keys (not for ingest / console).
        resp_meta.pop("_usage_input", None)
        ttft_ms = resp_meta.get("ttft_ms")
        if ttft_ms is not None:
            try:
                ttft_v = float(ttft_ms)
                if ttft_v > 0:
                    meta["ttft_ms"] = round(ttft_v, 2)
            except (TypeError, ValueError):
                pass
        if (req_cls.flow_kind or "") == "llm":
            meta["pipeline_sync"] = True
            if preflight_modules:
                meta["pipeline_modules"] = preflight_modules
        # When the outbound prompt was anonymized, the captured request_body is the
        # masked version (raw PII never persisted). Flag it so the console shows the
        # flow as anonymized and the entity counts that were masked on egress.
        if anonymized_pii is not None:
            meta["pii_detected"] = True
            meta["pii_anonymized"] = anonymized_pii
        if pii_filter_result is not None and pii_filter_result.pii_response:
            _merge_pii_response_meta(meta, pii_filter_result.pii_response)
        _merge_cache_audit_attrs(meta, cache_result)
        _merge_rag_audit_attrs(meta, rag_result)
        _merge_compression_audit_attrs(meta, compression_result)
        if meta.get("cache_embedding_error"):
            logger.warning(
                "MITM cache embedding error host=%s path=%s code=%s",
                host,
                req.path if req else None,
                meta.get("error_type"),
            )
        if meta.get("rag_embedding_error"):
            logger.warning(
                "MITM RAG embedding error host=%s path=%s code=%s",
                host,
                req.path if req else None,
                meta.get("error_type"),
            )
        if bodies_enabled():
            meta["request_body"] = prepare_body(req.body if req else None)
            meta["response_body"] = prepare_body(emitted_body or bytes(scan_sample))
            meta["request_headers"] = sanitize_headers(req.headers if req else None)
            meta["response_headers"] = sanitize_headers(resp_headers)
            _finalize_rag_audit_from_body(meta, req.body if req else None)
        trace_id = _trace_from_headers(req.headers if req else None) or default_execution_trace_id()
        _attach_loop_meta(meta, req.headers if req else None)
        with egress_span("tokensaver_egress.mitm", host=host, capture_mode="mitm", execution_trace_id=trace_id, attrs=meta):
            await sink.record(
                EgressRecord(
                    host=host,
                    capture_mode="mitm",
                    request_bytes=request_bytes,
                    response_bytes=response_bytes,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status_code=status_code,
                    execution_trace_id=trace_id,
                    attrs=meta,
                )
            )
        logger.info(
            "MITM capture host=%s path=%s status=%s flow=%s model=%s tokens_in=%s tokens_out=%s",
            host,
            req.path if req else None,
            status_code,
            meta.get("flow_kind"),
            meta.get("model"),
            meta.get("tokens_input"),
            meta.get("tokens_output"),
        )
        if (
            cache_client.enabled
            and (req_cls.flow_kind or "") == "llm"
            and status_code < 400
            and req.body
            and (emitted_body or scan_sample)
        ):
            # Thin proxy: ship the raw response sample; the backend extracts the
            # assistant text and detects tool use (no provider parsing client-side).
            store_sample = emitted_body if llm_flow else bytes(scan_sample)
            asyncio.create_task(
                cache_client.store(
                    raw_body_for_cache,
                    provider=req_cls.provider,
                    model=req_cls.model,
                    flow_kind=req_cls.flow_kind,
                    content_type=req.content_type,
                    response_sample=store_sample,
                    tokens_input=meta.get("tokens_input"),
                    tokens_output=meta.get("tokens_output"),
                    pii_anonymized=pii_anonymized,
                    host=host,
                    path=req.path if req else None,
                    method=req_cls.method,
                )
            )
    except Exception as exc:
        logger.debug("mitm relay failed host=%s", host, exc_info=True)
        client_reader.feed_eof()
        await sink.record(
            EgressRecord(
                host=host,
                capture_mode="mitm",
                status_code=status_code,
                latency_ms=int((time.monotonic() - started) * 1000),
                execution_trace_id=default_execution_trace_id(),
                attrs={
                    "error_type": "relay_failed",
                    "error_detail": str(exc)[:1000] or exc.__class__.__name__,
                },
            )
        )
    finally:
        if up_writer is not None:
            try:
                up_writer.close()
            except Exception:
                pass


def mitm_enabled() -> bool:
    return (os.environ.get("EGRESS_MITM_ENABLED") or "").strip().lower() in ("1", "true", "yes")


async def maybe_handle_connect(
    host: str,
    port: int,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
    *,
    ca: MitmCA | None,
    tunnel_handler,
) -> None:
    """Route CONNECT to MITM (known providers, port 443) or blind tunnel."""
    if mitm_enabled() and port == 443 and is_mitm_candidate_host(host) and ca is not None and ca.is_initialized():
        await handle_connect_mitm(host, port, client_reader, client_writer, sink, ca)
        return
    await tunnel_handler(host, port, client_reader, client_writer, sink)
