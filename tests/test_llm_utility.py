"""Client utility LLM path / request detection (Claude Code fast-path)."""

from __future__ import annotations

import json

from tokensaver_egress.llm_utility import is_client_utility_llm_path, is_client_utility_llm_request


def test_utility_count_tokens():
    assert is_client_utility_llm_path("/v1/messages/count_tokens?beta=true")
    assert is_client_utility_llm_path("/v1/messages/count_tokens")
    assert is_client_utility_llm_request("/v1/messages/count_tokens?beta=true")


def test_beta_haiku_is_utility():
    body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 16}).encode()
    assert is_client_utility_llm_request(
        "/v1/messages?beta=true",
        body=body,
    )
    assert is_client_utility_llm_request(
        "/v1/messages?beta=true",
        model="claude-haiku-4-5",
    )


def test_beta_sonnet_main_chat_not_utility():
    """Claude Code main agent often uses ?beta=true — must still be routed."""
    body = json.dumps({"model": "claude-sonnet-5", "max_tokens": 16000, "messages": []}).encode()
    assert not is_client_utility_llm_request("/v1/messages?beta=true", body=body)
    assert not is_client_utility_llm_request(
        "/v1/messages?beta=true&foo=1",
        model="claude-sonnet-5",
    )
    assert not is_client_utility_llm_path("/v1/messages?beta=true")


def test_chat_messages_not_utility():
    assert not is_client_utility_llm_path("/v1/messages")
    assert not is_client_utility_llm_path("/v1/messages/stream")
    assert not is_client_utility_llm_request("/v1/messages", model="claude-sonnet-5")


def test_empty_path():
    assert not is_client_utility_llm_path(None)
    assert not is_client_utility_llm_path("")
    assert not is_client_utility_llm_request(None)
