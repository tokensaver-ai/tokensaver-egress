#!/usr/bin/env bash
# ACP-4 — transparent egress capture (Linux, iptables REDIRECT + policy routing).
# Requires root and CAP_NET_ADMIN. Adjust EGRESS_PORT / LAN_CIDR before running.
#
# Pair with:  tokensaver-egress serve --transparent --port "${EGRESS_PORT}"
# The proxy recovers the original destination via SO_ORIGINAL_DST and the SNI from
# the TLS ClientHello, then blind-tunnels (metadata-only capture).
#
# Usage:
#   sudo EGRESS_PORT=8888 LAN_CIDR=10.0.0.0/8 ./tproxy-setup.sh up
#   sudo ./tproxy-setup.sh down
set -euo pipefail

EGRESS_PORT="${EGRESS_PORT:-8888}"
LAN_CIDR="${LAN_CIDR:-10.0.0.0/8}"
MARK=0x1
TABLE=100
ACTION="${1:-up}"

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run as root (iptables/ip rule)." >&2
    exit 1
  fi
}

up() {
  echo "Routing HTTPS(443)+HTTP(80) from ${LAN_CIDR} → local tokensaver-egress :${EGRESS_PORT}"

  ip rule add fwmark "${MARK}" table "${TABLE}" 2>/dev/null || true
  ip route add local default dev lo table "${TABLE}" 2>/dev/null || true

  iptables -t mangle -N TS_EGRESS 2>/dev/null || iptables -t mangle -F TS_EGRESS
  iptables -t mangle -A TS_EGRESS -d 127.0.0.0/8 -j RETURN
  iptables -t mangle -A TS_EGRESS -p tcp -m multiport --dports 80,443 -s "${LAN_CIDR}" -j MARK --set-mark "${MARK}"
  iptables -t mangle -C PREROUTING -j TS_EGRESS 2>/dev/null || iptables -t mangle -A PREROUTING -j TS_EGRESS

  iptables -t nat -N TS_EGRESS_NAT 2>/dev/null || iptables -t nat -F TS_EGRESS_NAT
  iptables -t nat -A TS_EGRESS_NAT -p tcp -m multiport --dports 80,443 -j REDIRECT --to-ports "${EGRESS_PORT}"
  iptables -t nat -C OUTPUT -m mark --mark "${MARK}" -j TS_EGRESS_NAT 2>/dev/null \
    || iptables -t nat -A OUTPUT -m mark --mark "${MARK}" -j TS_EGRESS_NAT

  echo "Done. Start the proxy:  tokensaver-egress serve --transparent --port ${EGRESS_PORT}"
  echo "Teardown:  sudo $0 down"
}

down() {
  iptables -t mangle -D PREROUTING -j TS_EGRESS 2>/dev/null || true
  iptables -t mangle -F TS_EGRESS 2>/dev/null || true
  iptables -t mangle -X TS_EGRESS 2>/dev/null || true
  iptables -t nat -D OUTPUT -m mark --mark "${MARK}" -j TS_EGRESS_NAT 2>/dev/null || true
  iptables -t nat -F TS_EGRESS_NAT 2>/dev/null || true
  iptables -t nat -X TS_EGRESS_NAT 2>/dev/null || true
  ip rule del fwmark "${MARK}" table "${TABLE}" 2>/dev/null || true
  ip route flush table "${TABLE}" 2>/dev/null || true
  echo "Transparent egress rules removed."
}

require_root
case "${ACTION}" in
  up) up ;;
  down) down ;;
  *) echo "Usage: $0 {up|down}" >&2; exit 2 ;;
esac
