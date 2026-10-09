"""Transparent-mode capture (ACP-4): TPROXY / iptables-REDIRECT.

In transparent mode the client connects directly to the destination IP:443 and the
kernel redirects the flow to this proxy. There is no ``CONNECT`` line, so we must:

1. recover the **original destination** via ``SO_ORIGINAL_DST`` (NAT/REDIRECT), and
2. read the first bytes to extract the **SNI** (TLS) or **Host** header (HTTP) for a
   human-readable host label,

then blind-tunnel to the original destination (metadata-only capture). Transparent
MITM is intentionally out of scope here: terminating TLS on an already-received
ClientHello is fragile under asyncio; explicit-proxy CONNECT remains the MITM path.
"""

from __future__ import annotations

import logging
import socket
import struct

logger = logging.getLogger("tokensaver-egress.transparent")

SO_ORIGINAL_DST = 80  # Linux netfilter, <linux/netfilter_ipv4.h>


def original_dst(sock: socket.socket) -> tuple[str, int] | None:
    """Return the pre-NAT destination (ip, port) for a REDIRECT'd IPv4 socket."""
    if sock is None:
        return None
    try:
        # struct sockaddr_in: family(2) port(2, big-endian) addr(4) zero(8) = 16 bytes
        raw = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
    except (OSError, AttributeError) as exc:
        logger.debug("original_dst unavailable: %s", exc)
        return None
    port = struct.unpack("!H", raw[2:4])[0]
    ip = socket.inet_ntoa(raw[4:8])
    return ip, port


def parse_sni(data: bytes) -> str | None:
    """Best-effort SNI extraction from a TLS ClientHello record (bounded, safe)."""
    try:
        if len(data) < 43 or data[0] != 0x16:  # not a TLS handshake record
            return None
        # Skip: record hdr(5) + handshake type(1) + handshake len(3) + version(2) + random(32)
        idx = 5 + 1 + 3 + 2 + 32
        if idx >= len(data):
            return None
        session_id_len = data[idx]
        idx += 1 + session_id_len
        if idx + 2 > len(data):
            return None
        cipher_len = struct.unpack("!H", data[idx : idx + 2])[0]
        idx += 2 + cipher_len
        if idx + 1 > len(data):
            return None
        comp_len = data[idx]
        idx += 1 + comp_len
        if idx + 2 > len(data):
            return None
        ext_total = struct.unpack("!H", data[idx : idx + 2])[0]
        idx += 2
        end = min(len(data), idx + ext_total)
        while idx + 4 <= end:
            ext_type = struct.unpack("!H", data[idx : idx + 2])[0]
            ext_len = struct.unpack("!H", data[idx + 2 : idx + 4])[0]
            idx += 4
            if ext_type == 0x0000:  # server_name
                # server_name_list: list_len(2) name_type(1) name_len(2) name
                if idx + 5 > len(data):
                    return None
                name_len = struct.unpack("!H", data[idx + 3 : idx + 5])[0]
                name = data[idx + 5 : idx + 5 + name_len]
                try:
                    return name.decode("idna") if name else None
                except Exception:
                    return name.decode("latin-1", errors="ignore") or None
            idx += ext_len
        return None
    except Exception:
        return None


def parse_http_host(data: bytes) -> tuple[str | None, int]:
    """Extract Host header (and port) from a plaintext HTTP request prefix."""
    try:
        head = data.split(b"\r\n\r\n", 1)[0]
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"host:"):
                host_part = line.split(b":", 1)[1].strip().decode("latin-1", errors="ignore")
                if ":" in host_part:
                    h, _, p = host_part.partition(":")
                    return h or None, int(p or "80")
                return host_part or None, 80
    except Exception:
        pass
    return None, 80


def host_label_from_prefix(prefix: bytes, fallback_ip: str) -> str:
    """Derive a human-readable host: SNI (TLS) → Host header (HTTP) → original IP."""
    sni = parse_sni(prefix)
    if sni:
        return sni
    http_host, _ = parse_http_host(prefix)
    if http_host:
        return http_host
    return fallback_ip
