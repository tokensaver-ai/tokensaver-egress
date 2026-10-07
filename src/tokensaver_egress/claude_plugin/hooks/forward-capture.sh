#!/usr/bin/env bash
# Fail-open forward of Claude Code hook JSON (stdin) → TokenSaver capture /hooks.
# Never blocks the agent: always exit 0, short timeout.
set -u
URL="${TOKENSAVER_CLAUDE_HOOKS_URL:-http://127.0.0.1:8789/hooks}"
EVENT="${1:-unknown}"
BODY="$(cat || true)"
# Inject hook event name if missing (Claude may send it already).
if command -v python3 >/dev/null 2>&1; then
  BODY="$(EVENT="$EVENT" python3 -c '
import json, os, sys
raw = sys.stdin.read() or "{}"
try:
    data = json.loads(raw)
except Exception:
    data = {"raw": raw}
if not isinstance(data, dict):
    data = {"value": data}
ev = os.environ.get("EVENT") or "unknown"
data.setdefault("hook_event_name", ev)
print(json.dumps(data, ensure_ascii=False))
' <<<"$BODY" 2>/dev/null || echo "$BODY")"
fi
curl -sS -m 2 -X POST "$URL" \
  -H "Content-Type: application/json" \
  -H "X-Claude-Hook-Event: $EVENT" \
  --data-binary "$BODY" >/dev/null 2>&1 || true
exit 0
