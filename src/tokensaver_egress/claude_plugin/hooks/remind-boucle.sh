#!/usr/bin/env bash
# UserPromptSubmit / UserPromptExpansion — remind Claude to open a TokenSaver
# Boucle silently when the user runs /goal or any implementing task.
# Never ask the user to type MCP tool names.
set -euo pipefail

ROOT="${CLAUDE_PLUGIN_ROOT:-${HOME}/.claude/skills/tokensaver-router}"
LOOP_FILE="${EGRESS_CONFIG_DIR:-$HOME/.tokensaver-egress}/current-loop.env"

INPUT="$(cat || true)"
EVENT="$(printf '%s' "$INPUT" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print("UserPromptSubmit")
    raise SystemExit(0)
print(d.get("hook_event_name") or d.get("hookEventName") or "UserPromptSubmit")
' 2>/dev/null || echo UserPromptSubmit)"
PROMPT="$(printf '%s' "$INPUT" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
print(d.get("prompt") or d.get("expanded_prompt") or d.get("command") or "")
' 2>/dev/null || true)"

# Detect active stamp (non-comment TOKENSAVER_LOOP_ID=…)
ACTIVE_ID=""
if [[ -f "$LOOP_FILE" ]]; then
  ACTIVE_ID="$(grep -E '^export TOKENSAVER_LOOP_ID=' "$LOOP_FILE" 2>/dev/null | head -1 | sed -E "s/^export TOKENSAVER_LOOP_ID=//; s/[\"']//g" || true)"
  # Ignore placeholder / cleared files
  if [[ -z "$ACTIVE_ID" || "$ACTIVE_ID" == "loop_xyz" ]]; then
    ACTIVE_ID=""
  fi
fi

# Heuristic: agentic / goal / deliverable prompts
PL="$(printf '%s' "$PROMPT" | tr '[:upper:]' '[:lower:]')"
NEED=0
if [[ "$PL" =~ /goal ]] || [[ "$PL" =~ (^|[[:space:]])goal[[:space:]] ]]; then NEED=1; fi
if [[ "$PL" =~ max[_[:space:]]*turns|max[[:space:]]*[0-9]+[[:space:]]*tours|jusqu ]] ; then NEED=1; fi
if [[ "$PL" =~ continue|goal_met|lab_|step[[:space:]]*[0-9]|iteration|boucle ]]; then NEED=1; fi
if [[ "$PL" =~ (fix|create|implement|ship|refactor|write|écris|ecris|ajoute|créer|creer) ]]; then NEED=1; fi

# Always nudge when no active stamp and prompt is non-trivial (>12 chars, not a pure greeting)
if [[ -z "$ACTIVE_ID" && ${#PROMPT} -gt 12 ]]; then
  if [[ ! "$PL" =~ ^(salut|hello|hi|hey|bonjour|bonsoir|thanks|merci|ok|oui|non)([.!?[:space:]]|$) ]]; then
    NEED=1
  fi
fi

if [[ "$NEED" -eq 0 ]]; then
  exit 0
fi

if [[ -n "$ACTIVE_ID" ]]; then
  CTX="TokenSaver Boucle (silent): stamp active (id=${ACTIVE_ID}). If user asked for N tours: after EACH tour call tokensaver_loop_iteration(iteration_index=k, decision=continue|stop) — never batch all writes then one end (that shows 1/N). When done: tokensaver_loop_end work_complete only. No goal_met, no invented metrics, no second Boucle. Never mention MCP to the user."
else
  CTX="TokenSaver Boucle (silent, mandatory): BEFORE first Edit/Write, tokensaver_loop_start once goal_based goal={expression, max_turns:N} WITHOUT metric_key for labs. If user wants N tours/steps: do tour k only, then tokensaver_loop_iteration(k, continue) for k<N and (N, stop) then end work_complete. NEVER write all N lines then a single end (Flux shows 1/N). Never mention MCP to the user."
fi

# JSON stdout for Claude Code hooks
python3 -c '
import json,sys
ctx=sys.argv[1]
event=sys.argv[2] if len(sys.argv)>2 else "UserPromptSubmit"
print(json.dumps({
  "hookSpecificOutput": {
    "hookEventName": event,
    "additionalContext": ctx,
  }
}, ensure_ascii=False))
' "$CTX" "$EVENT"
exit 0
