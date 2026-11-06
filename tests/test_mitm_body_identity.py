"""Multi-MB MITM request body must reach the provider byte-for-byte.

Repro for MYPRJ-11 T1: Anthropic EOF at column 2097152. Our relay must not
truncate; a 3 MiB JSON body with embedded base64 image must arrive identical.
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

from tokensaver_egress.mitm import _relay_one_exchange


class FakeReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    async def readline(self) -> bytes:
        if self.pos >= len(self.data):
            return b""
        idx = self.data.find(b"\n", self.pos)
        if idx < 0:
            chunk = self.data[self.pos :]
            self.pos = len(self.data)
            return chunk
        chunk = self.data[self.pos : idx + 1]
        self.pos = idx + 1
        return chunk

    async def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = len(self.data) - self.pos
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        return chunk

    async def readexactly(self, n: int) -> bytes:
        chunk = self.data[self.pos : self.pos + n]
        if len(chunk) < n:
            self.pos = len(self.data)
            raise asyncio.IncompleteReadError(chunk, n)
        self.pos += n
        return chunk

    def feed_eof(self) -> None:
        return None


class CaptureWriter:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def get_extra_info(self, name: str, default=None):
        return default

    def is_closing(self) -> bool:
        return self.closed


class Sink:
    def __init__(self) -> None:
        self.records: list = []

    async def record(self, rec) -> None:
        self.records.append(rec)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _large_anthropic_body(*, target: int = 3 * 1024 * 1024) -> bytes:
    b64 = "X" * max(1024, target - 220)
    return json.dumps(
        {
            "model": "claude-opus-4",
            "max_tokens": 16,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "ping"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                    ],
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def test_mitm_forwards_multi_mb_body_byte_identical(monkeypatch):
    os.environ["EGRESS_AUDIT_ONLY"] = "1"
    os.environ["EGRESS_CAPTURE_BODIES"] = "0"
    payload = _large_anthropic_body()
    assert len(payload) > 2 * 1024 * 1024

    up_written = bytearray()

    class UpWriter(CaptureWriter):
        def write(self, data: bytes) -> None:
            up_written.extend(data)

    async def fake_open(host, port, ca):
        resp = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: 11\r\n\r\n{\"ok\":true}"
        )
        return FakeReader(resp), UpWriter()

    import tokensaver_egress.mitm as mitm

    monkeypatch.setattr(mitm, "_open_upstream_connection", fake_open)

    client_raw = (
        b"POST /v1/messages HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        b"Content-Type: application/json\r\n"
        b"anthropic-version: 2023-06-01\r\n"
        b"x-api-key: test-key\r\n"
        + f"Content-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    sink = Sink()
    _run(
        _relay_one_exchange(
            "api.anthropic.com",
            443,
            FakeReader(client_raw),
            CaptureWriter(),
            sink,
            SimpleNamespace(),
        )
    )
    raw = bytes(up_written)
    idx = raw.find(b"\r\n\r\n")
    assert idx >= 0
    up_body = raw[idx + 4 :]
    assert up_body == payload
    assert sink.records
    attrs = sink.records[0].attrs or {}
    assert attrs.get("proxy_body_altered") is False
    assert attrs.get("client_json_valid") is True
    assert attrs.get("request_body_bytes_out") == len(payload)


def test_mitm_blocks_truncated_2mib_json(monkeypatch):
    os.environ["EGRESS_AUDIT_ONLY"] = "1"
    os.environ["EGRESS_CAPTURE_BODIES"] = "0"
    prefix = b'{"model":"x","messages":[{"role":"user","content":"'
    body = prefix + (b"Y" * (2 * 1024 * 1024 - len(prefix)))
    assert len(body) == 2 * 1024 * 1024

    opened = {"n": 0}

    async def fake_open(host, port, ca):
        opened["n"] += 1
        raise AssertionError("must not contact upstream for truncated JSON")

    import tokensaver_egress.mitm as mitm

    monkeypatch.setattr(mitm, "_open_upstream_connection", fake_open)

    client_raw = (
        b"POST /v1/messages HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    client_writer = CaptureWriter()
    sink = Sink()
    _run(
        _relay_one_exchange(
            "api.anthropic.com",
            443,
            FakeReader(client_raw),
            client_writer,
            sink,
            SimpleNamespace(),
        )
    )
    assert opened["n"] == 0
    assert sink.records
    rec = sink.records[0]
    assert rec.status_code == 400
    assert rec.attrs.get("client_body_truncation_suspect") is True
    assert b"client_body_truncated" in bytes(client_writer.buf)
