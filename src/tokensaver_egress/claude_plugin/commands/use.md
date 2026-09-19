---
description: Set Claude routing model or profile (sticky) and approve in Agent Registry
argument-hint: "<cheap|default|strong|provider/model>"
---

Select a TokenSaver model for Claude Code. Prefer the CLI (updates sticky model + Agent Registry).

If `$ARGUMENTS` is empty, run `tokensaver models` and ask the user which id/profile to use.

Otherwise run:

```bash
# Prefer --local when status shows local
tokensaver use $ARGUMENTS
# equivalent:
#   tokensaver use cheap
#   tokensaver use openrouter/z-ai/glm-4.7-flash
```

Then verify:

```bash
tokensaver status --line
```

Tell the user to send a **new message** (or restart Claude) so the model takes effect. Statusline must show the chosen model.

If approve fails (403), run `/tokensaver-router:approve $ARGUMENTS`.
