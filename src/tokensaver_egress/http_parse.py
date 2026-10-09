"""Minimal HTTP/1.1 parse helpers for MITM and plain HTTP capture."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_BUF = 64 * 1024


@dataclass
class HttpMessage:
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    headers: dict[str, str] | None = None
    body: bytes = b""

    @property
    def content_type(self) -> str | None:
        if not self.headers:
            return None
        for k, v in self.headers.items():
            if k.lower() == "content-type":
                return v.split(";")[0].strip().lower()
        return None

    def json_body(self) -> dict[str, Any] | None:
        if not self.body:
            return None
        ct = self.content_type or ""
        if "json" not in ct and not self.body.lstrip().startswith(b"{"):
            return None
        try:
            parsed = json.loads(self.body.decode("utf-8", errors="ignore"))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


async def read_http_message(reader) -> HttpMessage | None:
    """Read one HTTP request or response from a stream."""
    request_line = await reader.readline()
    if not request_line:
        return None
    line = request_line.decode("latin-1", errors="ignore").strip()
    parts = line.split()
    if len(parts) < 2:
        return None

    msg = HttpMessage(headers={})
    if parts[0].upper().startswith("HTTP/"):
        try:
            msg.status_code = int(parts[1])
        except ValueError:
            msg.status_code = None
    else:
        msg.method = parts[0]
        msg.path = parts[1]

    content_length = 0
    is_chunked = False
    while True:
        raw = await reader.readline()
        if raw in (b"\r\n", b"\n", b""):
            break
        header_line = raw.decode("latin-1", errors="ignore").strip()
        if ":" not in header_line:
            continue
        name, value = header_line.split(":", 1)
        msg.headers[name.strip()] = value.strip()
        lower = name.strip().lower()
        if lower == "content-length":
            try:
                content_length = int(value.strip())
            except ValueError:
                content_length = 0
        elif lower == "transfer-encoding" and "chunked" in value.lower():
            is_chunked = True

    if is_chunked:
        msg.body = await _read_chunked_body(reader)
    elif content_length > 0:
        msg.body = await reader.readexactly(content_length)
    return msg


async def _read_chunked_body(reader) -> bytes:
    chunks: list[bytes] = []
    while True:
        size_line = await reader.readline()
        if not size_line:
            break
        size_hex = size_line.decode("latin-1", errors="ignore").strip().split(";", 1)[0]
        try:
            size = int(size_hex, 16)
        except ValueError:
            break
        if size == 0:
            await reader.readline()
            break
        chunks.append(await reader.readexactly(size))
        await reader.readline()
    return b"".join(chunks)


def write_http_message(writer, msg: HttpMessage, *, is_response: bool) -> None:
    if is_response:
        status = msg.status_code or 200
        writer.write(f"HTTP/1.1 {status} OK\r\n".encode("latin-1"))
    else:
        method = msg.method or "GET"
        path = msg.path or "/"
        writer.write(f"{method} {path} HTTP/1.1\r\n".encode("latin-1"))
    for k, v in (msg.headers or {}).items():
        writer.write(f"{k}: {v}\r\n".encode("latin-1"))
    writer.write(b"\r\n")
    if msg.body:
        writer.write(msg.body)
