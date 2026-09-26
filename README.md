# tokensaver-egress

**Capture HTTPS traffic from your AI agents** (Claude Code, Cursor, n8n, …) and send audits to [TokenSaver](https://tokensaver.fr) → **Flux IA**.

[![PyPI](https://img.shields.io/pypi/v/tokensaver-egress?style=flat-square&logo=pypi)](https://pypi.org/project/tokensaver-egress/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/tokensaver-egress/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

```
  Your agent  ──HTTPS──►  tokensaver-egress  ──►  api.anthropic.com / …
                                │
                                └── audits ──►  api.tokensaver.fr  →  Flux IA
```

---

## Quick start (2 terminals)

**Once** — install and configure (any terminal):

```bash
pip install -U tokensaver-egress
tokensaver-egress setup
```

**Every day** — leave the proxy running, use Claude in another shell:

```bash
# Terminal 1 — egress (logs stay visible here)
tokensaver-egress serve

# Terminal 2 — Claude through that proxy
tokensaver-egress claude --no-start
```

That’s it. Open [platform.tokensaver.fr](https://platform.tokensaver.fr) → **Flux IA** to see traffic.

| Stop | How |
|------|-----|
| Proxy | `Ctrl+C` in terminal 1 |
| Shell still pointing at `:8888` | `eval "$(tokensaver-egress unproxy --sh)"` in terminal 2 |

Requires **Python ≥ 3.10**. The wizard asks for a TokenSaver account or a `ts_…` API key.

---

## What `setup` configures

Interactive wizard (`tokensaver-egress setup`):

| Step | Choices |
|------|---------|
| **API key** | Create account · **Log in** (email/password) · Paste `ts_…` · Open console |
| Platform | SaaS (`api.tokensaver.fr`) or Local (`localhost:8000`) |
| Then | Ingest URL · MITM CA · body capture · port · Claude skills |

Config is written to `~/.tokensaver-egress/env` (+ `client-env.sh`).

**Log in (existing account):** choose option **2** → SaaS/Local → email + password → a key labeled **Egress CLI** is created and saved. Prefer **Paste** if you already have a key. SSO-only orgs: paste after IdP login. Details: [Account options](#account-options-api-key).

---

## Other ways to run

| Goal | Commands |
|------|----------|
| **Recommended** — logs + Claude | `serve` then `claude --no-start` (two terminals above) |
| One command (proxy auto-starts, no log window) | `tokensaver-egress claude` |
| Any command through the proxy | `tokensaver-egress run -- curl -I https://api.anthropic.com` |
| Leave auto-proxy up after Claude exits | `claude --keep-proxy` → later `tokensaver-egress stop` |

```bash
tokensaver-egress stop
eval "$(tokensaver-egress unproxy --sh)"   # if HTTPS_PROXY was left in this shell
```

---

## Account options (API key)

| # | Option | Who | What happens |
|---|--------|-----|----------------|
| **1** | **Create a free account** | New users | Signup + key returned once |
| **2** | **Log in** (default) | Existing email/password | Sign-in → create key **Egress CLI** |
| **3** | **Paste** an existing key | CI / SSO / reuse a key | You paste `ts_…` from Settings → API keys |
| **4** | **Open** the console | Browser | https://platform.tokensaver.fr |

```text
tokensaver-egress setup
→ Keep this key? n          # when replacing an old key
→ 2) Log in …
→ 1) TokenSaver SaaS
→ email + password
→ key saved in ~/.tokensaver-egress/env
```

Notes:

- Login **creates a new** API key each time (plan key quota). Prefer **paste** to reuse a key.
- Email must be **verified** to create keys via login.
- **SSO-only** orgs: use **paste** after IdP login — not email/password.
- `TOKENSAVER_ENTERPRISE=1` / `TOKENSAVER_ORG_HINT=<slug>` hide **create account**.

---

## Architecture (client only)

`tokensaver-egress` is a **lightweight client-side HTTPS proxy**. It does **not** embed TokenSaver business logic, databases, or admin console.

| Runs locally (this package) | Runs on TokenSaver SaaS ([platform.tokensaver.fr](https://platform.tokensaver.fr)) |
|-----------------------------|-------------------------------------------------------------------------------------|
| Listen / MITM / forward HTTPS | Ingest, Flux IA, storage |
| Optional body capture (auth headers masked) | Governance policies (cache, RAG, compression, PII, routing) |
| Ship audits + call policy APIs with your `ts_…` key | Catalogue enforce, quotas, dashboards |

Default ingest: `https://api.tokensaver.fr/api/v1/egress/ingest`.

### How it works (principles)

1. **Runs next to the agent, not in the cloud** — laptop, CI, or pod. SaaS never terminates your LLM TLS for you.
2. **Traffic still goes to the real provider** — Anthropic / OpenAI remain the destination. Unset `HTTPS_PROXY` and tools talk to providers directly again.
3. **Metadata first** — bodies off by default (`EGRESS_CAPTURE_BODIES` to opt in). Auth headers masked when bodies are captured.
4. **Visibility levels** — explicit `HTTPS_PROXY` · MITM (local CA) · transparent (Linux, advanced).
5. **SaaS decides policy** — the proxy fetches effective settings for your key.
6. **Streaming stays interactive** — no “buffer everything then reply” for normal chat.
7. **Fail soft by default** — brief SaaS outages usually don’t block forward.
8. **Your key is the trust boundary** — without a valid `TOKENSAVER_API_KEY`, Flux IA stays empty.

---

## Everyday commands

| Command | What it does |
|---------|----------------|
| `tokensaver-egress` | Interactive menu (TTY) or help |
| `tokensaver-egress setup` | Guided setup (API key, CA, skills, options) |
| `tokensaver-egress serve` | Start proxy in foreground (**logs here**) |
| `tokensaver-egress claude --no-start` | Claude using an already-running `serve` |
| `tokensaver-egress claude` | Auto-start proxy + Claude (one terminal) |
| `tokensaver-egress claude --keep-proxy` | Auto-start proxy, leave it up after Claude exits |
| `tokensaver-egress run -- CMD` | Same for any command |
| `tokensaver-egress stop` | Stop a proxy auto-started by `claude` / `run` |
| `tokensaver-egress install-skills` | Install Claude skills → `~/.claude/skills/tokensaver-router` |
| `tokensaver-egress unproxy` | Clear `HTTPS_PROXY` / `HTTP_PROXY` in this shell |
| `tokensaver-egress init-ca` | (Re)generate the MITM CA |
| `Ctrl+C` | Stop `serve` |

If the port is busy: `tokensaver-egress serve --port 8889` (and update `HTTPS_PROXY`), or `tokensaver-egress stop`.

### Claude Code skills

```bash
tokensaver-egress install-skills
# or: tokensaver-egress install-skills --force
```

Installs CCR / onboarding / MCP helpers. Restart Claude Code after install.

### After Claude / Ctrl+C — clear proxy env

Stopping egress does **not** unset env vars in other shells (or shells that sourced `client-env.sh`):

| Layer | Clear with |
|-------|------------|
| Process on `:8888` | `tokensaver-egress stop` or Ctrl+C on `serve` |
| `HTTPS_PROXY` in **this** shell | `eval "$(tokensaver-egress unproxy --sh)"` |

```bash
tokensaver-egress stop
eval "$(tokensaver-egress unproxy --sh)"
```

---

## 5-minute setup (manual MITM)

MITM can see **model, tokens, and tool names**. Trust a **local** CA once.

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

**Trust that CA** on this Mac, then tell **Node** (Claude Code) to use it:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  "$HOME/.tokensaver-egress/ca/ca.crt"

export NODE_EXTRA_CA_CERTS="$HOME/.tokensaver-egress/ca/ca.crt"
```

### 3. Terminal 1 — start the proxy

```bash
EGRESS_MITM_ENABLED=true tokensaver-egress serve --port 8888
```

### 4. Terminal 2 — point tools at the proxy

```bash
export HTTPS_PROXY=http://127.0.0.1:8888
export HTTP_PROXY=http://127.0.0.1:8888
export NODE_EXTRA_CA_CERTS="$HOME/.tokensaver-egress/ca/ca.crt"
claude
# or: tokensaver-egress claude --no-start
```

### 5. Check Flux IA

[platform.tokensaver.fr](https://platform.tokensaver.fr) → **Flux IA** — new calls should appear within seconds.

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

**Mental model:** explicit = “I opted this shell in”; MITM = “I also trust a local cert so TokenSaver can enrich the audit”; transparent = “the network forces traffic through the proxy” (ops / lock-down setups).

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
| `pip install` → `Connection refused` to `localhost:8888` / “No matching distribution” | **Regression footgun:** shell still has `HTTPS_PROXY` after Claude quit (and/or proxy was left with `--keep-proxy` then died). Run `tokensaver-egress stop` then `eval "$(tokensaver-egress unproxy --sh)"`, retry with `--no-cache-dir`. Prefer day-to-day `claude` **without** `--keep-proxy` and without sourcing `client-env.sh`. |
| Claude `403 Blocked by TokenSaver loop precheck: deny` (`GOAL_REQUIRED`) | You still have a BusinessLoop / `TOKENSAVER_LOOP_ID` active (legacy `--loop` or skills). Prefer plain `serve` + `claude --no-start`. Or clear: `tokensaver-egress stop`, remove `~/.tokensaver-egress/current-loop.env`, restart without `--loop`. |
| `loop status` / Flux shows traffic but Boucle stuck | Prefer day-to-day **without** `--loop`. Skills manage Boucles via MCP when needed. If stuck: `stop`, delete `current-loop.env`, new `serve`. |
| `pip install -U` stays on an old version | `pip install -U --no-cache-dir tokensaver-egress` (after unproxy if needed) |
| TTFT empty on dashboard (egress) | Upgrade to **0.1.18+** and generate new LLM calls — TTFT is ``attrs.ttft_ms`` (first body byte after the provider request). Older audits lack the field. With ``OBSERVABILITY_CLICKHOUSE_READ``, confirm ``clickhouse_summary.py`` extracts TTFT quantiles (not hardcoded ``sample_count: 0``). |
| Overview Performance / compression blocks / Prompt cache empty (CH READ on) | Ensure worker-observability runs **arq** (not Uvicorn), dual-write includes ``payload_json.attrs``, and summary maps TTFT / ``compression.blocks`` / ``cache_read_input_tokens`` — see platform docs **ARCHITECTURE-OBSERVABILITY-STORES.md** §3.2bis. |
| Smart Cache 0% but Anthropic shows millions of cache tokens | Expected: **Smart Cache** = TokenSaver semantic hit (no LLM). **Prompt cache** = provider ``cache_read_input_tokens`` (LLM still runs). Token Flow shows a separate **Prompt cache** node when read > 0. |
| TLS / certificate errors in Claude Code (`CERT_SIGNATURE_FAILURE`) | Usually **stale leaf certs** after CA recreate: restart `serve` (0.1.17+ auto-purges). Or `rm -rf ~/.tokensaver-egress/ca/leaves`. Also use `tokensaver-egress claude --no-start` (sets `NODE_EXTRA_CA_CERTS`) — not bare `claude` with only `HTTPS_PROXY`. |
| Nothing in Flux IA | Check `TOKENSAVER_API_KEY` + `TOKENSAVER_INGEST_URL`; watch proxy logs |
| Login / signup fails with SSO message | Org requires IdP — open the console via SSO, create a key, **paste** it (option 3) |
| Login OK but “email not verified” | Verify email in the console, then retry login or paste a key |
| Login OK but “API key quota reached” | Delete an unused key in Settings → API keys, or **paste** an existing key |
| Port already in use | `tokensaver-egress stop` / free `:8888`, or `serve --port 8889` (update `HTTPS_PROXY`) |
| Want quieter startup | `TOKENSAVER_NO_BANNER=1 tokensaver-egress serve` |
| Boucle ends with `verification_failed` / `METRIC_MISSING` while Claude says Goal achieved | Skills must end with ``work_complete`` for file labs (not ``goal_met`` without metrics). Reinstall: `tokensaver-egress install-skills --force`. See console **Developer workspace** alerts + Flux IA. |

---

## Security

- Run MITM **only on machines you control**. The CA can decrypt traffic you route through the proxy.
- `HTTPS_PROXY` is easy to unset — it is **not** a hard security boundary. For lock-down, see `k8s/networkpolicy.example.yaml` / transparent mode.
- Bodies are optional; auth headers are masked unless you opt into raw headers.

---

## Related

| Package | Role |
|---------|------|
| [`tokensaver-cli`](https://pypi.org/project/tokensaver-cli/) | Route Claude Code / Cursor through TokenSaver APIs |
| [`tokensaver-sdk`](https://pypi.org/project/tokensaver-sdk/) | Python SDK |
| [TokenSaver platform](https://platform.tokensaver.fr) | Control plane — **Flux IA**, **Developer workspace** (Boucles / costs / alerts), policies, keys |

## License

MIT © TokenSaver
