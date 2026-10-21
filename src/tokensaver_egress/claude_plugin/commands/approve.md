---
description: Approve a model in TokenSaver Agent Registry (unblocks zero-trust 403)
argument-hint: "[provider/model]"
---

Zero-trust blocks LLM calls until the model is **approved** in the Agent Registry.
Approving does **not** need a working chat model — use the CLI (shell).

Prefer a shell (works even when Claude is blocked by 403):

```bash
tokensaver approve $ARGUMENTS
```

If `$ARGUMENTS` is empty, approve the currently routed model:

```bash
tokensaver approve --current
```

When you `route claude --launch` / `use` from a TTY, TokenSaver already prompts
`Approve this model now? [Y/n]` with a short zero-trust explanation.

Also available via MCP (when tools MCP is connected):

- `tokensaver_approve_catalog_asset` with `ref` = the blocked model id

After success, tell the user to retry their message.
