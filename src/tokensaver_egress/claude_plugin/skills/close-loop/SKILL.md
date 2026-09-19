---
name: tokensaver-close-loop
description: >-
  Use when finishing a coding task, saying Goal achieved / done, reporting
  success/failure, or after verification. Silently closes the TokenSaver Boucle
  via tokensaver_loop_end (clear egress stamp) — never ask the user to name MCP
  or run tokensaver loop end. Also on abort/cancel. Prefer work_complete for
  file labs; never goal_met without metrics (avoids METRIC_MISSING).
---

# Close TokenSaver Boucle (silent)

When you are about to tell the user the work is **done**, **Goal achieved**,
**failed**, or **cancelled**:

1. If a Boucle is open (`loop_id` remembered or
   `TOKENSAVER_LOOP_ID` in `~/.tokensaver-egress/current-loop.env`), call
   **`tokensaver_loop_end`** **first**.
2. Then give the short user-facing summary — **without** mentioning MCP.

## Default end for `/goal` file labs (important)

If the user asked to write files / do N turns (e.g. `lab_three.txt`,
`three_steps == 1`) and you **did the work**, close with:

```text
tokensaver_loop_end({
  loop_id: "<id>",
  stop_reason: "work_complete",
  summary: "<one line>"
})
```

**Do not** send `stop_reason: "goal_met"` with only `{ "goal_met": true }`.
That yields **`METRIC_MISSING` / `verification_failed`** on Flux IA even when
Claude’s local `/goal` shows ✔ Goal achieved.

| Outcome | `stop_reason` | `verification` |
|---------|----------------|----------------|
| File / lab / PR deliverable done (default) | **`work_complete`** | **omit** |
| Numeric goal **and** you have the metric value | `goal_met` | `{ "goal_met": true, "metrics": { "<metric_key>": <value> } }` |
| Stop-signal goal | `found` | `{ "stop_signal": "found" }` |
| User abort | `cancelled` | — |
| Hit turn limit | `max_turns` | — |

### Hard ban

- ❌ `verification: { "goal_met": true }` alone  
- ❌ `goal_met` when you started with `metric_key` but did not pass `metrics`  
- ✅ `work_complete` without verification for almost all Claude Code labs  

Do **not** ask the user to run `tokensaver loop end`.
