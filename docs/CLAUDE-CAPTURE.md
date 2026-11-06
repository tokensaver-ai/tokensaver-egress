# Claude Code capture side-channel (agentic graph P0)

When `tokensaver-egress serve` starts, a local HTTP server listens on
**`127.0.0.1:8789`** (override with `EGRESS_CAPTURE_PORT`, disable with
`EGRESS_CLAUDE_CAPTURE=0`). Ports **8787/8788** are reserved for MCP HTTP / gateway.

## Endpoints

| Method | Path | Role |
|--------|------|------|
| `GET` | `/health` | Liveness |
| `POST` | `/hooks` | Claude Code HTTP hooks (always returns `continue: true` — fail-open) |
| `POST` | `/v1/logs` `/v1/metrics` `/v1/traces` | OTLP byte relay → platform |

Every event is wrapped in the common envelope (`source`, `session_id`, `seq`,
`ts`, `claude_code_version`, `payload`) and shipped to
`POST {TOKENSAVER_INGEST_URL}/egress/agentic-events`.

## Wiring Claude Code

`tokensaver-egress claude` and Claude `settings.json` (via `ide_config`) inject:

- `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:8789`
- `CLAUDE_CODE_ENABLE_TELEMETRY=1`
- `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`
- `TOKENSAVER_CLAUDE_HOOKS_URL=http://127.0.0.1:8789/hooks`

**Hooks** : the Claude plugin (`tokensaver-router`) runs
`hooks/forward-capture.sh` (command hooks, fail-open) for
`PreToolUse` / `PostToolUse` / `Subagent*` / `Session*` / `PreCompact` / `Stop`.
Reinstall with `PYTHONPATH=src python -c "from tokensaver_egress.plugin_install import install_claude_plugin; install_claude_plugin(force=True)"`
from `packages/egress` after pulls.

Restart `tokensaver-egress serve` so the side-channel binds **8789**.

## See also

- `docs/agentic-graph-spec.md` §§3, 10, 14–15
