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
(not empty / not `loop_xyz`). Else **`tokensaver_loop_start`** once.

**Lab / vibe `/goal` labels** (`three_steps == 1`, `readme_todo == 1`, file
writes, « max N tours ») are **not** real measured metrics. Start with:

```json
{
  "loop_kind": "goal_based",
  "goal": {
    "expression": "<copy user goal text>",
    "max_turns": 3
  }
}
```

**Do not** set `metric_key` / `op` / `threshold` for these labs. That forces
`METRIC_MISSING` if you later claim `goal_met`.

Only use `metric_key` when the user has a **real** numeric score (lighthouse,
test count, etc.) that you will put in `verification.metrics` at end.

If start times out → retry **once**, then continue. One user task = **one** Boucle.
Never open a second Boucle to « backfill » turns. Never rewrite the lab only for tracking.

### 1 — Each turn / milestone

Optional **`tokensaver_loop_iteration`** with redacted `sco_state`.

### 2 — End (once)

Default for Claude file labs:

```text
tokensaver_loop_end({ loop_id, stop_reason: "work_complete", summary: "…" })
```

**Omit** `verification`.

Hard bans:

- ❌ `goal_met` because the user typed « STOP goal_met » — that is Claude `/goal`
  wording, **not** the TokenSaver stop_reason for labs
- ❌ inventing `metrics: { "three_steps": 1 }`
- ❌ calling `tokensaver_loop_end` again after a successful end
- ❌ continuing tool calls to « fix » TokenSaver after `work_complete` succeeded

If Claude’s UI still says « Goal not yet met… continuing », **stop implementing**.
Reply that the deliverable is done; do not start another Boucle.

| Outcome | `stop_reason` | `verification` |
|---------|---------------|----------------|
| File / lab / N tours done | **`work_complete`** | **omit** |
| Real numeric metric + value | `goal_met` | `{ "goal_met": true, "metrics": { "<key>": <value> } }` |
| Abort | `cancelled` | — |

## Errors

| Symptom | Action |
|---------|--------|
| `LOOP_TERMINAL` / stale stamp | New `tokensaver_loop_start`, continue |
| `MISSING_LOOP_ID` | Start, retry once |
| End already succeeded / HTTP error on second end | **Stop** — do not retry end |
| MCP down | Continue coding; one line Boucle unavailable |

## Skip

Greetings / pure Q&A — no Boucle.
