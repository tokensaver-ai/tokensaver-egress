---
description: Message d'accueil TokenSaver — guide des actions (console, models, Flux IA, quotas)
---

Print the welcome template from skill **tokensaver-onboarding** as plain text **immediately**.

**Latency rules (strict):**
- Do **not** run any shell/Bash (`tokensaver whoami`, `status`, `flows --open`, etc.)
- Do **not** call MCP tools
- Do **not** load extra skills beyond reading the welcome template
- One short reply only

Include: invite to open the web console (same account as `tokensaver login`) —
`https://platform.tokensaver.fr` or `tokensaver flows --open` (mention the command; do not execute it).

Then list slash actions: models, flows, quota, whoami, help.

End by asking what the user wants to do next.
