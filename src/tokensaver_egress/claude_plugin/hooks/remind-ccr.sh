#!/usr/bin/env bash
# PostToolUse — when a tool result contains a TokenSaver CCR marker, remind
# Claude to retrieve or re-Read disk. Never claim TokenSaver blocks content.
set -euo pipefail

INPUT="$(cat || true)"

HAS_CCR="$(printf '%s' "$INPUT" | python3 -c '
import json,sys,re
raw=sys.stdin.read()
try:
    d=json.loads(raw) if raw.strip() else {}
except Exception:
    d={}
blob=""
for k in ("tool_response","toolResponse","tool_result","toolResult","content","result","output"):
    v=d.get(k)
    if isinstance(v,str):
        blob+=v
    elif v is not None:
        blob+=json.dumps(v,ensure_ascii=False)
if not blob:
    blob=raw
print("1" if re.search(r"\[TokenSaver CCR id=", blob) or "tokensaver_retrieve_context" in blob else "0")
' 2>/dev/null || echo 0)"

if [[ "$HAS_CCR" != "1" ]]; then
  exit 0
fi

CTX="TokenSaver CCR (mandatory): this tool result was compressed. You CAN get the full original — call tokensaver_retrieve_context(content_id=…) OR re-Read the same file path from disk. NEVER tell the user TokenSaver refuses/blocks/summarizes full content; NEVER put that in Limites/reports; NEVER switch to tests-only because of CCR."

python3 -c '
import json,sys
ctx=sys.argv[1]
print(json.dumps({
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": ctx,
  }
}, ensure_ascii=False))
' "$CTX"
exit 0
