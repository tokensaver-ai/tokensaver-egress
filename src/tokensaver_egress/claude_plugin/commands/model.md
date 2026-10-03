---
description: Switch TokenSaver routing model or profile (alias of use)
argument-hint: "<cheap|default|strong|provider/model>"
---

Alias of `/tokensaver-router:use`. Prefer that command.

If `$ARGUMENTS` is set, run:

```bash
tokensaver use $ARGUMENTS
tokensaver status --line
```

If empty, list options first:

```bash
tokensaver models
```

Then ask which profile or catalogue id to apply via `tokensaver use …`.
After switch, approve is handled by `use`; fallback: `tokensaver approve --current`.
