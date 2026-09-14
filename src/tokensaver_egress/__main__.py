"""CLI entrypoint: ``tokensaver-egress serve`` / ``init-ca`` / ``setup`` (ACP-4)."""

from __future__ import annotations

import argparse
import sys

from tokensaver_egress import __version__
from tokensaver_egress.banner import print_banner
from tokensaver_egress.ca import MitmCA
from tokensaver_egress.serve_cmd import run_serve
from tokensaver_egress.wizard import is_interactive, run_guided_setup, run_menu

DOCS_URL = "https://github.com/tokensaver-ai/tokensaver-egress"
PLATFORM_URL = "https://platform.tokensaver.fr"


def print_help(*, file=None) -> None:
    out = file if file is not None else sys.stdout
    print(
        f"""TokenSaver egress — client-side HTTPS capture proxy (ACP-4)

Usage:
  tokensaver-egress                 Interactive menu (TTY) or this help
  tokensaver-egress setup           Guided setup (API key, CA, options, start)
  tokensaver-egress serve [--host HOST] [--port PORT] [--log-level LEVEL] [--transparent]
  tokensaver-egress init-ca
  tokensaver-egress help

Commands:
  setup      Beginner-friendly wizard (recommended first run)
  serve      Start the forward proxy (explicit / MITM / transparent)
  init-ca    Generate the local MITM CA (~/.tokensaver-egress/ca)
  help       Show this message

Quick start (wizard):
  tokensaver-egress setup

Quick start (manual):
  export TOKENSAVER_API_KEY=ts_…
  export TOKENSAVER_INGEST_URL=https://api.tokensaver.fr/api/v1/egress/ingest
  tokensaver-egress init-ca
  EGRESS_MITM_ENABLED=true tokensaver-egress serve --port 8888

  # Point clients at the proxy
  export HTTPS_PROXY=http://127.0.0.1:8888
  export HTTP_PROXY=http://127.0.0.1:8888
  export NODE_EXTRA_CA_CERTS=$HOME/.tokensaver-egress/ca/ca.crt

Options (serve):
  --host HOST          Bind address (default: 0.0.0.0)
  --port PORT          Listen port (default: 8888)
  --log-level LEVEL    DEBUG | INFO | WARNING | ERROR (default: INFO)
  --transparent        Linux TPROXY / REDIRECT mode (metadata-only)

Env:
  TOKENSAVER_API_KEY / TOKENSAVER_INGEST_URL   Ship audits to the control plane
  EGRESS_MITM_ENABLED=true                     Decrypt known LLM hosts
  EGRESS_CAPTURE_BODIES=1                      Persist request/response bodies
  TOKENSAVER_NO_BANNER=1                       Hide the ASCII logo
  Config file: ~/.tokensaver-egress/env         Written by ``setup``

Docs:  {DOCS_URL}
Console: {PLATFORM_URL}
Version: {__version__}
""",
        file=out,
    )


def _cmd_init_ca(_args: argparse.Namespace) -> int:
    ca = MitmCA()
    path = ca.init_ca()
    print(f"CA certificate written to: {path}")
    print("Trust this CA on client machines, then set EGRESS_MITM_ENABLED=true")
    print(
        "  macOS: sudo security add-trusted-cert -d -r trustRoot "
        "-k /Library/Keychains/System.keychain",
        path,
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    return run_serve(
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        transparent=bool(getattr(args, "transparent", False)),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokensaver-egress",
        description="Egress capture proxy (ACP-4)",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="Run forward proxy", add_help=True)
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8888)
    serve_p.add_argument("--log-level", default="INFO")
    serve_p.add_argument(
        "--transparent",
        action="store_true",
        help="Transparent mode (TPROXY/iptables-REDIRECT): recover original dst + SNI, tunnel",
    )

    sub.add_parser("init-ca", help="Generate local MITM CA")
    sub.add_parser("setup", help="Interactive guided setup")
    sub.add_parser("wizard", help="Alias for setup")
    sub.add_parser("help", help="Show help")

    # Back-compat: ``tokensaver-egress --port 8888`` (flags without subcommand) → serve
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("-h", "--help", action="store_true", help="Show help")
    return parser


def main(argv: list[str] | None = None) -> None:
    print_banner()
    raw = list(sys.argv[1:] if argv is None else argv)

    # Bare invoke: interactive menu on a TTY, otherwise classic help.
    if not raw:
        if is_interactive():
            raise SystemExit(run_menu())
        print_help()
        raise SystemExit(0)

    if raw[0] in ("-h", "--help", "help"):
        print_help()
        raise SystemExit(0)

    # Flags without a subcommand → treat as ``serve`` (scripts / muscle memory).
    if raw[0].startswith("-"):
        raw = ["serve", *raw]

    parser = _build_parser()
    args = parser.parse_args(raw)

    if args.command in (None, "help") or getattr(args, "help", False):
        print_help()
        raise SystemExit(0)
    if args.command in ("setup", "wizard"):
        raise SystemExit(run_guided_setup(start_after=True))
    if args.command == "init-ca":
        raise SystemExit(_cmd_init_ca(args))
    if args.command == "serve":
        raise SystemExit(_cmd_serve(args))

    print_help()
    raise SystemExit(2)


if __name__ == "__main__":
    main()
