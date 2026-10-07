---
description: Show TokenSaver identity, plan, session auth, and Flux IA link
---

Run:

```bash
tokensaver whoami
# local stack:
tokensaver whoami --local
```

Report email, plan (Free / Pro / Enterprise), API host, **session JWT auth** (not a stored key prefix), and Flux IA URL.

Explain that Free plan profiles default to allowlisted models (e.g. GPT-OSS 20B on `default`) while paid defaults to Sonnet/Opus unless a sticky `--model` was set.

Offer `/tokensaver-router:models` or `/tokensaver-router:quota` as follow-ups.
