---
name: tokensaver-close-loop
description: >-
  Use when finishing a coding task, saying done, reporting success/failure, or
  after verification (/verify, tests green). Closes the active TokenSaver
  BusinessLoop via tokensaver_loop_end so Boucles do not stay running and clears
  the egress current-loop stamp. Also use when the user says the task is
  finished, abort, or cancel the session goal — including after egress-linked
  agentic work.
---

# Close TokenSaver Boucle on task completion

When you are about to tell the user the work is **done**, **failed**, or **cancelled**:

1. If this session has a BusinessLoop (`loop_id` known, or
   `TOKENSAVER_LOOP_ID` / `~/.tokensaver-egress/current-loop.env` still stamped),
   call **`tokensaver_loop_end`** first — clears the local egress stamp so later
   captures stop attaching to that Boucle.
2. Then give the short user-facing summary.

| Outcome | `stop_reason` | `verification` |
|---------|----------------|----------------|
| Deliverable done (lab, file, PR) | `work_complete` | usually omit — safest under dual evaluator |
| Numeric goal met | `goal_met` | `{ "goal_met": true, "metrics": { "<key>": <value> } }` |
| Stop-signal goal | `found` | `{ "stop_signal": "found" }` |
| User abort / stop | `cancelled` | — |
| Hit turn limit | `max_turns` | — |

```text
tokensaver_loop_end({
  loop_id: "<id>",
  stop_reason: "work_complete",
  summary: "<one line>"
})
```

**Avoid** `verification.goal_met: true` without metrics when the goal had a
`metric_key` — that yields `verification_failed` /
`CLIENT_CLAIM_IGNORED_NO_METRIC`. Prefer `work_complete` for file labs.

If unsure whether a loop is open: **`tokensaver_loop_get`** with the remembered
id, or skip end if you never called start and no stamp exists.

Do **not** ask the user to run `tokensaver loop end`.
