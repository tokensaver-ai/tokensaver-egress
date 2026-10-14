---
description: Configure Claude Code to route through TokenSaver (login, settings, MCP, model profile)
---

If TokenSaver is not configured yet, start with CLI login (no dashboard required):

```bash
tokensaver login
# or: tokensaver login --email you@acme.com --signup -y
```

This creates a Free account (or signs in) and saves a **session JWT** only — the backend maps it to your API key (same as the console). No `ts_…` secret on disk.

Then route Claude Code:

```bash
tokensaver route claude --scope project --profile default --with-fs
```

Useful variants:

- Cheap testing model: `tokensaver route claude --profile cheap`
- Explicit model: `tokensaver route claude --model openrouter/openai/gpt-oss-20b`
- Local self-host: `tokensaver route claude --local --with-fs`

Verify:

```bash
tokensaver whoami
tokensaver doctor --claude
tokensaver status
```

In Claude Code: `/tokensaver-router:welcome` for a guided tour.

Open governed runs in Flux IA (printed by `tokensaver status` / `whoami`).
