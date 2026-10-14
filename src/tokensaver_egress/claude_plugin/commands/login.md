---
description: Login or signup — session JWT only (backend maps to API key)
argument-hint: "[--local]"
---

Run in the user's terminal (shell):

```bash
tokensaver login
# signup: tokensaver login --email you@acme.com --signup -y
# local stack: tokensaver login --local
```

Explain:
- **No API key on disk** — JWT session only (like the web console)
- After login: `tokensaver route claude` or `/tokensaver-router:setup`
- Email verification required before API/Claude (403 otherwise) — `tokensaver resend-verification` / `tokensaver verify-email`

Do **not** ask the user to paste a `ts_…` key unless they use CI (`TOKENSAVER_API_KEY` env).
