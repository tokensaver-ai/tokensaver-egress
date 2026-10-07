"""P0 Claude capture envelope + hooks fail-open."""

from __future__ import annotations

import json

from tokensaver_egress.claude_capture import (
    SessionSeqStore,
    _extract_hook_ids,
    build_envelope,
    handle_capture_request,
)


def test_session_seq_monotonic():
    store = SessionSeqStore()
    assert store.next_seq("s1") == 1
    assert store.next_seq("s1") == 2
    assert store.next_seq("s2") == 1
    assert store.next_seq("s1") == 3


def test_build_envelope_fields():
    env = build_envelope(
        source="hook",
        session_id="sess-1",
        payload={"hook_event": "PreToolUse"},
        tool_use_id="toolu_1",
        agent_id="main",
    )
    assert env["source"] == "hook"
    assert env["session_id"] == "sess-1"
    assert env["seq"] >= 1
    assert env["tool_use_id"] == "toolu_1"
    assert env["ts"].endswith("Z")


def test_extract_hook_ids_keeps_top_level_session_id():
    """Regression: ``a or b if cond else None`` dropped session_id (0.1.52)."""
    ids = _extract_hook_ids({"session_id": "abc-123", "tool_name": "Write"})
    assert ids["session_id"] == "abc-123"
    ids2 = _extract_hook_ids({"session": {"id": "nested"}, "tool_name": "Bash"})
    assert ids2["session_id"] == "nested"


def test_hooks_fail_open_continue():
    body = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "abc",
            "tool_name": "Bash",
        }
    ).encode()
    status, resp, ctype = handle_capture_request(
        "POST", "/hooks", {"content-type": "application/json"}, body
    )
    assert status == 200
    assert ctype == "application/json"
    data = json.loads(resp.decode())
    assert data.get("continue") is True


def test_otlp_relay_accepts_bytes():
    status, resp, _ctype = handle_capture_request(
        "POST",
        "/v1/traces",
        {"content-type": "application/x-protobuf"},
        b"\x00\x01\x02raw-otlp",
    )
    assert status == 200
    assert resp == b""


def test_health():
    status, resp, _ = handle_capture_request("GET", "/health", {}, b"")
    assert status == 200
    assert json.loads(resp.decode())["ok"] is True
