---
description: List / create / revoke TokenSaver API keys
argument-hint: "[list|create|revoke <id>]"
---

Requires a login JWT (`tokensaver login`), not key-only import.

```bash
tokensaver keys list
tokensaver keys create --name Claude --use
tokensaver keys revoke <key-id>
```

If `$ARGUMENTS` is `create`, run create with `--use`.
If it starts with `revoke`, revoke that id.
Otherwise list keys.

Warn: the plaintext key is shown once at create time — store it securely.
Related: `/tokensaver-router:whoami`, `/tokensaver-router:login`.
