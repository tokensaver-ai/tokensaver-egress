"""Client proxy env helpers — enable / clear HTTPS_PROXY after egress stops."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("EGRESS_CONFIG_DIR", str(Path.home() / ".tokensaver-egress")))
CLIENT_ENV_FILE = CONFIG_DIR / "client-env.sh"
CLIENT_UNPROXY_FILE = CONFIG_DIR / "client-unproxy.sh"

# Vars commonly set when pointing tools at tokensaver-egress.
_PROXY_VARS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
)

# Also cleared on full stop / unproxy --ide (match ide_config.MANAGED_ENV_KEYS subset).
_EXTRA_CLEAR_VARS = (
    "NODE_EXTRA_CA_CERTS",
    "NO_PROXY",
    "no_proxy",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA",
    "TOKENSAVER_CLAUDE_HOOKS_URL",
)


def unproxy_sh_snippet() -> str:
    """Shell snippet suitable for ``eval "$(tokensaver-egress unproxy --sh)"``."""
    lines = [
        "# Clear tokensaver-egress client proxy exports",
        "unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy",
        "unset NODE_EXTRA_CA_CERTS NO_PROXY no_proxy",
        "unset OTEL_EXPORTER_OTLP_ENDPOINT CLAUDE_CODE_ENABLE_TELEMETRY",
        "unset CLAUDE_CODE_ENHANCED_TELEMETRY_BETA TOKENSAVER_CLAUDE_HOOKS_URL",
    ]
    return "\n".join(lines) + "\n"


def write_client_unproxy(path: Path | None = None) -> Path:
    path = path or CLIENT_UNPROXY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# TokenSaver egress — clear proxy exports in this shell\n"
        "# source ~/.tokensaver-egress/client-unproxy.sh\n"
        "#\n"
        + unproxy_sh_snippet(),
        encoding="utf-8",
    )
    return path


def neutralize_client_env(path: Path | None = None) -> Path:
    """Rewrite ``client-env.sh`` so sourcing it no longer re-enables the proxy."""
    path = path or CLIENT_ENV_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# TokenSaver egress — proxy stopped (neutralized)\n"
        "# Re-enable with:  tokensaver-egress serve && tokensaver-egress ide\n"
        "#             or:  tokensaver-egress claude\n"
        "#\n"
        + unproxy_sh_snippet(),
        encoding="utf-8",
    )
    return path


def unset_proxy_in_process() -> list[str]:
    """Drop proxy-related vars from ``os.environ`` (this process only)."""
    cleared: list[str] = []
    for name in (*_PROXY_VARS, *_EXTRA_CLEAR_VARS):
        if name in os.environ:
            os.environ.pop(name, None)
            cleared.append(name)
    return cleared


def clear_persisted_proxy(*, ide: bool = True) -> dict[str, Any]:
    """Clear client-env scripts + optional Claude/VS Code/shell snippets.

    Used by ``stop`` (full teardown) and ``unproxy --ide``.
    """
    unproxy_path = write_client_unproxy()
    env_path = neutralize_client_env()
    process_cleared = unset_proxy_in_process()
    report: dict[str, Any] = {
        "unproxy_file": str(unproxy_path),
        "client_env_file": str(env_path),
        "process_cleared": process_cleared,
        "claude": None,
        "vscode": [],
        "shell": [],
    }
    if ide:
        from tokensaver_egress.ide_config import (
            clear_claude_settings,
            clear_shell_rc,
            clear_vscode_settings,
        )

        report["claude"] = clear_claude_settings()
        report["vscode"] = [(str(p), s) for p, s in clear_vscode_settings()]
        report["shell"] = [(str(p), s) for p, s in clear_shell_rc()]
    return report


def active_proxy_vars() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in _PROXY_VARS:
        val = (os.environ.get(name) or "").strip()
        if val:
            out[name] = val
    return out


def looks_like_egress_proxy(value: str, *, port: int | None = None) -> bool:
    v = (value or "").lower()
    if "127.0.0.1" not in v and "localhost" not in v:
        return False
    if port is not None:
        return f":{port}" in v
    return True


def print_stopped_unproxy_hint(*, port: int = 8888, file=None) -> None:
    out = file if file is not None else sys.stderr
    unproxy = write_client_unproxy()
    print("\n  Stopped.", file=out)
    print(
        "  If another terminal still has HTTPS_PROXY → this machine, pip/curl will fail.",
        file=out,
    )
    print(f"    source {unproxy}", file=out)
    print('    # or:  eval "$(tokensaver-egress unproxy --sh)"', file=out)
    print("    # or:  unset HTTPS_PROXY HTTP_PROXY\n", file=out)


def run_unproxy_cmd(*, sh_only: bool = False, ide: bool = False) -> int:
    """CLI for ``tokensaver-egress unproxy``."""
    if sh_only:
        write_client_unproxy()
        sys.stdout.write(unproxy_sh_snippet())
        return 0

    active = active_proxy_vars()
    print("Clear client proxy env (after stopping tokensaver-egress)")
    print("────────────────────────────────────────────────────────")
    if active:
        print("  Currently set in this process:")
        for k, v in active.items():
            marker = "  ← looks like local egress" if looks_like_egress_proxy(v) else ""
            print(f"    {k}={v}{marker}")
    else:
        print("  No HTTP(S)_PROXY set in this process.")
        print("  (Your interactive shell may still have them — run the source/eval below there.)")
    print()

    report = clear_persisted_proxy(ide=ide)
    path = Path(report["unproxy_file"])
    print("  In the shell where you exported the proxy:")
    print(f"    source {path}")
    print('    # or:  eval "$(tokensaver-egress unproxy --sh)"')
    print("    # or:  unset HTTPS_PROXY HTTP_PROXY ALL_PROXY")
    print()
    if ide:
        print("  IDE / shell snippets:")
        print(f"    Claude settings: {report.get('claude')}")
        for pth, status in report.get("vscode") or []:
            print(f"    VS Code:         {status}  {pth}")
        for pth, status in report.get("shell") or []:
            print(f"    Shell rc:        {status}  {pth}")
        print()
    print("  Then retry:  pip install -U --no-cache-dir tokensaver-egress")
    return 0
