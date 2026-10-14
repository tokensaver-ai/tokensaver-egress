"""ACP-4 transparent mode — SNI / Host-label parsing (hermetic, no sockets).

Run from apps/egress:  python -m pytest tests/test_transparent.py
"""

from __future__ import annotations

import struct

from tokensaver_egress.transparent import host_label_from_prefix, parse_http_host, parse_sni


def _client_hello_with_sni(server_name: str) -> bytes:
    name = server_name.encode("idna")
    # server_name_list = name_type(0) + name_len(2) + name
    sn_entry = b"\x00" + struct.pack("!H", len(name)) + name
    sn_list = struct.pack("!H", len(sn_entry)) + sn_entry
    sni_ext = b"\x00\x00" + struct.pack("!H", len(sn_list)) + sn_list

    extensions = sni_ext
    body = (
        b"\x03\x03"  # client version TLS 1.2
        + b"\x00" * 32  # random
        + b"\x00"  # session_id_len = 0
        + struct.pack("!H", 2) + b"\x13\x01"  # cipher suites (1)
        + b"\x01\x00"  # compression methods: len=1, null
        + struct.pack("!H", len(extensions))
        + extensions
    )
    handshake = b"\x01" + struct.pack("!I", len(body))[1:] + body  # 3-byte length
    record = b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake
    return record


def test_parse_sni_extracts_server_name():
    data = _client_hello_with_sni("api.openai.com")
    assert parse_sni(data) == "api.openai.com"


def test_parse_sni_returns_none_for_non_tls():
    assert parse_sni(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n") is None
    assert parse_sni(b"") is None
    assert parse_sni(b"\x16\x03\x01") is None  # truncated


def test_parse_http_host():
    host, port = parse_http_host(b"POST /v1 HTTP/1.1\r\nHost: example.com:8080\r\n\r\n")
    assert host == "example.com"
    assert port == 8080
    host2, port2 = parse_http_host(b"GET / HTTP/1.1\r\nHost: plain.test\r\n\r\n")
    assert host2 == "plain.test"
    assert port2 == 80


def test_host_label_prefers_sni_then_http_then_ip():
    tls = _client_hello_with_sni("api.anthropic.com")
    assert host_label_from_prefix(tls, "1.2.3.4") == "api.anthropic.com"

    http = b"GET / HTTP/1.1\r\nHost: svc.internal\r\n\r\n"
    assert host_label_from_prefix(http, "1.2.3.4") == "svc.internal"

    assert host_label_from_prefix(b"\x00\x01\x02garbage", "9.9.9.9") == "9.9.9.9"
