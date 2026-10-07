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

When the deliverable is done (files written, N tours finished):

1. Call **`tokensaver_loop_end`** once with **`work_complete`** (no verification)
   unless you have a **real** measured metric.
2. Summarize for the user. **Stop tooling.**

```text
tokensaver_loop_end({
  loop_id: "<id>",
  stop_reason: "work_complete",
  summary: "<one line>"
})
```

## Hard bans

- ❌ `stop_reason: "goal_met"` because the user wrote « STOP goal_met » in a lab prompt
- ❌ inventing metrics (`three_steps: 1`, etc.)
- ❌ second `tokensaver_loop_end` after success (causes errors / noise)
- ❌ opening a new Boucle after close to satisfy Claude « Goal not yet met »

User text « goal_met » = Claude `/goal` slang. TokenSaver lab close = **`work_complete`**.

| Outcome | `stop_reason` | `verification` |
|---------|----------------|----------------|
| File / lab / tours done | **`work_complete`** | **omit** |
| Real metric + value | `goal_met` | `{ "goal_met": true, "metrics": { "<key>": <value> } }` |
| Abort | `cancelled` | — |
