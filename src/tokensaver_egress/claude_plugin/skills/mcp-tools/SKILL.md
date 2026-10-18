---
name: tokensaver-mcp-tools
description: >-
  TokenSaver MCP tools reference for Claude Code — when to call tokensaver_* tools,
  quotas, runs, cache, RAG, governance, and Agent Registry. Use when the user asks
  for MCP tools, quotas, last run, cache, or platform data.
---

# TokenSaver MCP tools

MCP server: **`tokensaver-route-tools`** (HTTP). Auth: same Bearer as routing (session JWT or `TOKENSAVER_API_KEY`).

## Response rules

1. **ASCII dashboards** from quota/overview tools → paste **verbatim**, do not summarize into a table unless asked.
2. **Last run / run detail** → `tokensaver_get_last_run_detail` or `tokensaver_get_monitoring_run` once, paste ASCII.
3. **Model pick** → `tokensaver_list_models` before suggesting ids.
4. Respect **effective modules** on the key (cache/RAG/PII may be off via governance).

## Tool groups

### Chat & models

| Tool | Use when |
|------|----------|
| `tokensaver_chat` | Governed LLM Q&A (pipeline inside) |
| `tokensaver_list_models` | Available models for this key/plan |
| `tokensaver_create_chat` / `tokensaver_chat_in_session` | Multi-turn sessions |

### Quotas & cost

**For « usage / quotas / consommation »:** run Bash `tokensaver quota` (paste verbatim).  
**Do not invent** tool names like `TokenSaver_get_usage` — that name does **not** exist and causes a failed round-trip.

MCP fallback only if the CLI is unavailable (exact lowercase names on server `tokensaver-route-tools`):

| Tool | Use when |
|------|----------|
| `tokensaver_get_usage` | Simple « usage » / consommation (1 call) |
| `tokensaver_get_quota_usage` | Quotas + limits ASCII |
| `tokensaver_get_api_key_overview` | Full key dashboard only |
| `tokensaver_get_consumption` | Explicit today/month cost breakdown |
| `tokensaver_get_cost_breakdown` | Cost by model |
| `tokensaver_check_quota_alerts` | Near limits |

**Never** call several of these for the same user question.

### Runs & observability (real-time control plane)

**For « derniers runs / liste les runs / Flux IA »:** run Bash `tokensaver flows` (paste verbatim).  
**Do not invent** tool names like `TokenSaverRouter…ListRuns`, `mcp__plugin_…__list_runs`, or `TokenSaver_list_runs` — they do **not** exist.

MCP fallback only if the CLI fails (exact lowercase names on server `tokensaver-route-tools`):

| Tool | Use when |
|------|----------|
| `tokensaver_list_runs` | Recent runs list (limit 5) |
| `tokensaver_get_last_run_detail` | « Détail du dernier run » (paste ASCII) |
| `tokensaver_get_monitoring_run` | Step-by-step run (need request_id) |
| `tokensaver_open_run_console` | Open run in browser (real-time Flux IA) |
| `tokensaver_get_execution_trace` | Trace by execution_trace_id |

CLI: `tokensaver flows` lists runs; `tokensaver flows --open` opens Flux IA.

### Business loops (ACP-9)

**Automatic (egress-aware):** behind `tokensaver-egress` or for any implementing
task (edit / fix / test / ship / `/goal`), follow skills
**`tokensaver-agentic-loops`** (start + iterate) and **`tokensaver-close-loop`**
(end). MCP start stamps `~/.tokensaver-egress/current-loop.env` so captures get
`loop_id`. Use MCP only — do **not** ask the user to run `tokensaver loop start|end`.

| Tool | Use when |
|------|----------|
| `tokensaver_loop_start` | Before first implementing tool call; on 403 `MISSING_LOOP_ID` |
| `tokensaver_loop_iteration` | Milestone (keeps Boucles / SCO progressing) |
| `tokensaver_loop_precheck` | Before expensive turn / when `TOKENSAVER_LOOP_PRECHECK` |
| `tokensaver_loop_end` | **Before** saying done / abort (mandatory; clears egress stamp) |
| `tokensaver_loop_get` | Inspect loop / reuse id from `claude --loop` |

Console: **Espace développeur** / graphe **Boucles**. Optional Stop hook
`auto-end-loop.sh` may end a leftover Boucle — skills remain the primary owner.

Do **not** invent a loop from wall-clock gaps alone. Do **not** skip start when
egress is on and you are about to implement (orphan hubs).

### Cache

| Tool | Use when |
|------|----------|
| `tokensaver_cache_stats` | Stats overview |
| `tokensaver_list_cache_entries` | Inspect entries |
| `tokensaver_cache_invalidate` | Wrong cached answer for one prompt |
| `tokensaver_cache_feedback` | Thumbs down + ban |

Do **not** use `tokensaver_cache_purge` to « disable » cache — use governance policies.

### Agent Registry / zero-trust

| Tool | Use when |
|------|----------|
| `tokensaver_list_catalog_assets` | List models/assets |
| `tokensaver_approve_catalog_asset` | Unblock quarantined model (`ref=provider/model`) |
| `tokensaver_set_catalog_asset_status` | Admin status changes |

CLI equivalent: `tokensaver approve`, `tokensaver catalog`.

### Governance & security

| Tool | Use when |
|------|----------|
| `tokensaver_list_governance_policies` | Policies on key |
| `tokensaver_security_summary` | Security overview |
| `tokensaver_list_security_events` | Recent events |

### RAG (if enabled for key)

| Tool | Use when |
|------|----------|
| `tokensaver_search_rag` | Search documents |
| `tokensaver_upload_rag_document` | Ingest document |

If RAG is disabled for the key, use `tokensaver_chat` without assuming RAG context.

### CCR retrieve

When a tool result starts with `[TokenSaver CCR id=…]`, follow skill **`tokensaver-ccr`**:
call `tokensaver_retrieve_context(content_id=…)` before Edit/Write. Do not refuse the tool.

## Fallback when MCP fails

```bash
tokensaver whoami
tokensaver status
tokensaver doctor --claude
```

If chat model returns 403, run **`tokensaver approve --current`** in the user's terminal.
