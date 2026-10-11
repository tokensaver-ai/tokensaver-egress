---
description: Show TokenSaver quotas and usage (tokens, requests, cost)
---

# Usage / quotas — do this exactly

When the user asks for **usage**, **consommation**, **quotas**, **combien de tokens**, etc.:

## Step 1 (REQUIRED) — CLI only

Run **exactly** this Bash command (nothing else first):

```bash
tokensaver quota
```

Paste the stdout **verbatim**. Done.

Optional detail:

```bash
tokensaver quota --full
```

## Hard rules

- **Do not** invent tool names (`TokenSaver_get_usage`, `get_usage`, etc. do **not** exist).
- **Do not** call MCP for this question unless `tokensaver quota` fails in the shell.
- **Do not** chain several quota/overview/consumption tools.
- If MCP is needed as fallback, use the real tool name on server **`tokensaver-route-tools`**:
  - `tokensaver_get_usage` (exact lowercase spelling)
  - or `tokensaver_get_quota_usage`
- Never invent PascalCase / mixed names.
