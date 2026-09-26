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

## Architecture (client only)

`tokensaver-egress` is a **lightweight client-side HTTPS proxy**. It does **not** embed TokenSaver business logic, databases, or admin console.

| Runs locally (this package) | Runs on TokenSaver SaaS ([platform.tokensaver.fr](https://platform.tokensaver.fr)) |
|-----------------------------|-------------------------------------------------------------------------------------|
| Listen / MITM / forward HTTPS | Ingest, Flux IA, storage |
| Optional body capture (auth headers masked) | Governance policies (cache, RAG, compression, PII, routing) |
| Ship audits + call policy APIs with your `ts_…` key | Catalogue enforce, quotas, dashboards |

You need an API key from the control plane. Default ingest: `https://api.tokensaver.fr/api/v1/egress/ingest`.

### How it works (principles)

These are the design ideas behind the product — enough to use it safely, without exposing internal control-plane details.

1. **Runs next to the agent, not in the cloud**  
   The proxy is a separate process on the laptop, CI runner, or pod where Claude Code / Cursor / your agent runs. TokenSaver SaaS never terminates your LLM TLS for you.

2. **Traffic still goes to the real provider**  
   Anthropic, OpenAI, etc. remain the destination. Egress sits in the middle only for observation (and optional governance hooks). Uninstall or unset `HTTPS_PROXY` and your tools talk to providers directly again.

3. **Metadata first; plaintext stays local by default**  
   By default the proxy reports **observability facts** to TokenSaver (destination host, timing, sizes, and — with MITM — model / token usage when available). Full request/response bodies are **off** unless you opt in (`EGRESS_CAPTURE_BODIES`). Auth headers are masked when bodies are captured.

4. **Three visibility levels (you choose)**  
   - **Explicit proxy** — point `HTTPS_PROXY` at egress: see that a call happened (host, latency, bytes). TLS content stays opaque.  
   - **MITM (opt-in)** — trust a **local** CA once: for known LLM hosts, egress can read what is needed for richer audits (model, tokens, tool names) and for SaaS policies.  
   - **Transparent (Linux, advanced)** — network-level redirect so clients need not set proxy env (harder to “forget” the proxy). Still metadata-oriented unless you also enable MITM where applicable.

5. **SaaS decides policy; the proxy asks and applies**  
   Cache, compression, PII handling, catalogue allow/deny, model preferences, etc. are configured on your API key in the control plane. The local proxy **fetches effective settings** and may call TokenSaver APIs during a request; it does not hard-code your organisation’s rules in the package.

6. **Streaming stays interactive**  
   When MITM is on, responses are still streamed to the agent in real time. Audits are built along the way — you should not feel a “buffer everything then reply” delay for normal chat.

7. **Fail soft by default**  
   If the control plane is briefly unreachable, capture/forward typically continues (audits may be dropped or policies skipped depending on options). Stricter “block if SaaS is down” behaviour is available for enforcement scenarios — opt in deliberately.

8. **Your key is the trust boundary**  
   `TOKENSAVER_API_KEY` authenticates ingest and policy calls. Without a valid key the proxy can still forward traffic locally, but nothing useful appears in Flux IA.

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
tokensaver-egress setup          # once (choose SaaS or Local ingest)
tokensaver-egress claude         # every day — starts proxy if needed, then Claude Code
tokensaver-egress claude --loop  # same + create a BusinessLoop (Boucles)
```

No second terminal, no `source` / `export`. When Claude exits, an auto-started proxy is stopped (use `--keep-proxy` to leave it up).

**Important:** stopping the proxy process does **not** clear `HTTPS_PROXY` in a
shell where you previously ran `source ~/.tokensaver-egress/client-env.sh`.
If `pip` / `curl` then fail with `Connection refused` to `localhost:8888`, run
`eval "$(tokensaver-egress unproxy --sh)"` (see [Troubleshooting](#troubleshooting)).

```bash
tokensaver-egress run -- curl -sS https://api.anthropic.com/...
tokensaver-egress claude --keep-proxy   # leave proxy running (then stop + unproxy later)
tokensaver-egress stop                  # stop auto-started proxy
eval "$(tokensaver-egress unproxy --sh)"  # clear leftover HTTPS_PROXY in *this* shell
```

Or open the menu with a bare `tokensaver-egress` in a terminal (TTY).

The wizard walks you through:

1. **API key** — three options:
   - **Create a free TokenSaver account** from the CLI (name, email, password) → `POST /api/v1/auths/signup` with `create_key=true` (returns a `ts_…` key; no console visit required) — **0.1.36+** · **self-serve only**
   - Paste an existing `ts_…` key
   - Open the console signup / keys page in a browser

**Enterprise orgs** (SSO / SCIM): do **not** use “Create a free account” — obtain a `ts_…` key from the console after IdP login (or from an admin). Set ``TOKENSAVER_ENTERPRISE=1`` and/or ``TOKENSAVER_ORG_HINT=<slug>`` so the wizard **hides** create-account. Identity model: platform docs **[IDENTITY-MANAGEMENT-ET-ENTERPRISE-V2.md](../../docs/IDENTITY-MANAGEMENT-ET-ENTERPRISE-V2.md)**.
2. **Ingest target** — SaaS, **Local** (`http://localhost:8000/…/ingest`), or custom URL
3. **Loop precheck** default (`TOKENSAVER_LOOP_PRECHECK`) for Boucles
4. **MITM** on/off + create the local CA if needed
5. **Trust instructions** (and optional macOS keychain trust)
6. **Body capture** on/off
7. **Port** (default `8888`)
8. **Claude Code skills** (CCR / onboarding / Boucles) if missing — no `tokensaver-cli` required
9. Saves `~/.tokensaver-egress/env` + `client-env.sh`

Then day-to-day:

```bash
tokensaver-egress claude
# govern a session:
tokensaver-egress claude --loop --max-turns 5 --goal "tests green"
```

`--loop` creates a BusinessLoop via the control plane, restarts the auto-proxy so it inherits `TOKENSAVER_LOOP_ID`, then launches Claude.
---

## Watch proxy logs + Claude (two terminals)

```bash
# Terminal A — logs (+ boucle avec goal — obligatoire pour goal_based + precheck)
tokensaver-egress serve --loop --goal "tests green"

# Terminal B — Claude through that proxy
tokensaver-egress claude --no-start
```

Sans boucle : `serve` puis `claude --no-start` (comme avant).
Sans expression de goal : `--loop-kind turn_based` (évite le 403 `GOAL_REQUIRED`).

**Stop**

1. Ctrl+C in terminal 1  
2. In terminal 2: `source ~/.tokensaver-egress/client-unproxy.sh` (or `tokensaver-egress unproxy`)

| Goal | Commands |
|------|----------|
| One command, no log window | `tokensaver-egress claude` |
| Logs visible + Claude | `serve` then `claude --no-start` |
| Leave auto-proxy up after Claude exits | `claude --keep-proxy` then later `stop` |

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
| `tokensaver-egress setup` | Guided setup (API key, CA, Claude skills, options) |
| `tokensaver-egress claude` | **Easiest day-to-day** — auto-start proxy + Claude Code |
| `tokensaver-egress claude --no-start` | Claude only — requires an already-running `serve` (see two-terminal) |
| `tokensaver-egress claude --keep-proxy` | Auto-start proxy, leave it running after Claude exits |
| `tokensaver-egress run -- CMD` | Same for any command |
| `tokensaver-egress stop` | Stop a proxy auto-started by `claude` / `run` |
| `tokensaver-egress install-skills` | Install Claude Code skills to `~/.claude/skills/tokensaver-router` |
| `tokensaver-egress unproxy` | Clear `HTTPS_PROXY` / `HTTP_PROXY` in your shell (after stop) |
| `tokensaver-egress serve` | Start proxy in foreground (logs in this terminal) |
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

### After Claude / Ctrl+C — clear the proxy in client shells

Stopping egress does **not** unset env vars in other terminals (or in a shell that
sourced `client-env.sh`). Two independent layers:

| Layer | Clear with |
|-------|------------|
| Process listening on `:8888` | `tokensaver-egress stop` (auto-proxy) or Ctrl+C on `serve` |
| `HTTPS_PROXY` / `HTTP_PROXY` in **this** shell | `eval "$(tokensaver-egress unproxy --sh)"` |

If `pip` / `curl` still try `localhost:8888`:

```bash
tokensaver-egress stop
eval "$(tokensaver-egress unproxy --sh)"
# or: source ~/.tokensaver-egress/client-unproxy.sh
# or: unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy
```

Then retry your install. See also runbook §2.1.1 (régression documentée).

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
| Claude `403 Blocked by TokenSaver loop precheck: deny` (`GOAL_REQUIRED`) | `serve --loop` / `claude --loop` without `--goal` on a `goal_based` loop. Ctrl+C the serve, restart with `--loop --goal "…"`, or use `--loop-kind turn_based`. |
| `loop status` shows new loop / iters=0 but Claude still 403 | Stale serve still on `:8888` with an old (often cancelled) `TOKENSAVER_LOOP_ID`. `stop` / Ctrl+C, then one `serve --loop --goal "…"`. Do not start a second serve while the first is up. |
| `loop status` iters=0 while Flux shows Anthropic traffic | Captures missing `attrs.loop_id`. Causes: (1) egress &lt; 0.1.25 not stamping; (2) **platform API** dropping flattened `loop_id` (Pydantic) — fixed when `EgressRecord` declares `loop_id` / `loop_iteration`. Restart backend, upgrade egress ≥0.1.26, new Claude turn. |
| `pip install -U` stays on an old version | `pip install -U --no-cache-dir tokensaver-egress` (after unproxy if needed) |
| TTFT empty on dashboard (egress) | Upgrade to **0.1.18+** and generate new LLM calls — TTFT is ``attrs.ttft_ms`` (first body byte after the provider request). Older audits lack the field. With ``OBSERVABILITY_CLICKHOUSE_READ``, confirm ``clickhouse_summary.py`` extracts TTFT quantiles (not hardcoded ``sample_count: 0``). |
| Overview Performance / compression blocks / Prompt cache empty (CH READ on) | Ensure worker-observability runs **arq** (not Uvicorn), dual-write includes ``payload_json.attrs``, and summary maps TTFT / ``compression.blocks`` / ``cache_read_input_tokens`` — see platform docs **ARCHITECTURE-OBSERVABILITY-STORES.md** §3.2bis. |
| Smart Cache 0% but Anthropic shows millions of cache tokens | Expected: **Smart Cache** = TokenSaver semantic hit (no LLM). **Prompt cache** = provider ``cache_read_input_tokens`` (LLM still runs). Token Flow shows a separate **Prompt cache** node when read > 0. |
| TLS / certificate errors in Claude Code (`CERT_SIGNATURE_FAILURE`) | Usually **stale leaf certs** after CA recreate: restart `serve` (0.1.17+ auto-purges). Or `rm -rf ~/.tokensaver-egress/ca/leaves`. Also use `tokensaver-egress claude --no-start` (sets `NODE_EXTRA_CA_CERTS`) — not bare `claude` with only `HTTPS_PROXY`. |
| Nothing in Flux IA | Check `TOKENSAVER_API_KEY` + `TOKENSAVER_INGEST_URL`; watch proxy logs |
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
