---
description: Pipeline TokenSaver — voir le détail d'un run IA en temps réel (Flux IA)
---

Each Claude message creates a **governed pipeline run** visible **in real time** on the TokenSaver control plane.

Run:

```bash
tokensaver status          # Flux IA URL + latest flowId when available
tokensaver flows --open    # open console live
```

For a **list** of recent runs: Bash only — `tokensaver flows` (do not invent MCP tool names).

MCP (only for step-by-step detail, exact lowercase on `tokensaver-route-tools`):
- `tokensaver_get_last_run_detail` — paste ASCII dashboard
- `tokensaver_get_monitoring_run` — full step trace (needs request_id)
- `tokensaver_open_run_console` — browser deep link

Prefer the **Latest flow** URL when printed (`?tab=flows&flowId=…`). Explain pipeline modules the user may see: cache, compression, PII, LLM, MCP tool runs.
