"""Claude Code multi-source capture (agentic-graph-spec P0).

Local HTTP side-channel (default :8787) that accepts:
  - POST /hooks          — Claude Code HTTP hooks (fail-open)
  - POST /v1/logs|metrics|traces — OTLP protobuf/json byte relay

Every event is wrapped in the common envelope and shipped to the control plane
via TOKENSAVER_INGEST_URL (agentic events endpoint). The MITM proxy stays
byte-relay only; this module adds observation, not policy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

logger = logging.getLogger("tokensaver-egress.claude_capture")

# Avoid 8787/8788 — reserved locally for MCP HTTP / gateway (reboot-platform.sh).
DEFAULT_CAPTURE_PORT = 8789


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def capture_enabled() -> bool:
    return _env_truthy("EGRESS_CLAUDE_CAPTURE", default=True)


def capture_port() -> int:
    raw = (os.environ.get("EGRESS_CAPTURE_PORT") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_CAPTURE_PORT


@dataclass
class SessionSeqStore:
    """Per-session monotonic sequence for gap detection on the platform."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _seqs: dict[str, int] = field(default_factory=dict)

    def next_seq(self, session_id: str) -> int:
        key = (session_id or "").strip() or "_unknown"
        with self._lock:
            n = self._seqs.get(key, 0) + 1
            self._seqs[key] = n
            return n


_SEQ = SessionSeqStore()


def build_envelope(
    *,
    source: str,
    session_id: str | None,
    payload: Any,
    claude_code_version: str | None = None,
    prompt_id: str | None = None,
    tool_use_id: str | None = None,
    agent_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sid = (session_id or "").strip() or None
    env: dict[str, Any] = {
        "source": source,
        "session_id": sid,
        "seq": _SEQ.next_seq(sid or "_unknown"),
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "claude_code_version": (claude_code_version or os.environ.get("CLAUDE_CODE_VERSION") or "").strip()
        or None,
        "payload": payload,
    }
    if prompt_id:
        env["prompt_id"] = prompt_id
    if tool_use_id:
        env["tool_use_id"] = tool_use_id
    if agent_id:
        env["agent_id"] = agent_id
    if extra:
        env.update(extra)
    return env


def _extract_hook_ids(body: dict[str, Any]) -> dict[str, str | None]:
    session_id = (
        body.get("session_id")
        or body.get("sessionId")
        or (body.get("session") or {}).get("id")
        if isinstance(body.get("session"), dict)
        else None
    )
    if isinstance(session_id, dict):
        session_id = session_id.get("id")
    return {
        "session_id": str(session_id) if session_id else None,
        "prompt_id": str(body.get("prompt_id") or body.get("promptId") or "") or None,
        "tool_use_id": str(
            body.get("tool_use_id") or body.get("toolUseId") or body.get("tool_call_id") or ""
        )
        or None,
        "agent_id": str(body.get("agent_id") or body.get("agentId") or "") or None,
        "claude_code_version": str(body.get("claude_code_version") or body.get("version") or "")
        or None,
    }


class AgenticEventShipper:
    """Best-effort batch shipper for agentic envelopes (never blocks Claude)."""

    def __init__(self) -> None:
        base = (os.environ.get("TOKENSAVER_INGEST_URL") or "").rstrip("/")
        # Prefer dedicated agentic endpoint; fall back to /egress/agentic-events
        if base.endswith("/egress/ingest"):
            base = base[: -len("/egress/ingest")]
        self.url = (os.environ.get("TOKENSAVER_AGENTIC_INGEST_URL") or "").strip() or (
            f"{base}/egress/agentic-events" if base else ""
        )
        self.api_key = (os.environ.get("TOKENSAVER_API_KEY") or "").strip()
        self._buf: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()

    def enqueue(self, envelope: dict[str, Any]) -> None:
        if not self.url or not self.api_key:
            logger.debug("agentic ingest disabled — drop event source=%s", envelope.get("source"))
            return
        with self._lock:
            self._buf.append(envelope)
            should = len(self._buf) >= 20 or (time.monotonic() - self._last_flush) > 2.0
        if should:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            batch = self._buf
            self._buf = []
            self._last_flush = time.monotonic()
        if not batch:
            return
        try:
            data = json.dumps({"events": batch}).encode("utf-8")
            req = urlrequest.Request(
                self.url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "X-API-Key": self.api_key,
                },
            )
            with urlrequest.urlopen(req, timeout=5.0) as resp:
                resp.read()
        except (urlerror.URLError, TimeoutError, OSError) as exc:
            logger.warning("agentic ingest failed (%d events): %s", len(batch), exc)


_SHIPPER = AgenticEventShipper()


def _json_response(status: int, body: dict[str, Any]) -> tuple[int, bytes, str]:
    return status, json.dumps(body).encode("utf-8"), "application/json"


