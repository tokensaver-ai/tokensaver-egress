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
| `goal` | `{ "expression": "<one-line done condition>", "max_turns": N }` — derive from the user prompt (e.g. « lab_smoke.txt avec boucle ok », « tests green ») |
| `max_turns` | 5 default; use user’s number if they said max turns / tries |

Remember `loop_id` from the tool result for the rest of the session.
The MCP tool also stamps `~/.tokensaver-egress/current-loop.env` so egress
attaches traffic — you do **not** export `TOKENSAVER_LOOP_ID` yourself.

Do **not** start a loop for: greetings, « how does X work? », pure explanation, no file/code change.

### 2 — During work

- After a meaningful milestone (files written, tests green): optional **`tokensaver_loop_iteration`**
- Before a heavy LLM/egress turn if policy is strict: **`tokensaver_loop_precheck`** — honour deny / require_approval

### 3 — When the task is done (or you report failure / give up)

**Before** your final user-facing “done” message, call **`tokensaver_loop_end`**:

| Field | Value |
|-------|--------|
| `loop_id` | the id from start |
| `stop_reason` | `work_complete` \| `goal_met` \| `cancelled` \| `max_turns` |
| `summary` | one short sentence of outcome |
| `verification` | optional `{ "goal_met": true/false, "metrics": { … } }` |

Leaving the Boucle `running` after you finished is a **bug** — always end.

If the user aborts mid-task: `stop_reason=cancelled`.

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
