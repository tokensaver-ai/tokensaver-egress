---
name: tokensaver-agentic-loops
description: >-
  ALWAYS use when behind TokenSaver egress OR for any coding / agentic task with
  a deliverable (edit, fix, test, ship, /goal, lab smoke, iterative work,
  « jusqu'à … », max turns, multi-tool implementation). Automatically opens,
  iterates, and closes a TokenSaver BusinessLoop via MCP so egress stamps
  loop_id on captures and Boucles appear in Flux IA — never ask the user to run
  tokensaver loop start/end. Also use on 403 MISSING_LOOP_ID / loop policy
  denials, and when finishing so the Boucle does not stay running. Skip only for
  pure chat / exploratory Q&A with no file or tool side-effects.
---

# TokenSaver — auto BusinessLoop (ACP-9) + egress

Claude’s agentic / verification loop (turns, tools, skills) is **yours**.
TokenSaver **mirrors** it as a governed Boucle (budget, Isoler/Annuler, audit).
Behind **tokensaver-egress**, the Boucle is what links Anthropic / provider
traffic to **Flux IA → Boucles** (SCO diagram, step chain, scrubber).

**You** start / iterate / stop the Boucle with MCP — the user must **not** run
`tokensaver loop start` / `end` in a terminal.

## When a Boucle is necessary (decision)

| Situation | Action |
|-----------|--------|
| Behind egress (`HTTPS_PROXY` → tokensaver, or user ran `tokensaver-egress claude`) **and** you will Edit / Write / Bash / multi-step tools | **Start** before first implementing call |
| Clear done condition (`/goal`, tests green, lab file, « jusqu'à … », max turns) | **Start** (`goal_based` or measurable goal) |
| Multi-turn implementation (create / fix / refactor / ship) even without a numeric metric | **Start** (`turn_based` or descriptive `goal`) |
| Org policy may require `X-Tokensaver-Loop-Id` (`on_missing_loop_id=deny`) | **Start** — otherwise next egress LLM call → **403 `MISSING_LOOP_ID`** |
| Tool / model error mentions `MISSING_LOOP_ID`, `LOOP_POLICY_DENIED`, or « loop id required » | **Start** (or resume existing), then **retry** the failed step |
| Session already has `TOKENSAVER_LOOP_ID` / `~/.tokensaver-egress/current-loop.env` from `claude --loop` | **Reuse** — do **not** call `tokensaver_loop_start` again; remember that id |
| Greeting, « how does X work? », pure explanation, no file/code/tool side-effect | **Skip** — no Boucle |

**Rule of thumb:** if the next tool call can change the repo or trigger an LLM
egress capture that should show under a Boucle, open (or reuse) a loop first.

## Egress binding (why MCP start matters)

1. `tokensaver_loop_start` creates the Boucle on the control plane **and** stamps
   `~/.tokensaver-egress/current-loop.env` (`TOKENSAVER_LOOP_ID=…`).
2. The egress MITM / proxy reads that file (or the process env) and attaches
   `X-Tokensaver-Loop-Id` (+ iteration) on outbound provider requests.
3. Ingested hubs then appear under **Espace développeur → Boucles** / Flux IA
   with iterations, step chain, and scrubber — not as orphan egress noise.
4. `tokensaver_loop_end` clears the stamp so later captures stop attaching.

You do **not** `export TOKENSAVER_LOOP_ID` yourself. Do **not** ask the user to
re-run `tokensaver-egress claude --loop` mid-task if MCP start succeeded.

If start fails (MCP disconnected / plan): continue ungoverened; one-line note
that Boucle / egress link is unavailable. Prefer fixing MCP
(`tokensaver doctor --claude`) over inventing a fake `loop_id`.

## Automatic lifecycle (mandatory)

### 1 — Before first implementing tool call

If there is **no** active `loop_id` in this conversation (and no reusable stamp
from `claude --loop`), call **`tokensaver_loop_start`**:

| Field | Value |
|-------|--------|
| `loop_kind` | `goal_based` when there is a success criterion; else `turn_based` |
| `goal` | Prefer a **measurable** goal when possible (see below). Always include `max_turns` (default 5; use the user’s number if given). |
| `agent_id` | Optional: `claude-code` / stable agent label for FinOps |

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

### 2 — During work

- After a meaningful milestone (files written, tests green, major subgoal):
  **`tokensaver_loop_iteration`** with redacted `sco_state` / links when useful
  (keeps Boucles / SCO diagram progressing; egress iteration header advances).
- Before a heavy LLM / egress turn when policy is strict or
  `TOKENSAVER_LOOP_PRECHECK` is on: **`tokensaver_loop_precheck`** — honour
  `deny` / `require_approval` / throttle; do not burn another provider call.
- If egress or API returns **403** with `MISSING_LOOP_ID`: call
  `tokensaver_loop_start` (if none), confirm stamp, retry once.
- If **403** `LOOP_POLICY_DENIED` / awaiting approval: stop iterating, tell the
  user (Isoler / approve in console); do not invent a workaround loop id.

### 3 — When the task is done (or you report failure / give up)

**Before** your final user-facing “done” message, call **`tokensaver_loop_end`**
(skill **`tokensaver-close-loop`**). That clears the egress stamp.

| Situation | `stop_reason` | `verification` |
|-----------|---------------|----------------|
| File / PR / lab deliverable done | `work_complete` | omit `goal_met`, or `{ "goal_met": true }` if goal was descriptive |
| Numeric goal met | `goal_met` | `{ "goal_met": true, "metrics": { "<metric_key>": <value> } }` |
| Stop-signal goal | `found` | `{ "stop_signal": "found" }` |
| User abort | `cancelled` | — |
| Hit turn limit | `max_turns` | — |

**Do not** send `goal_met: true` without either (a) matching `metrics` for
`metric_key`, or (b) a descriptive / stop_signal goal. Inventing a fake score
is worse than using `work_complete`.

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

- Do **not** invent MCP tool names or fabricate a `loop_id`.
- Do **not** claim a Boucle exists without a successful `tokensaver_loop_start`
  (or a verified existing stamp from `claude --loop` / `tokensaver_loop_get`).
- Do **not** end the turn with “done” while a Boucle you started is still open.
- Do **not** skip start when behind egress and about to implement — orphan
  captures break Boucles attribution in the console.
- If `tokensaver_loop_start` fails (plan / auth / MCP down): continue the coding
  task ungoverened; say Boucle unavailable in one line.
