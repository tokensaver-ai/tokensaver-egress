# tokensaver-egress

Client-side **HTTPS egress capture proxy** for the [TokenSaver](https://tokensaver.fr) control plane (ACP-4).

Runs **on the client** (Claude Code, Cursor, n8n, custom agents). Captures outbound HTTP/HTTPS — including flows that protocol-aware SDKs never see — then ships **metadata** (and optionally bodies) to `POST /api/v1/egress/ingest`.

[![PyPI](https://img.shields.io/pypi/v/tokensaver-egress?style=flat-square&logo=pypi)](https://pypi.org/project/tokensaver-egress/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/tokensaver-egress/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

## Install

```bash
pip install tokensaver-egress
```

Editable (monorepo):

```bash
pip install -e "packages/egress[dev]"
```

## Quick start

```bash
export TOKENSAVER_API_KEY=ts_...
export TOKENSAVER_INGEST_URL=https://api.tokensaver.fr/api/v1/egress/ingest

# Optional MITM (model / tokens / tool names)
tokensaver-egress init-ca
# Trust ~/.tokensaver-egress/ca/ca.crt on the client OS (+ NODE_EXTRA_CA_CERTS for Node)
export EGRESS_MITM_ENABLED=true

tokensaver-egress serve --port 8888
```

Point any client at the proxy:

```bash
export HTTPS_PROXY=http://127.0.0.1:8888
export HTTP_PROXY=http://127.0.0.1:8888
export NODE_EXTRA_CA_CERTS=$HOME/.tokensaver-egress/ca/ca.crt   # Claude Code / Node
```

Equivalent module form: `python -m tokensaver_egress serve --port 8888`.

## Modes

| Mode | How | Capture |
|------|-----|---------|
| Explicit (default) | `HTTPS_PROXY` → proxy | host, bytes, latency; HTTP JSON when parseable |
| MITM | `EGRESS_MITM_ENABLED=true` + trusted CA | model, tokens, MCP/A2A, optional bodies |
| Transparent | `tokensaver-egress serve --transparent` + `scripts/tproxy-setup.sh` (Linux) | original dst + SNI, metadata |

## Env (common)

| Variable | Role |
|----------|------|
| `TOKENSAVER_API_KEY` | Ship audits + governance (authorize, compress, …) |
| `TOKENSAVER_INGEST_URL` | Default SaaS: `https://api.tokensaver.fr/api/v1/egress/ingest` |
| `EGRESS_MITM_ENABLED` | TLS terminate known LLM hosts |
| `EGRESS_CAPTURE_BODIES` | Persist request/response bodies (auth headers masked) |
| `EGRESS_ENFORCE_ENABLED` | Pre-flight catalogue deny (zero-trust) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Optional OTLP traces (e.g. `http://localhost:4318/v1/traces`) |

## Monorepo helpers

From the TokenSaver platform repo:

```bash
./scripts/start-egress.sh --mode mitm --capture-bodies
./scripts/stop-egress.sh
```

Docs: [RUNBOOK-EGRESS-ACP-4](https://github.com/CapIA-Labs-ai/tokensaver-platform/blob/main/docs/RUNBOOK-EGRESS-ACP-4.md) · [SPEC-ACP-4](https://github.com/CapIA-Labs-ai/tokensaver-platform/blob/main/docs/SPEC-ACP-4-EGRESS-PROXY.md)

## Security note

`HTTPS_PROXY` is convenience, not a hard boundary. Real anti-bypass needs network controls (TPROXY / NetworkPolicy). MITM requires trusting a **local** CA you generate — only do this on machines you control.

## Related

- [`tokensaver-cli`](https://pypi.org/project/tokensaver-cli/) — route Claude Code / Cursor through the control plane
- [`tokensaver-sdk`](https://pypi.org/project/tokensaver-sdk/) — Python SDK

## License

MIT © TokenSaver
