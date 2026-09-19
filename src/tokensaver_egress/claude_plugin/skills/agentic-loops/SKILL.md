---
name: tokensaver-agentic-loops
description: >-
  ALWAYS use for coding tasks with a clear done condition (create/fix/test/ship,
  /goal, lab smoke, iterative work, « jusqu'à … », max turns). Automatically opens
  and closes a TokenSaver BusinessLoop via MCP only — never ask the user to run
  tokensaver loop start/end. Also use when finishing a governed task so the Boucle
  does not stay running. Skip only for pure chat / exploratory Q&A with no deliverable.
---

# TokenSaver — auto BusinessLoop (ACP-9)

Claude’s agentic / verification loop (turns, tools, skills) is **yours**.
TokenSaver **mirrors** it as a governed Boucle for the org (budget, Isoler/Annuler, audit).

**You** start and stop the Boucle with MCP — the user must **not** run
`tokensaver loop start` / `end` in a terminal.

## Automatic lifecycle (mandatory)

### 1 — Before first tool write / edit / bash that implements the task

If there is **no** active `loop_id` in this conversation yet, call **`tokensaver_loop_start`**:

| Field | Value |
|-------|--------|
| `loop_kind` | `goal_based` when there is a success criterion; else `turn_based` |
| `goal` | Prefer a **measurable** goal when possible (see below). Always include `max_turns` (default 5). |

**Goal shapes (pick one):**

1. **Numeric / boolean metric** (best for dual evaluator):
   ```json
   { "expression": "lab_smoke == 1", "metric_key": "lab_smoke", "op": "==", "threshold": 1, "max_turns": 5 }
   ```
2. **Stop signal** (deliverable labs without inventing fake scores):
   ```json
   { "stop_signal": "found", "expression": "lab_smoke.txt contains boucle ok", "max_turns": 5 }
   ```
3. **Descriptive only** (free text): `{ "expression": "<done condition>", "max_turns": 5 }` — on end use `work_complete` **or** `goal_met` + `verification.goal_met: true` (platform accepts descriptive claims).

Remember `loop_id` from the tool result for the rest of the session.
The MCP tool also stamps `~/.tokensaver-egress/current-loop.env` so egress
attaches traffic — you do **not** export `TOKENSAVER_LOOP_ID` yourself.

Do **not** start a loop for: greetings, « how does X work? », pure explanation, no file/code change.

### 2 — During work

- After a meaningful milestone (files written, tests green): optional **`tokensaver_loop_iteration`**
- Before a heavy LLM/egress turn if policy is strict: **`tokensaver_loop_precheck`** — honour deny / require_approval

### 3 — When the task is done (or you report failure / give up)

**Before** your final user-facing “done” message, call **`tokensaver_loop_end`**:

| Situation | `stop_reason` | `verification` |
|-----------|---------------|----------------|
| File / PR / lab deliverable done | `work_complete` | omit `goal_met`, or `{ "goal_met": true }` if goal was descriptive |
| Numeric goal met | `goal_met` | `{ "goal_met": true, "metrics": { "<metric_key>": <value> } }` |
| Stop-signal goal | `found` | `{ "stop_signal": "found" }` |
| User abort | `cancelled` | — |
| Hit turn limit | `max_turns` | — |

**Do not** send `goal_met: true` without either (a) matching `metrics` for `metric_key`, or (b) a descriptive / stop_signal goal. Inventing a fake score is worse than using `work_complete`.

Leaving the Boucle `running` after you finished is a **bug** — always end.

## MCP only

Server: TokenSaver MCP (`tokensaver_*` tools). Exact names:

- `tokensaver_loop_start`
- `tokensaver_loop_iteration`
- `tokensaver_loop_precheck`
- `tokensaver_loop_end`
- `tokensaver_loop_get`

**Never** tell the user to run Bash `tokensaver loop …` for start/stop.
(Bash is only an emergency fallback if MCP tools are missing — then say so briefly.)

## Mapping Claude loop engineering → Boucle kind

| Claude (Anthropic) | `loop_kind` |
|--------------------|-------------|
| Turn-based + verify skills | `turn_based` |
| `/goal` / clear done condition | `goal_based` |
| Recurring `/loop` `/schedule` | `time_based` |
| Long-running routine | `proactive` |

## Hard rules

- Do **not** invent MCP tool names.
- Do **not** claim a Boucle exists without a successful `tokensaver_loop_start`.
- Do **not** end the turn with “done” while a Boucle you started is still open.
- If `tokensaver_loop_start` fails (plan / auth): continue the coding task ungoverened; say Boucle unavailable in one line.
