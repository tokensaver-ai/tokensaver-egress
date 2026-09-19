---
name: tokensaver-close-loop
description: >-
  Use when finishing a coding task, saying Goal achieved / done, reporting
  success/failure, or after verification. Silently closes the TokenSaver Boucle
  via tokensaver_loop_end (clear egress stamp) — never ask the user to name MCP
  or run tokensaver loop end. Also on abort/cancel.
---

# Close TokenSaver Boucle (silent)

When you are about to tell the user the work is **done**, **Goal achieved**,
**failed**, or **cancelled**:

1. If a Boucle is open (`loop_id` remembered or
   `TOKENSAVER_LOOP_ID` in `~/.tokensaver-egress/current-loop.env`), call
   **`tokensaver_loop_end`** first.
2. Then give the short user-facing summary — **without** mentioning MCP.

| Outcome | `stop_reason` | `verification` |
|---------|----------------|----------------|
| Deliverable done (lab, file, PR) | `work_complete` | usually omit |
| Numeric goal met | `goal_met` | `{ "goal_met": true, "metrics": { "<key>": <value> } }` |
| User abort / stop | `cancelled` | — |
| Hit turn limit | `max_turns` | — |

Prefer `work_complete` for file labs when metrics are unclear.

Do **not** ask the user to run `tokensaver loop end`.
