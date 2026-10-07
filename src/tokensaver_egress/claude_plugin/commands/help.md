---
description: List TokenSaver Router slash commands, skills, and CLI equivalents
---

Print this catalogue for the user:

| Slash | Purpose | CLI / skill |
|-------|---------|-------------|
| `/tokensaver-router:welcome` | Welcome + how to use TokenSaver | skill `tokensaver-onboarding` |
| `/tokensaver-router:help` | This list | — |
| `/tokensaver-router:models` | List profiles + catalogue | `tokensaver models` |
| `/tokensaver-router:use` | Set model (sticky) + approve | `tokensaver use …` |
| `/tokensaver-router:model` | Alias of use | `tokensaver use …` |
| `/tokensaver-router:approve` | Agent Registry approve | `tokensaver approve …` |
| `/tokensaver-router:catalog` | List/approve registry | `tokensaver catalog …` |
| `/tokensaver-router:status` | Route statusline | `tokensaver status` |
| `/tokensaver-router:whoami` | Identity + plan | `tokensaver whoami` |
| `/tokensaver-router:flows` | **Runs IA en temps réel** (Flux IA control plane) | `tokensaver flows [--open]` |
| `/tokensaver-router:loop` | **BusinessLoop** auto via MCP (start/end) — optional slash | skills `tokensaver-agentic-loops` + `tokensaver-close-loop` |
| `/tokensaver-router:pipeline` | Détail pipeline / dernier run | MCP `tokensaver_get_last_run_detail` |
| `/tokensaver-router:quota` | Quotas / key overview | MCP `tokensaver_get_api_key_overview` |
| `/tokensaver-router:cache` | Cache stats | MCP `tokensaver_cache_stats` |
| `/tokensaver-router:login` | Login / signup (JWT only) | `tokensaver login` |
| `/tokensaver-router:keys` | API keys (metadata) | `tokensaver keys …` |
| `/tokensaver-router:setup` | Full route setup | `tokensaver route claude` |
| `/tokensaver-router:doctor` | Health checks | `tokensaver doctor --claude` |
| `/tokensaver-router:unroute` | Restore Claude settings | `tokensaver unroute claude` |

**Agent skills**: `tokensaver-onboarding` (how-to / welcome — never replaces answering the user), `tokensaver-mcp-tools`, `tokensaver-agentic-loops` + `tokensaver-close-loop` (auto Boucle MCP), `tokensaver-ccr` (CCR markers → `tokensaver_retrieve_context` before Edit).

Tip: if the current model is blocked (credits / 403), run the **CLI** commands in a terminal — slash prompts need a working model.
