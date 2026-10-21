#!/usr/bin/env bash
# Optional safety net — end the active Boucle (e.g. from a custom Stop hook).
# Not wired by default: a Stop hook would close the Boucle after every Claude
# reply (including clarifying questions). Skills tokensaver-agentic-loops +
# tokensaver-close-loop own start/end via MCP.
#
# To enable: add a Stop hook in hooks.json pointing at this script.
set -euo pipefail
ROOT="${EGRESS_CONFIG_DIR:-$HOME/.tokensaver-egress}"
LOOP_FILE="$ROOT/current-loop.env"
if [[ ! -f "$LOOP_FILE" ]]; then
  exit 0
fi
set -a
# shellcheck disable=SC1090
source "$LOOP_FILE" 2>/dev/null || true
set +a
if [[ -z "${TOKENSAVER_LOOP_ID:-}" ]]; then
  exit 0
fi
EXTRA=()
ingest="${TOKENSAVER_INGEST_URL:-}"
if [[ -f "$ROOT/env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT/env" 2>/dev/null || true
  set +a
  ingest="${TOKENSAVER_INGEST_URL:-$ingest}"
fi
if [[ "$ingest" == *"localhost"* || "$ingest" == *"127.0.0.1"* ]]; then
  EXTRA+=(--local)
fi
if command -v tokensaver >/dev/null 2>&1; then
  tokensaver loop end "${EXTRA[@]}" --loop-id "$TOKENSAVER_LOOP_ID" --reason work_complete >/dev/null 2>&1 || true
fi
exit 0
