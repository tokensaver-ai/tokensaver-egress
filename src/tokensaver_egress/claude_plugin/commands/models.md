---
description: List TokenSaver profiles and catalogue models available on your plan
---

Run (shell — works even if the chat model is blocked):

```bash
tokensaver models
```

Also show sticky Claude model via:

```bash
tokensaver status --line
```

Optional MCP (richer JSON): call `tokensaver_list_models` and paste the ids.

Summarize for the user:
1. Profiles (`cheap` / `default` / `strong`) for their plan
2. Catalogue ids they can pass to `/tokensaver-router:use`
3. Current sticky model if any

Next step hint: `/tokensaver-router:use <profile|provider/model>`
