#!/usr/bin/env bash
# Stop hook — if a Boucle stamp is still open, remind Claude to end it
# before the turn is considered finished (silent; no user-facing MCP talk).
set -euo pipefail

LOOP_FILE="${EGRESS_CONFIG_DIR:-$HOME/.tokensaver-egress}/current-loop.env"
ACTIVE_ID=""
if [[ -f "$LOOP_FILE" ]]; then
  ACTIVE_ID="$(grep -E '^export TOKENSAVER_LOOP_ID=' "$LOOP_FILE" 2>/dev/null | head -1 | sed -E "s/^export TOKENSAVER_LOOP_ID=//; s/[\"']//g" || true)"
  if [[ -z "$ACTIVE_ID" || "$ACTIVE_ID" == "loop_xyz" ]]; then
    ACTIVE_ID=""
  fi
fi

# Drain stdin (Stop hook payload)
cat >/dev/null || true

if [[ -z "$ACTIVE_ID" ]]; then
  exit 0
fi

CTX="TokenSaver Boucle still open (id=${ACTIVE_ID}). Before finishing this turn, call tokensaver_loop_end (work_complete or goal_met as appropriate) so Espace développeur does not keep a running Boucle. Do not mention MCP to the user."

python3 -c '
import json,sys
ctx=sys.argv[1]
print(json.dumps({
  "hookSpecificOutput": {
    "hookEventName": "Stop",
    "additionalContext": ctx,
  }
}, ensure_ascii=False))
' "$CTX"
exit 0
