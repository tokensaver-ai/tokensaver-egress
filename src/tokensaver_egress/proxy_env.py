"""Client proxy env helpers — enable / clear HTTPS_PROXY after egress stops."""

from __future__ import annotations

import os
import sys
from pathlib import Path

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


def unproxy_sh_snippet() -> str:
    """Shell snippet suitable for ``eval "$(tokensaver-egress unproxy --sh)"``."""
    lines = [
        "# Clear tokensaver-egress client proxy exports",
        "unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy",
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


def run_unproxy_cmd(*, sh_only: bool = False) -> int:
    """CLI for ``tokensaver-egress unproxy``."""
    path = write_client_unproxy()
    if sh_only:
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
    print("  In the shell where you exported the proxy:")
    print(f"    source {path}")
    print('    # or:  eval "$(tokensaver-egress unproxy --sh)"')
    print("    # or:  unset HTTPS_PROXY HTTP_PROXY ALL_PROXY")
    print()
    print("  Then retry:  pip install -U --no-cache-dir tokensaver-egress")
    return 0
