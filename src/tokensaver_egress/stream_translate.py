"""Live OpenAI SSE → Anthropic Messages SSE translation (egress MITM hot path).

Used when ``model_routing`` reroutes Claude Code (Anthropic client) to an
OpenAI-compatible upstream (OpenRouter, etc.). Buffering the full OpenAI stream
and synthesizing a late Anthropic body makes Claude Code report
"Streaming response ended before any complete data" and retry without streaming.

This translator emits Anthropic events incrementally as OpenAI chunks arrive.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _msg_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _finish_to_stop(finish_reason: str | None) -> str:
    fr = (finish_reason or "stop").strip().lower()
    if fr == "tool_calls":
        return "tool_use"
    if fr == "length":
        return "max_tokens"
    if fr == "stop_sequence":
        return "stop_sequence"
    return "end_turn"


@dataclass
class _ToolBlock:
    openai_index: int
    anthropic_index: int
    call_id: str
    name: str
    started: bool = False
    closed: bool = False


@dataclass
class OpenAIToAnthropicSSETranslator:
    """Stateful converter: feed OpenAI SSE bytes, emit Anthropic SSE bytes."""

    client_model: str | None = None
    msg_id: str = field(default_factory=_msg_id)
    started: bool = False
    text_started: bool = False
    text_closed: bool = False
    text_index: int = 0
    next_block_index: int = 0
    tools: dict[int, _ToolBlock] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "end_turn"
    finished: bool = False
    model: str = "claude-sonnet-4-6"
    _buf: bytearray = field(default_factory=bytearray)

    def __post_init__(self) -> None:
        if self.client_model:
            self.model = self.client_model

    def feed(self, data: bytes) -> list[bytes]:
        if not data or self.finished:
            return []
        self._buf.extend(data)
        out: list[bytes] = []
        while True:
            sep = self._buf.find(b"\n\n")
            if sep < 0:
                break
            block = bytes(self._buf[:sep])
            del self._buf[: sep + 2]
            out.extend(self._handle_sse_block(block))
        return out

    def finish(self) -> list[bytes]:
        out: list[bytes] = []
        if self._buf.strip():
            out.extend(self._handle_sse_block(bytes(self._buf)))
            self._buf.clear()
        if not self.finished:
            out.extend(self._close_stream())
        return out

    def _ensure_message_start(self) -> list[bytes]:
        if self.started:
            return []
        self.started = True
        return [
            _sse(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": self.msg_id,
                        "type": "message",
                        "role": "assistant",
                        "model": self.model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": int(self.input_tokens or 0), "output_tokens": 0},
                    },
                },
            )
        ]

    def _ensure_text_start(self) -> list[bytes]:
        out = self._ensure_message_start()
        if self.text_started:
            return out
        self.text_started = True
        self.text_index = self.next_block_index
        self.next_block_index += 1
        out.append(
            _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": self.text_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
        )
        return out

    def _close_text_if_open(self) -> list[bytes]:
        if not self.text_started or self.text_closed:
            return []
        self.text_closed = True
        return [_sse("content_block_stop", {"type": "content_block_stop", "index": self.text_index})]

    def _handle_sse_block(self, block: bytes) -> list[bytes]:
        data_lines: list[str] = []
        for raw in block.split(b"\n"):
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            return []
        payload = "\n".join(data_lines).strip()
        if not payload or payload == "[DONE]":
            return self._close_stream() if not self.finished else []
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if not isinstance(evt, dict):
            return []
        return self._handle_openai_chunk(evt)

    def _handle_openai_chunk(self, evt: dict[str, Any]) -> list[bytes]:
        out: list[bytes] = []
        model = evt.get("model")
        if isinstance(model, str) and model.strip() and not self.client_model:
            self.model = model.strip()[:256]

        usage = evt.get("usage")
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
            if pt is not None:
                try:
                    self.input_tokens = max(self.input_tokens, int(pt))
                except (TypeError, ValueError):
                    pass
            if ct is not None:
                try:
                    self.output_tokens = max(self.output_tokens, int(ct))
                except (TypeError, ValueError):
                    pass

        choices = evt.get("choices")
        if not isinstance(choices, list) or not choices:
            # usage-only chunk (some providers)
            return out

        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        finish = choice.get("finish_reason")
        if isinstance(finish, str) and finish:
            self.stop_reason = _finish_to_stop(finish)

        content = delta.get("content")
        if isinstance(content, str) and content:
            out.extend(self._ensure_text_start())
            out.append(
                _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self.text_index,
                        "delta": {"type": "text_delta", "text": content},
                    },
                )
            )

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    out.extend(self._handle_tool_delta(tc))

        if finish:
            out.extend(self._close_stream())
        return out

    def _handle_tool_delta(self, tc: dict[str, Any]) -> list[bytes]:
        out: list[bytes] = []
        try:
            oi = int(tc.get("index") if tc.get("index") is not None else 0)
        except (TypeError, ValueError):
            oi = 0
        block = self.tools.get(oi)
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        name = (fn.get("name") or "") if isinstance(fn, dict) else ""
        call_id = (tc.get("id") or "").strip()
        args_piece = fn.get("arguments") if isinstance(fn, dict) else None

        if block is None:
            out.extend(self._close_text_if_open())
            out.extend(self._ensure_message_start())
            anthropic_index = self.next_block_index
            self.next_block_index += 1
            block = _ToolBlock(
                openai_index=oi,
                anthropic_index=anthropic_index,
                call_id=call_id or f"toolu_{uuid.uuid4().hex[:24]}",
                name=name or "tool",
            )
            self.tools[oi] = block

        if call_id:
            block.call_id = call_id
        if name:
            block.name = name

        if not block.started:
            block.started = True
            out.append(
                _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block.anthropic_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": block.call_id,
                            "name": block.name,
                            "input": {},
                        },
                    },
                )
            )

        if isinstance(args_piece, str) and args_piece:
            out.append(
                _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block.anthropic_index,
                        "delta": {"type": "input_json_delta", "partial_json": args_piece},
                    },
                )
            )
        return out

    def _close_stream(self) -> list[bytes]:
        if self.finished:
            return []
        self.finished = True
        out = self._ensure_message_start()
        # Empty assistant reply still needs a text block for some clients.
        if not self.text_started and not self.tools:
            out.extend(self._ensure_text_start())
        out.extend(self._close_text_if_open())
        for block in sorted(self.tools.values(), key=lambda b: b.anthropic_index):
            if block.started and not block.closed:
                block.closed = True
                out.append(
                    _sse("content_block_stop", {"type": "content_block_stop", "index": block.anthropic_index})
                )
        out.append(
            _sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": int(self.output_tokens or 0)},
                },
            )
        )
        # Patch message_start usage if we learned input tokens late — Anthropic clients
        # primarily read output_tokens from message_delta; input is best-effort.
        out.append(_sse("message_stop", {"type": "message_stop"}))
        return out


def request_body_wants_stream(body: bytes | str | None) -> bool:
    if not body:
        return False
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return False
    else:
        text = body
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, dict) and bool(parsed.get("stream"))
