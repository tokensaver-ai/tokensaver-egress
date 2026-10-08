---
name: tokensaver-agentic-loops
description: >-
  ALWAYS use on /goal, max turns, iterative coding, Edit/Write/Bash deliverables,
  or any task behind TokenSaver egress — silently open/iterate/close a BusinessLoop
  so Flux IA Boucles appear. User must NEVER type MCP tool names or tokensaver loop
  CLI; you call tokensaver_loop_* yourself without mentioning them. Also on 403
  MISSING_LOOP_ID / LOOP_TERMINAL (start a fresh loop). Skip only pure chat Q&A.
---

# TokenSaver — silent auto BusinessLoop (ACP-9)

**UX contract:** the user writes a normal prompt or `/goal …`. You govern the
Boucle **in the background**. Do **not** ask them to name MCP tools.

## Silent lifecycle (mandatory)

### 0 — Start (once, before first Edit/Write)

Reuse a real stamp in `~/.tokensaver-egress/current-loop.env` if present
(not empty / not `loop_xyz`). Else **`tokensaver_loop_start`** once:

```json
{
  "loop_kind": "goal_based",
  "goal": { "expression": "<user text>", "max_turns": N }
}
```

Lab labels (`three_steps == 1`, file tours): **no** `metric_key` / `op` / `threshold`.

One user task = **one** Boucle. Retry start once on timeout. Never open a second
Boucle to backfill.

### 1 — « N tours » = N TokenSaver iterations (mandatory)

If the user asks for **exactly N tours / turns / steps** (e.g. 3):

For each `k` in `1..N`, in order:

1. Do **only** the work for tour `k` (e.g. write/append `"step k"` — not all steps at once).
2. Call **`tokensaver_loop_iteration`** with:
   - `iteration_index: k`
   - `decision: "continue"` if `k < N`, else `"stop"`
   - optional `sco_state: { "tour": k }`

**Forbidden:** writing all N lines in one shot then a single `tokensaver_loop_end`
→ Flux IA shows **1/N tours**. That is wrong.

Sequence for N=3:

```text
start → write step1 → iteration(1, continue)
      → write step2 → iteration(2, continue)
      → write step3 → iteration(3, stop) → end(work_complete)
```

### 2 — End (once, after the Nth iteration)

```text
tokensaver_loop_end({ loop_id, stop_reason: "work_complete", summary: "…" })
```

Omit `verification` for file labs.

Hard bans:

- ❌ batching all tours then one end (under-counts iterations)
- ❌ `goal_met` / invented metrics for labs
- ❌ second `tokensaver_loop_end` after success
- ❌ second Boucle after close

| Outcome | `stop_reason` | `verification` |
|---------|---------------|----------------|
| File / lab / N tours done | **`work_complete`** | **omit** |
| Real numeric metric + value | `goal_met` | `{ "goal_met": true, "metrics": { "<key>": <value> } }` |
| Abort | `cancelled` | — |

## Errors

| Symptom | Action |
|---------|--------|
| `LOOP_TERMINAL` / stale stamp | New start, continue |
| `MISSING_LOOP_ID` | Start, retry once |
| Second end / HTTP error | **Stop** |
| MCP down | Continue coding; one line Boucle unavailable |

## Skip

Greetings / pure Q&A — no Boucle.
