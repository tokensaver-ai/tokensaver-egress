---
description: Run TokenSaver doctor checks for Claude routing
---

```bash
tokensaver doctor --claude
# local stack:
tokensaver doctor --claude --local
```

Report which checks failed (API key, Anthropic base URL, /v1/models, MCP). Suggest `tokensaver login`, `tokensaver route claude --local`, or `tokensaver approve --current` as appropriate.
