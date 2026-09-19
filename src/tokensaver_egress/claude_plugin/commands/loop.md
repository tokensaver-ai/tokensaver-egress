---
description: BusinessLoop — Claude opens/closes Boucles via MCP automatically (no terminal tokensaver loop)
argument-hint: "[optional hint: goal text]"
---

# Business loop — automatic (MCP)

Do **not** ask the user to run `tokensaver loop start` / `end`.

Follow skill **`tokensaver-agentic-loops`** then **`tokensaver-close-loop`**:

1. **`tokensaver_loop_start`** — derive goal from the user message (or $ARGUMENTS)
2. Do the work (tools, verification)
3. **`tokensaver_loop_end`** with `work_complete` / `goal_met` / `cancelled` **before** saying done

If $ARGUMENTS is non-empty, use it as `goal.expression` with `max_turns: 5`.
