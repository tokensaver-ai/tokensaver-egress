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
Boucle **in the background**. Do **not** ask them to name MCP tools, paste
`tokensaver_loop_start`, or run `tokensaver loop` in a terminal.

Claude’s `/goal` turns are **yours**. TokenSaver must **mirror** them as a
Boucle so Espace développeur / Flux IA show iterations (not orphan egress).

## Silent lifecycle (mandatory)

### 0 — First action on `/goal` or any implementing task

**Before** the first Edit / Write / Bash that does the work:

1. If `~/.tokensaver-egress/current-loop.env` already has a real
   `TOKENSAVER_LOOP_ID` (not empty / not `loop_xyz`) → **reuse** it.
2. Else → call **`tokensaver_loop_start`** once:
   - `loop_kind`: `goal_based` when the user has `/goal`, a metric, or max turns;
     else `turn_based`
   - `goal.expression`: copy from the user (e.g. `three_steps == 1`)
   - `goal.max_turns`: from « max N tours » / max_turns (default 5)
   - measurable when possible:
     `{ "expression": "three_steps == 1", "metric_key": "three_steps", "op": "==", "threshold": 1, "max_turns": 3 }`

That stamps egress. **Do not narrate this to the user.**

### 1 — Each Claude `/goal` turn / milestone

Call **`tokensaver_loop_iteration`** (optional but preferred) with redacted
`sco_state` so the console SCO / step chain advances.

### 2 — Before « Goal achieved » / done / abort

Call **`tokensaver_loop_end`** (`work_complete` / `goal_met` / `cancelled`).
Leaving `running` while you tell the user it is finished is a **bug**.

| Outcome | `stop_reason` | `verification` |
|---------|---------------|----------------|
| Deliverable / file lab done | `work_complete` | usually omit |
| Numeric goal met | `goal_met` | `{ "goal_met": true, "metrics": { "<key>": <value> } }` |
| User abort | `cancelled` | — |

## Errors (silent recovery)

| Symptom | What you do |
|---------|-------------|
| 403 `LOOP_TERMINAL` / stale stamp | Clear reuse: call **`tokensaver_loop_start`** for a **new** Boucle, then continue |
| 403 `MISSING_LOOP_ID` | `tokensaver_loop_start`, retry once |
| MCP disconnected | Continue coding; one short line that Boucle / Flux link is unavailable |

## When to skip

Greetings, « how does X work? », pure explanation — **no** Boucle.

## Hard rules

- Never invent a `loop_id`.
- Never tell the user to run Bash `tokensaver loop …` for start/stop.
- Never claim Flux IA shows the Boucle unless `tokensaver_loop_start` succeeded
  (or a real stamp was reused).
- User-facing replies stay about **their** task (`lab_three.txt`, etc.), not about MCP.