def handle_capture_request(
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
) -> tuple[int, bytes, str]:
    """Synchronous request handler for the capture side-channel (fail-open)."""
    method = method.upper()
    path = path.split("?", 1)[0]

    if method == "GET" and path in ("/health", "/"):
        return _json_response(200, {"ok": True, "service": "tokensaver-claude-capture"})

    if method == "POST" and path == "/hooks":
        try:
            parsed = json.loads(body.decode("utf-8") or "{}")
            if not isinstance(parsed, dict):
                parsed = {"raw": parsed}
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {"raw_b64": body.hex()}
        ids = _extract_hook_ids(parsed)
        hook_event = str(
            parsed.get("hook_event_name")
            or parsed.get("hookEventName")
            or parsed.get("event")
            or headers.get("x-claude-hook-event")
            or "unknown"
        )
        env = build_envelope(
            source="hook",
            session_id=ids["session_id"],
            payload={"hook_event": hook_event, **parsed},
            claude_code_version=ids["claude_code_version"],
            prompt_id=ids["prompt_id"],
            tool_use_id=ids["tool_use_id"],
            agent_id=ids["agent_id"],
        )
        _SHIPPER.enqueue(env)
        # Fail-open: never block Claude Code tool use.
        return _json_response(200, {"continue": True, "suppressOutput": True})

    if method == "POST" and path in ("/v1/logs", "/v1/metrics", "/v1/traces"):
        ctype = (headers.get("content-type") or "").lower()
        signal = path.rsplit("/", 1)[-1]
        session_hdr = headers.get("x-session-id") or headers.get("session-id")
        payload: dict[str, Any] = {
            "otlp_signal": signal,
            "content_type": ctype,
            "byte_length": len(body),
            # Raw relay — platform parses. Cap stored preview for safety.
            "body_hex_prefix": body[:64].hex() if body else "",
            "body_b64": None,
        }
        # Ship full body when small enough; otherwise prefix only + length.
        max_inline = int(os.environ.get("EGRESS_OTLP_INLINE_MAX", "262144") or "262144")
        import base64

        if len(body) <= max_inline:
            payload["body_b64"] = base64.b64encode(body).decode("ascii")
        else:
            payload["truncated"] = True
            payload["body_b64_prefix"] = base64.b64encode(body[:max_inline]).decode("ascii")

        env = build_envelope(
            source="otel",
            session_id=session_hdr,
            payload=payload,
            extra={"otlp_path": path},
        )
        _SHIPPER.enqueue(env)
        return 200, b"", "application/json"

    return _json_response(404, {"error": "not_found"})


async def _read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], bytes] | None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=30.0)
    except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
        return None
    if not request_line:
        return None
    try:
        line = request_line.decode("latin-1").strip()
        method, path, _proto = line.split(" ", 2)
    except ValueError:
        return None
    headers: dict[str, str] = {}
    while True:
        raw = await reader.readline()
        if raw in (b"\r\n", b"\n", b""):
            break
        try:
            hk, hv = raw.decode("latin-1").split(":", 1)
            headers[hk.strip().lower()] = hv.strip()
        except ValueError:
            continue
    length = int(headers.get("content-length") or "0")
    body = b""
    if length > 0:
        body = await reader.readexactly(min(length, 8 * 1024 * 1024))
    return method, path, headers, body


async def _handle_capture_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        parsed = await _read_http_request(reader)
        if not parsed:
            return
        method, path, headers, body = parsed
        status, resp_body, ctype = await asyncio.to_thread(
            handle_capture_request, method, path, headers, body
        )
        reason = {200: "OK", 404: "Not Found"}.get(status, "OK")
        header = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(resp_body)}\r\n"
            "Connection: close\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "\r\n"
        ).encode("latin-1")
        writer.write(header + resp_body)
        await writer.drain()
    except Exception:
        logger.debug("capture client error", exc_info=True)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def serve_claude_capture(
    host: str = "127.0.0.1",
    port: int | None = None,
) -> asyncio.AbstractServer | None:
    """Start the Claude capture side-channel. Returns None if disabled."""
    if not capture_enabled():
        logger.info("Claude capture side-channel disabled (EGRESS_CLAUDE_CAPTURE=0)")
        return None
    port = port if port is not None else capture_port()

    async def _flush_loop() -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                await asyncio.to_thread(_SHIPPER.flush)
            except Exception:
                logger.debug("agentic flush error", exc_info=True)

    server = await asyncio.start_server(_handle_capture_client, host, port)
    asyncio.create_task(_flush_loop(), name="claude-capture-flush")
    addr = ", ".join(str(s.getsockname()) for s in server.sockets)
    logger.info(
        "Claude capture listening on %s  (POST /hooks, POST /v1/logs|metrics|traces)",
        addr,
    )
    return server


def client_environ_for_claude_capture(port: int | None = None) -> dict[str, str]:
    """Env vars for `tokensaver-egress claude` to point OTEL + hooks at the side-channel."""
    if not capture_enabled():
        return {}
    p = port if port is not None else capture_port()
    base = f"http://127.0.0.1:{p}"
    return {
        "OTEL_EXPORTER_OTLP_ENDPOINT": base,
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        # Hook URL — exact Claude Code settings schema still [à vérifier];
        # documented for manual ~/.claude/settings.json wiring.
        "TOKENSAVER_CLAUDE_HOOKS_URL": f"{base}/hooks",
    }
