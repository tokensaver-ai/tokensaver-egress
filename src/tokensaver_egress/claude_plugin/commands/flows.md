---
description: Flux IA — lister les derniers runs IA / ouvrir la console (control plane)
argument-hint: "[--open]"
---

# Derniers runs / Flux IA — do this exactly

When the user asks for **derniers runs**, **liste les runs**, **runs IA**, **Flux IA**, **flows**, etc.:

## Step 1 (REQUIRED) — CLI only

Run **exactly** this Bash command (nothing else first):

```bash
tokensaver flows
```

Paste the stdout **verbatim**. Done.

To open the console:

```bash
tokensaver flows --open
```

## Hard rules

- **Do not** invent tool names. These do **not** exist and waste a round-trip:
  - `TokenSaverRouterTokensaverRouteToolsListRuns`
  - `mcp__plugin_tokensaver-router_tokensaver-route-tools__list_runs`
  - `TokenSaver_list_runs`, `ListRuns`, PascalCase / mangled MCP ids
- **Do not** call MCP for a simple list unless `tokensaver flows` fails in the shell.
- If MCP is needed as fallback, use the **exact** lowercase name on server **`tokensaver-route-tools`**:
  - `tokensaver_list_runs` (limit 5) — liste
  - `tokensaver_get_last_run_detail` — détail du dernier run (coller l'ASCII)
  - `tokensaver_open_run_console` — deep link navigateur
- Never invent PascalCase / mixed / plugin-prefixed names.

## Tell the user (brief)

1. **Temps réel** — chaque message Claude est tracé dans Flux IA (même compte que `tokensaver whoami`)
2. Garder [platform.tokensaver.fr](https://platform.tokensaver.fr) ouvert pendant la session pour voir les runs live
