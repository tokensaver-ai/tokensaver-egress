"""ACP-4 MITM — streamed response relay + SSE/JSON classification (hermetic).

Run from apps/egress:  python -m pytest tests/test_mitm_streaming.py
"""

from __future__ import annotations

import asyncio

import pytest

from tokensaver_egress.mitm import _classify_response_payload, _read_response_head, _stream_response_body


class FakeReader:
    """Feeds a fixed byte buffer through readline / read / readexactly."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    async def readline(self) -> bytes:
        if self.pos >= len(self.data):
            return b""
        idx = self.data.find(b"\n", self.pos)
        if idx == -1:
            chunk = self.data[self.pos :]
            self.pos = len(self.data)
            return chunk
        chunk = self.data[self.pos : idx + 1]
        self.pos = idx + 1
        return chunk

    async def read(self, n: int) -> bytes:
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


class FakeWriter:
    def __init__(self, *, abort_after_drains: int | None = None):
        self.buf = bytearray()
        self._drain_count = 0
        self._abort_after_drains = abort_after_drains

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        self._drain_count += 1
        if self._abort_after_drains is not None and self._drain_count >= self._abort_after_drains:
            raise BrokenPipeError("client gone")

    def close(self) -> None:
        return None


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_read_response_head_parses_status_and_headers():
    raw = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
    head, headers = _run(_read_response_head(FakeReader(raw)))
    assert head.startswith(b"HTTP/1.1 200 OK")
    assert headers["Content-Type"] == "text/event-stream"
    assert headers["Transfer-Encoding"] == "chunked"


def test_stream_chunked_body_records_ttft_ms():
    import time

    from tokensaver_egress.mitm import _stream_response_body

    body = b"5\r\nhello\r\n0\r\n\r\n"
    reader = FakeReader(body)
    writer = FakeWriter()
    anchor = time.monotonic() - 0.05  # ~50ms ago
    meta, _head = _run(
        _stream_response_body(
            reader, writer, {"Transfer-Encoding": "chunked"}, ttft_anchor=anchor
        )
    )
    assert meta.get("ttft_ms") is not None
    assert float(meta["ttft_ms"]) >= 40.0


def test_stream_chunked_body_is_forwarded_verbatim_and_accumulated():
    # Two chunks then terminator.
    body = b"5\r\nhello\r\n5\r\nworld\r\n0\r\n\r\n"
    reader = FakeReader(body)
    writer = FakeWriter()
    _meta, head = _run(_stream_response_body(reader, writer, {"Transfer-Encoding": "chunked"}))
    # Client must receive the exact chunked framing.
    assert bytes(writer.buf) == body
    # Head sample is the concatenated chunk data.
    assert head == b"helloworld"


def test_stream_content_length_body():
    reader = FakeReader(b"abcdefghij")
    writer = FakeWriter()
    _meta, head = _run(_stream_response_body(reader, writer, {"Content-Length": "10"}))
    assert bytes(writer.buf) == b"abcdefghij"
    assert head == b"abcdefghij"


def test_stream_chunked_sse_scrapes_usage_live():
    # Anthropic-style SSE split across chunks: model+input in head, final output in tail.
    e1 = b'data: {"type":"message_start","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":25,"output_tokens":1}}}\n\n'
    e2 = b'data: {"type":"message_delta","usage":{"output_tokens":99}}\n\n'
    body = b"%x\r\n%s\r\n%x\r\n%s\r\n0\r\n\r\n" % (len(e1), e1, len(e2), e2)
    reader = FakeReader(body)
    writer = FakeWriter()
    meta, _head = _run(_stream_response_body(reader, writer, {"Transfer-Encoding": "chunked"}))
    assert meta["model"] == "claude-sonnet-4-6"
    assert meta["tokens_input"] == 25
    assert meta["tokens_output"] == 99


def test_stream_scrapes_anthropic_prompt_cache_as_total_input():
    """Claude Code caching: input_tokens is uncached tail only — must sum cache_* fields."""
    from tokensaver_egress.mitm import _scan_usage

    meta: dict = {}
    _scan_usage(
        meta,
        b'{"type":"message_start","message":{"model":"claude-sonnet-5",'
        b'"usage":{"input_tokens":2,"cache_creation_input_tokens":100,'
        b'"cache_read_input_tokens":45000,"output_tokens":1}}}',
    )
    assert meta["tokens_input"] == 45102
    assert meta["cache_read_input_tokens"] == 45000
    assert meta["cache_creation_input_tokens"] == 100
    _scan_usage(meta, b'{"type":"message_delta","usage":{"output_tokens":631}}')
    assert meta["tokens_output"] == 631
    assert meta["tokens_input"] == 45102  # not wiped by later delta


def test_strip_accept_encoding_removes_header_case_insensitive():
    from tokensaver_egress.mitm import _strip_accept_encoding

    headers = {"Host": "api.anthropic.com", "Accept-Encoding": "gzip, br", "X-Keep": "1"}
    _strip_accept_encoding(headers)
    assert "Accept-Encoding" not in headers
    assert not any(k.lower() == "accept-encoding" for k in headers)
    assert headers["X-Keep"] == "1"


def test_classify_plain_json_response():
    body = b'{"model":"gpt-4o-mini","usage":{"prompt_tokens":12,"completion_tokens":8}}'
    meta = _classify_response_payload("api.openai.com", body)
    assert meta["model"] == "gpt-4o-mini"
    assert meta["tokens_output"] == 8
    assert meta["tokens_input"] == 12


def test_classify_sse_stream_scrapes_model_and_usage():
    # Anthropic-style streamed events: usage split across message_start / message_delta.
    body = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"model":"claude-sonnet-4","usage":{"input_tokens":25,"output_tokens":1}}}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":42}}\n\n'
    )
    meta = _classify_response_payload("api.anthropic.com", body)
    assert meta["model"] == "claude-sonnet-4"
    assert meta["tokens_input"] == 25
    # max across the incremental output_tokens values
    assert meta["tokens_output"] == 42


def test_classify_openai_sse_stream_scrapes_model_and_usage():
    body = (
        b'data: {"object":"chat.completion.chunk","model":"gpt-4o-mini-2024-07-18","choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"object":"chat.completion.chunk","model":"gpt-4o-mini-2024-07-18","choices":[],'
        b'"usage":{"prompt_tokens":13,"completion_tokens":5,"total_tokens":18}}\n\n'
        b'data: [DONE]\n\n'
    )
    meta = _classify_response_payload("api.openai.com", body)
    assert meta["model"].startswith("gpt-4o-mini")
    assert meta["tokens_input"] == 13
    assert meta["tokens_output"] == 5


def test_classify_empty_body():
    assert _classify_response_payload("api.openai.com", b"") == {}


def test_stream_openai_to_anthropic_translates_chunked_sse():
    from tokensaver_egress.mitm import _stream_openai_to_anthropic

    e1 = (
        b'data: {"object":"chat.completion.chunk","model":"moonshotai/kimi-k3",'
        b'"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
    )
    e2 = (
        b'data: {"object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
    )
    e3 = b"data: [DONE]\n\n"
    body = b"%x\r\n%s\r\n%x\r\n%s\r\n%x\r\n%s\r\n0\r\n\r\n" % (
        len(e1),
        e1,
        len(e2),
        e2,
        len(e3),
        e3,
    )
    reader = FakeReader(body)
    writer = FakeWriter()
    meta, _head, _n = _run(
        _stream_openai_to_anthropic(
            reader,
            writer,
            {"Transfer-Encoding": "chunked"},
            client_model="claude-sonnet-4-6",
        )
    )
    text = bytes(writer.buf).decode("utf-8", errors="replace")
    assert "event: message_start" in text
    assert "text_delta" in text
    assert "Hi" in text
    assert "event: message_stop" in text
    assert meta.get("tokens_input") == 5
    assert meta.get("tokens_output") == 2


def test_stream_openai_to_anthropic_client_abort_does_not_raise():
    """Client disconnect mid-stream must not trigger async-generator aclose races."""
    from tokensaver_egress.mitm import _stream_openai_to_anthropic

    e1 = (
        b'data: {"object":"chat.completion.chunk","model":"x",'
        b'"choices":[{"delta":{"content":"part1"},"finish_reason":null}]}\n\n'
    )
    e2 = (
        b'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"part2"},'
        b'"finish_reason":null}]}\n\n'
    )
    e3 = (
        b'data: {"object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
    )
    body = b"%x\r\n%s\r\n%x\r\n%s\r\n%x\r\n%s\r\n0\r\n\r\n" % (
        len(e1),
        e1,
        len(e2),
        e2,
        len(e3),
        e3,
    )
    reader = FakeReader(body)
    writer = FakeWriter(abort_after_drains=3)
    meta, _head, _n = _run(
        _stream_openai_to_anthropic(
            reader,
            writer,
            {"Transfer-Encoding": "chunked"},
            client_model="claude-sonnet-4-6",
        )
    )
    text = bytes(writer.buf).decode("utf-8", errors="replace")
    assert "event: message_start" in text
    assert "part1" in text
