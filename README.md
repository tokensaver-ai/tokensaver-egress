# tokensaver-egress

**Capture HTTPS traffic from your AI agents** (Claude Code, Cursor, n8n, …) and send audits to the [TokenSaver](https://tokensaver.fr) control plane.

You run a small proxy on **your machine**. Your tools talk to the proxy; the proxy talks to Anthropic / OpenAI / … and reports what happened to TokenSaver (Flux IA).

[![PyPI](https://img.shields.io/pypi/v/tokensaver-egress?style=flat-square&logo=pypi)](https://pypi.org/project/tokensaver-egress/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/tokensaver-egress/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

```
  Your agent  ──HTTPS──►  tokensaver-egress  ──►  api.anthropic.com / …
                                │
                                └── audits ──►  api.tokensaver.fr  →  Flux IA
```

---

## Install

```bash
pip install -U tokensaver-egress
tokensaver-egress setup    # interactive wizard (recommended)
```

Or open the menu with a bare `tokensaver-egress` in a terminal (TTY).

Requires **Python ≥ 3.10** and a TokenSaver API key (`ts_…`) from [platform.tokensaver.fr](https://platform.tokensaver.fr).

---

## Easiest path: guided setup

```bash
tokensaver-egress setup
```

The wizard walks you through:

1. **API key** (`TOKENSAVER_API_KEY`)
2. **Ingest URL** (default: production TokenSaver)
3. **MITM** on/off + create the local CA if needed
4. **Trust instructions** (and optional macOS keychain trust)
5. **Body capture** on/off
6. **Port** (default `8888`)
7. **Claude Code skills** (CCR / onboarding) if missing — no `tokensaver-cli` required
8. Saves `~/.tokensaver-egress/env` + `client-env.sh`, then offers to **start** the proxy

In another terminal:

```bash
source ~/.tokensaver-egress/client-env.sh
claude   # or your agent
```

Next time you can run `tokensaver-egress` → **Start proxy now**, or:

```bash
source ~/.tokensaver-egress/env
tokensaver-egress serve
```

---

## 5-minute setup (manual MITM)

MITM mode can see **model, tokens, and tool names**. You must trust a **local** certificate once.

### 1. Configure TokenSaver

```bash
export TOKENSAVER_API_KEY=ts_your_key_here
export TOKENSAVER_INGEST_URL=https://api.tokensaver.fr/api/v1/egress/ingest
```

### 2. Create the local CA (once)

```bash
tokensaver-egress init-ca
# Writes: ~/.tokensaver-egress/ca/ca.crt
```

**Trust that CA** on this Mac (system keychain), then tell **Node** (Claude Code) to use it:

```bash
# macOS — trust the CA (admin password)
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  "$HOME/.tokensaver-egress/ca/ca.crt"

# Every terminal where you run Claude Code / Node agents
export NODE_EXTRA_CA_CERTS="$HOME/.tokensaver-egress/ca/ca.crt"
```

### 3. Start the proxy

```bash
EGRESS_MITM_ENABLED=true tokensaver-egress serve --port 8888
```

Leave this terminal open. You should see something like `listening on …8888`.

### 4. Point your tools at the proxy

**In another terminal:**

```bash
export HTTPS_PROXY=http://127.0.0.1:8888
export HTTP_PROXY=http://127.0.0.1:8888
export NODE_EXTRA_CA_CERTS="$HOME/.tokensaver-egress/ca/ca.crt"

# Example: Claude Code
claude
```

### 5. Check Flux IA

Open [platform.tokensaver.fr](https://platform.tokensaver.fr) → **Flux IA** / egress flows. New calls should appear within seconds.

---

## Everyday commands

| Command | What it does |
|---------|----------------|
| `tokensaver-egress` | Interactive menu (TTY) or help |
| `tokensaver-egress setup` | Guided setup (API key, CA, Claude skills, options, start) |
| `tokensaver-egress install-skills` | Install Claude Code skills to `~/.claude/skills/tokensaver-router` |
| `tokensaver-egress serve` | Start proxy on `0.0.0.0:8888` (loads `~/.tokensaver-egress/env`) |
| `tokensaver-egress serve --port 8890` | Use another port |
| `tokensaver-egress init-ca` | (Re)generate the MITM CA |
| `Ctrl+C` | Stop `serve` |

If the port is busy:

```text
✖ Cannot listen on 0.0.0.0:8888 (Address already in use).
  Try:  tokensaver-egress serve --port 8889
```

Stop whatever is on 8888 (often another egress), or pick a free port and update `HTTPS_PROXY`.

### Claude Code skills (no CLI needed)

```bash
tokensaver-egress install-skills
# or: tokensaver-egress install-skills --force
```

Installs `~/.claude/skills/tokensaver-router` (same plugin as `tokensaver-cli`): **CCR**, onboarding, MCP helpers. Restart Claude Code after install.

---

## Examples

### A. Claude Code (full capture)

```bash
# Terminal A — proxy
export TOKENSAVER_API_KEY=ts_…
export TOKENSAVER_INGEST_URL=https://api.tokensaver.fr/api/v1/egress/ingest
EGRESS_MITM_ENABLED=true EGRESS_CAPTURE_BODIES=1 \
  tokensaver-egress serve --port 8888

# Terminal B — agent
export HTTPS_PROXY=http://127.0.0.1:8888
export HTTP_PROXY=http://127.0.0.1:8888
export NODE_EXTRA_CA_CERTS="$HOME/.tokensaver-egress/ca/ca.crt"
claude
```

`EGRESS_CAPTURE_BODIES=1` stores request/response bodies in TokenSaver (auth headers are masked). Omit it if you only want metadata (host, latency, tokens).

### B. One-shot curl through the proxy

```bash
# Proxy already running with MITM + CA trusted
export HTTPS_PROXY=http://127.0.0.1:8888
curl -sS https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":32,"messages":[{"role":"user","content":"ping"}]}'
```

### C. Metadata only (no TLS decrypt)

No CA trust needed. You still see host / bytes / latency, not model tokens.

```bash
export TOKENSAVER_API_KEY=ts_…
export TOKENSAVER_INGEST_URL=https://api.tokensaver.fr/api/v1/egress/ingest
tokensaver-egress serve --port 8888

# Client
export HTTPS_PROXY=http://127.0.0.1:8888
export HTTP_PROXY=http://127.0.0.1:8888
```

### D. Local TokenSaver backend (dev)

```bash
export TOKENSAVER_API_KEY=ts_…
export TOKENSAVER_INGEST_URL=http://localhost:8000/api/v1/egress/ingest
EGRESS_MITM_ENABLED=true tokensaver-egress serve --port 8888
```

---

## Modes (simple)

| Mode | How you enable it | What you get |
|------|-------------------|--------------|
| **Explicit** | `HTTPS_PROXY` → proxy | Host, size, latency |
| **MITM** | + `EGRESS_MITM_ENABLED=true` + trusted CA | Model, tokens, tools, optional bodies |
| **Transparent** | Linux TPROXY (advanced) | No proxy env on the client; metadata only |

Most people want **MITM + explicit proxy** (sections above).

Transparent (Linux only):

```bash
sudo EGRESS_PORT=8888 ./scripts/tproxy-setup.sh up
tokensaver-egress serve --transparent --port 8888
```

---

## Useful environment variables

| Variable | Meaning |
|----------|---------|
| `TOKENSAVER_API_KEY` | Your `ts_…` key (required to ship audits) |
| `TOKENSAVER_INGEST_URL` | SaaS default: `https://api.tokensaver.fr/api/v1/egress/ingest` |
| `EGRESS_MITM_ENABLED` | `true` = decrypt known LLM hosts |
| `EGRESS_CAPTURE_BODIES` | `1` = store bodies in Flux IA |
| `EGRESS_ENFORCE_ENABLED` | `1` = block non-approved catalogue assets |
| `TOKENSAVER_NO_BANNER` | `1` = hide the ASCII logo |
| `NODE_EXTRA_CA_CERTS` | Path to `ca.crt` for Node / Claude Code |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `pip install -U` stays on an old version | `pip install -U --no-cache-dir tokensaver-egress` |
| TLS / certificate errors in Claude Code | Trust `ca.crt` **and** set `NODE_EXTRA_CA_CERTS` |
| Nothing in Flux IA | Check `TOKENSAVER_API_KEY` + `TOKENSAVER_INGEST_URL`; watch proxy logs |
| Port already in use | `tokensaver-egress serve --port 8889` (update `HTTPS_PROXY`) |
| Want quieter startup | `TOKENSAVER_NO_BANNER=1 tokensaver-egress serve` |

---

## Security

- Run MITM **only on machines you control**. The CA can decrypt traffic you route through the proxy.
- `HTTPS_PROXY` is easy to unset — it is **not** a hard security boundary. For lock-down, use transparent mode / NetworkPolicy (see runbook).
- Bodies are optional; auth headers are masked unless you opt into raw headers.

---

## Related

| Package | Role |
|---------|------|
| [`tokensaver-cli`](https://pypi.org/project/tokensaver-cli/) | Route Claude Code / Cursor through TokenSaver APIs |
| [`tokensaver-sdk`](https://pypi.org/project/tokensaver-sdk/) | Python SDK |
| [Runbook (platform)](https://github.com/CapIA-Labs-ai/tokensaver-platform/blob/main/docs/RUNBOOK-EGRESS-ACP-4.md) | Full ops guide |

Monorepo helpers (developers):

```bash
./scripts/start-egress.sh --mode mitm --capture-bodies
./scripts/stop-egress.sh
```

## License

MIT © TokenSaver
