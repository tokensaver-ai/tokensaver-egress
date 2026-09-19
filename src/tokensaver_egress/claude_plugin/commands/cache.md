---
description: Show TokenSaver cache stats (and optional purge guidance)
argument-hint: "[stats|purge]"
---

Use MCP tools:

1. Default / `stats`: call `tokensaver_cache_stats` and paste the result.
2. Optional detail: `tokensaver_list_cache_entries` (small limit).
3. Only if the user explicitly asks to purge: confirm, then `tokensaver_cache_purge`.

Never purge without clear user consent.

CLI fallback:

```bash
tokensaver status
tokensaver flows
```
