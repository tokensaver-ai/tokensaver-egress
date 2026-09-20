---
description: List or approve Agent Registry (catalog) models — fix zero-trust 403
argument-hint: "[pending|approve <ref>]"
---

Agent Registry governs which models may egress (zero-trust).

If `$ARGUMENTS` starts with `approve`, run:

```bash
tokensaver catalog approve <ref>
# or: tokensaver approve <ref>
```

If `$ARGUMENTS` is `pending` or empty, list pending models:

```bash
tokensaver catalog pending
tokensaver catalog list
```

MCP alternatives:
- `tokensaver_list_catalog_assets` with `status=quarantined` or `type=model`
- `tokensaver_approve_catalog_asset` with `ref=…` (creates+approves if missing)

After approve, tell the user to retry their message. Related: `/tokensaver-router:use` and `/tokensaver-router:approve`.
