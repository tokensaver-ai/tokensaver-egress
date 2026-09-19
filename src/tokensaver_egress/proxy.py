"""Forward proxy with blind-tunnel egress capture (ACP-4 scaffold).

Supports HTTP forwarding and HTTPS via ``CONNECT`` tunnelling. In tunnel mode we
capture metadata only (host, bytes, latency, status) — the TLS payload stays
end-to-end encrypted and is never inspected. Plain HTTP is forwarded with
host-level classification (provider / flow_kind in attrs). MITM TLS is the
next increment; see README.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from tokensaver_egress.audit import AuditSink, EgressRecord
from tokensaver_egress.bodies import bodies_enabled, headers_from_lines, prepare_body
from tokensaver_egress.classify import classify_egress, classify_host
from tokensaver_egress.mitm import _attach_loop_meta, default_execution_trace_id, maybe_handle_connect
from tokensaver_egress.ca import MitmCA
from tokensaver_egress.otel import egress_span
from tokensaver_egress.pipeline import warm_llm_policies
from tokensaver_egress.transparent import host_label_from_prefix, original_dst

logger = logging.getLogger("tokensaver-egress.proxy")

_BUF = 64 * 1024


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, counter: list[int]) -> None:
    try:
        while True:
            data = await reader.read(_BUF)
            if not data:
                break
            counter[0] += len(data)
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle_connect(
    host: str,
    port: int,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
) -> None:
    started = time.monotonic()
    up_bytes = [0]
    down_bytes = [0]
    status_code = 200
    try:
        remote_reader, remote_writer = await asyncio.open_connection(host, port)
    except Exception:
        client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await client_writer.drain()
        client_writer.close()
        await sink.record(EgressRecord(host=host, capture_mode="tunnel", status_code=502))
        return

    client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_writer.drain()

    await asyncio.gather(
        _pipe(client_reader, remote_writer, up_bytes),
        _pipe(remote_reader, client_writer, down_bytes),
    )

    latency_ms = int((time.monotonic() - started) * 1000)
    meta: dict = {}
    _attach_loop_meta(meta, None)
    await sink.record(
        EgressRecord(
            host=host,
            capture_mode="tunnel",
            request_bytes=up_bytes[0],
            response_bytes=down_bytes[0],
            latency_ms=latency_ms,
            status_code=status_code,
            attrs=meta,
        )
    )


async def _handle_http_forward(
    *,
    method: str,
    target: str,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
    first_line: bytes,
) -> None:
    """Forward plain HTTP requests and capture host-level metadata."""
    started = time.monotonic()
    headers: list[bytes] = [first_line]
    host: str | None = None
    port = 80
    content_length = 0

    while True:
        line = await client_reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        headers.append(line)
        lower = line.lower()
        if lower.startswith(b"host:"):
            host_part = line.split(b":", 1)[1].strip().decode("latin-1", errors="ignore")
            if ":" in host_part:
                host, _, port_s = host_part.partition(":")
                port = int(port_s or "80")
            else:
                host = host_part
        elif lower.startswith(b"content-length:"):
            try:
                content_length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                content_length = 0

    body = b""
    if content_length > 0:
        body = await client_reader.readexactly(content_length)

    path = target
    if target.startswith("http://"):
        from urllib.parse import urlparse

        parsed = urlparse(target)
        host = host or parsed.hostname
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

    status_code = 502
    request_bytes = sum(len(h) for h in headers) + len(body)
    response_bytes = 0
    resp_headers: list[bytes] = []
    resp_body = b""
    try:
        if not host:
            client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await client_writer.drain()
            status_code = 400
            return

        # Re-emit the request in origin-form (normal servers reject absolute-form),
        # strip proxy/hop headers, and force Connection: close so we can read the
        # body until EOF without hanging on keep-alive.
        out: list[bytes] = [f"{method} {path} HTTP/1.1\r\n".encode("latin-1")]
        for h in headers[1:]:
            low = h.lower()
            if low.startswith(b"proxy-connection:") or low.startswith(b"connection:"):
                continue
            out.append(h)
        out.append(b"Connection: close\r\n")
        out.append(b"\r\n")

        remote_reader, remote_writer = await asyncio.open_connection(host, port)
        remote_writer.write(b"".join(out))
        if body:
            remote_writer.write(body)
        await remote_writer.drain()

        response_line = await remote_reader.readline()
        if not response_line:
            status_code = 502
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            return
        resp_headers = [response_line]
        while True:
            line = await remote_reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            resp_headers.append(line)
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    content_length = 0

        resp_body = b""
        if content_length > 0:
            resp_body = await remote_reader.readexactly(content_length)
        else:
            # Chunked or connection-close bodies: stream until remote closes.
            while True:
                chunk = await remote_reader.read(_BUF)
                if not chunk:
                    break
                resp_body += chunk

        # readline keeps each header's CRLF but drops the blank separator line;
        # re-emit it so the client sees a well-formed header/body boundary.
        client_writer.write(b"".join(resp_headers) + b"\r\n")
        if resp_body:
            client_writer.write(resp_body)
        await client_writer.drain()
        response_bytes = sum(len(h) for h in resp_headers) + len(resp_body)
        try:
            status_code = int(response_line.split()[1])
        except (IndexError, ValueError):
            status_code = 200
    except Exception:
        client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await client_writer.drain()
        status_code = 502
    finally:
        try:
            client_writer.close()
        except Exception:
            pass

    cls = classify_host(host)
    req_cls = classify_egress(host=host, body=None)
    if body:
        try:
            import json

            parsed = json.loads(body.decode("utf-8", errors="ignore"))
            if isinstance(parsed, dict):
                req_cls = classify_egress(host=host, content_type="application/json", body=parsed)
        except Exception:
            pass
    resp_cls = classify_egress(host=host, body=None)
    if resp_body:
        try:
            import json

            parsed = json.loads(resp_body.decode("utf-8", errors="ignore"))
            if isinstance(parsed, dict):
                resp_cls = classify_egress(host=host, content_type="application/json", body=parsed)
        except Exception:
            pass

    latency_ms = int((time.monotonic() - started) * 1000)
    meta = {
        "flow_kind": resp_cls.flow_kind if resp_cls.flow_kind != "http" else req_cls.flow_kind,
        "provider": resp_cls.provider or req_cls.provider,
        "model": resp_cls.model or req_cls.model,
        "method": req_cls.method or method,
        "args_hash": req_cls.args_hash,
        "tokens_input": req_cls.tokens_input,
        "tokens_output": resp_cls.tokens_output,
        "path": path[:256],
    }
    if bodies_enabled():
        meta["request_body"] = prepare_body(body)
        meta["response_body"] = prepare_body(resp_body)
        meta["request_headers"] = headers_from_lines(headers[1:])
        meta["response_headers"] = headers_from_lines(resp_headers[1:])
    _attach_loop_meta(meta, None)
    trace_id = default_execution_trace_id()
    with egress_span("tokensaver_egress.http_forward", host=host, capture_mode="http_forward", execution_trace_id=trace_id, attrs=meta):
        await sink.record(
            EgressRecord(
                host=host,
                capture_mode="http_forward",
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                latency_ms=latency_ms,
                status_code=status_code,
                execution_trace_id=trace_id,
                attrs=meta,
            )
        )


async def _handle_transparent(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
) -> None:
    """Transparent (TPROXY/REDIRECT) connection: recover original dst + SNI, then tunnel."""
    started = time.monotonic()
    up_bytes = [0]
    down_bytes = [0]

    sock = client_writer.get_extra_info("socket")
    dst = original_dst(sock)
    if dst is None:
        peer = client_writer.get_extra_info("peername")
        logger.warning("transparent: original destination unavailable for %s", peer)
        client_writer.close()
        return
    dst_ip, dst_port = dst

    try:
        prefix = await client_reader.read(_BUF)
    except (ConnectionResetError, asyncio.IncompleteReadError):
        client_writer.close()
        return
    if not prefix:
        client_writer.close()
        return

    host = host_label_from_prefix(prefix, dst_ip)
    up_bytes[0] += len(prefix)

    try:
        remote_reader, remote_writer = await asyncio.open_connection(dst_ip, dst_port)
    except Exception:
        client_writer.close()
        await sink.record(
            EgressRecord(host=host, capture_mode="transparent", status_code=502)
        )
        return

    remote_writer.write(prefix)
    await remote_writer.drain()

    await asyncio.gather(
        _pipe(client_reader, remote_writer, up_bytes),
        _pipe(remote_reader, client_writer, down_bytes),
    )

    cls = classify_host(host)
    trace_id = default_execution_trace_id()
    meta = {
        "flow_kind": cls.flow_kind,
        "provider": cls.provider,
        "dst_ip": dst_ip,
        "dst_port": dst_port,
        "sni": host,
    }
    with egress_span(
        "tokensaver_egress.transparent", host=host, capture_mode="transparent", execution_trace_id=trace_id, attrs=meta
    ):
        await sink.record(
            EgressRecord(
                host=host,
                capture_mode="transparent",
                request_bytes=up_bytes[0],
                response_bytes=down_bytes[0],
                latency_ms=int((time.monotonic() - started) * 1000),
                status_code=200,
                execution_trace_id=trace_id,
                attrs=meta,
            )
        )


async def _handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
    ca: MitmCA | None,
) -> None:
    try:
        request_line = await client_reader.readline()
        if not request_line:
            client_writer.close()
            return
        parts = request_line.decode("latin-1", errors="ignore").split()
        if len(parts) < 2:
            client_writer.close()
            return
        method, target = parts[0], parts[1]

        if method.upper() == "CONNECT":
            host, _, port_s = target.partition(":")
            port = int(port_s or "443")
            # Drain remaining request headers.
            while True:
                line = await client_reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            await maybe_handle_connect(
                host,
                port,
                client_reader,
                client_writer,
                sink,
                ca=ca,
                tunnel_handler=_handle_connect,
            )
            return

        await _handle_http_forward(
            method=method,
            target=target,
            client_reader=client_reader,
            client_writer=client_writer,
            sink=sink,
            first_line=request_line,
        )
    except Exception:
        logger.debug("client handler error", exc_info=True)
        try:
            client_writer.close()
        except Exception:
            pass


def _log_client_task(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug("egress client session ended with error", exc_info=exc)


async def _spawn_client_session(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    sink: AuditSink,
    ca: MitmCA | None,
) -> None:
    """Detach each client session so new connections are never queued behind a slow MITM flow."""
    task = asyncio.create_task(
        _handle_client(client_reader, client_writer, sink, ca),
        name="egress-client-session",
    )
    task.add_done_callback(_log_client_task)


async def serve(
    host: str = "0.0.0.0",
    port: int = 8888,
    sink: AuditSink | None = None,
    ca: MitmCA | None = None,
    transparent: bool = False,
) -> None:
    sink = sink or AuditSink()
    if transparent:
        handler = lambda r, w: _handle_transparent(r, w, sink)  # noqa: E731
    else:
        handler = lambda r, w: _spawn_client_session(r, w, sink, ca)  # noqa: E731
    server = await asyncio.start_server(handler, host, port)
    asyncio.create_task(sink.run_periodic_flush())
    asyncio.create_task(warm_llm_policies(), name="egress-warm-policies-startup")
    addr = ", ".join(str(s.getsockname()) for s in server.sockets)
    mode = "transparent (TPROXY/REDIRECT)" if transparent else "explicit-proxy"
    logger.info("tokensaver-egress listening on %s [%s]", addr, mode)
    loop_id = (os.environ.get("TOKENSAVER_LOOP_ID") or "").strip()
    if loop_id:
        precheck = (os.environ.get("TOKENSAVER_LOOP_PRECHECK") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        logger.info(
            "BusinessLoop binding: TOKENSAVER_LOOP_ID=%s precheck=%s",
            loop_id,
            "on" if precheck else "off",
        )
    else:
        logger.info(
            "BusinessLoop binding: none at startup — Claude MCP "
            "tokensaver_loop_start writes ~/.tokensaver-egress/current-loop.env "
            "(hot-reloaded per request); or use serve --loop / TOKENSAVER_LOOP_ID"
        )
    async with server:
        await server.serve_forever()
