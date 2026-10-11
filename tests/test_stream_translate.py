"""OpenAI SSE → Anthropic SSE live translator (egress model_routing)."""

from __future__ import annotations

from tokensaver_egress.stream_translate import OpenAIToAnthropicSSETranslator, request_body_wants_stream


def test_request_body_wants_stream():
    assert request_body_wants_stream(b'{"stream":true,"model":"x"}') is True
    assert request_body_wants_stream(b'{"stream":false}') is False
    assert request_body_wants_stream(b"not-json") is False


def test_openai_to_anthropic_text_stream():
    tr = OpenAIToAnthropicSSETranslator(client_model="claude-sonnet-4-6")
    chunks = [
        b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","model":"stealth/ox-alpha",'
        b'"choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n',
        b'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"Salut"},"finish_reason":null}]}\n\n',
        b'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":" !"},"finish_reason":null}]}\n\n',
        b'data: {"object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":10,"completion_tokens":3}}\n\n',
        b"data: [DONE]\n\n",
    ]
    out = b"".join(b"".join(tr.feed(c)) for c in chunks)
    out += b"".join(tr.finish())
    text = out.decode("utf-8")
    assert "event: message_start" in text
    assert "claude-sonnet-4-6" in text
    assert "text_delta" in text
    assert "Salut" in text
    assert " !" in text
    assert "event: message_delta" in text
    assert "end_turn" in text
    assert "event: message_stop" in text
    assert tr.input_tokens == 10
    assert tr.output_tokens == 3


def test_openai_to_anthropic_tool_stream():
    tr = OpenAIToAnthropicSSETranslator(client_model="claude-haiku-4-5")
    chunks = [
        (
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function",'
            b'"function":{"name":"Bash","arguments":""}}]}}]}\n\n'
        ),
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"cmd\\":\\"ls\\"}"}}]}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":2,"completion_tokens":4}}\n\n',
        b"data: [DONE]\n\n",
    ]
    out = b"".join(b"".join(tr.feed(c)) for c in chunks)
    out += b"".join(tr.finish())
    text = out.decode("utf-8")
    assert '"type":"tool_use"' in text
    assert "Bash" in text
    assert "input_json_delta" in text
    assert "tool_use" in text  # stop_reason
    assert "message_stop" in text


def test_split_sse_across_tcp_chunks():
    tr = OpenAIToAnthropicSSETranslator(client_model="m")
    part1 = b'data: {"choices":[{"delta":{"content":"Hi"'
    part2 = b'}}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    out = b"".join(tr.feed(part1))
    assert out == b""  # incomplete JSON event
    out = b"".join(tr.feed(part2))
    out += b"".join(tr.finish())
    assert b"text_delta" in out
    assert b"Hi" in out
    assert b"message_stop" in out
