"""Body integrity helpers — truncation detect + proxy_body_altered metric."""

from __future__ import annotations

import json

from tokensaver_egress.body_integrity import (
    assess_request_body,
    integrity_block_payload,
    should_block_invalid_llm_body,
)


def test_identity_passthrough_not_altered():
    body = json.dumps({"model": "x", "messages": []}).encode()
    attrs = assess_request_body(
        body_in=body,
        body_out=body,
        headers_out={"Content-Length": str(len(body))},
        intentional_transform=False,
    )
    assert attrs["proxy_body_altered"] is False
    assert attrs["client_json_valid"] is True
    assert should_block_invalid_llm_body(attrs) is False


def test_intentional_transform_not_flagged_as_proxy_altered():
    body_in = json.dumps({"model": "a", "messages": []}).encode()
    body_out = json.dumps({"model": "b", "messages": []}).encode()
    attrs = assess_request_body(
        body_in=body_in,
        body_out=body_out,
        headers_out={"Content-Length": str(len(body_out))},
        intentional_transform=True,
    )
    assert attrs["proxy_body_altered"] is False
    assert attrs["intentional_body_transform"] is True


def test_unexpected_rewrite_sets_proxy_body_altered():
    body_in = json.dumps({"model": "a"}).encode()
    body_out = json.dumps({"model": "b"}).encode()
    attrs = assess_request_body(
        body_in=body_in,
        body_out=body_out,
        headers_out={"Content-Length": str(len(body_out))},
        intentional_transform=False,
    )
    assert attrs["proxy_body_altered"] is True
    assert should_block_invalid_llm_body(attrs) is True


def test_exactly_2mib_invalid_json_is_truncation_suspect():
    # Incomplete JSON padded to exactly 2 MiB (Bun HWM ceiling signature).
    prefix = b'{"model":"x","messages":[{"role":"user","content":"'
    pad = b"A" * (2 * 1024 * 1024 - len(prefix))
    body = prefix + pad
    assert len(body) == 2 * 1024 * 1024
    attrs = assess_request_body(
        body_in=body,
        body_out=body,
        headers_out={"Content-Length": str(len(body))},
        intentional_transform=False,
    )
    assert attrs["client_json_valid"] is False
    assert attrs["client_body_truncation_suspect"] is True
    assert should_block_invalid_llm_body(attrs) is True
    payload = json.loads(integrity_block_payload(attrs))
    assert payload["error"]["tokensaver_reason"] == "client_body_truncated"


def test_content_length_mismatch_blocks():
    body = b'{"ok":true}'
    attrs = assess_request_body(
        body_in=body,
        body_out=body,
        headers_out={"Content-Length": "999"},
        intentional_transform=False,
    )
    assert attrs["content_length_mismatch"] is True
    assert should_block_invalid_llm_body(attrs) is True
