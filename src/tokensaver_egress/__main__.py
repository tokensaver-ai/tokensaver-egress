"""CLI entrypoint: ``tokensaver-egress serve`` / ``init-ca`` (ACP-4)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from tokensaver_egress import __version__
from tokensaver_egress.banner import print_banner
from tokensaver_egress.ca import MitmCA
from tokensaver_egress.mitm import mitm_enabled
from tokensaver_egress.proxy import serve

DOCS_URL = "https://github.com/tokensaver-ai/tokensaver-egress"
PLATFORM_URL = "https://platform.tokensaver.fr"

# Benign asyncio noise emitted by loop.start_tls() + the low-level streams API
# when a client closes a keep-alive TLS connection mid-flight (MITM mode). The
# exchange itself succeeds; these messages would otherwise spam the log.
_ASYNCIO_NOISE = (
    "returning true from eof_received() has no effect when using ssl",
    "Task was destroyed but it is pending",
)


class _AsyncioNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(noise in msg for noise in _ASYNCIO_NOISE)


def _quiet_loop_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    msg = str(context.get("message", ""))
    if any(noise in msg for noise in _ASYNCIO_NOISE):
        return
    loop.default_exception_handler(context)


async def _serve_quiet(**kwargs) -> None:
    """Install the noise-suppressing exception handler on the running loop, then serve."""
    asyncio.get_running_loop().set_exception_handler(_quiet_loop_exception_handler)
    await serve(**kwargs)


def print_help(*, file=None) -> None:
    out = file if file is not None else sys.stdout
    print(
        f"""TokenSaver egress — client-side HTTPS capture proxy (ACP-4)

Usage:
  tokensaver-egress serve [--host HOST] [--port PORT] [--log-level LEVEL] [--transparent]
  tokensaver-egress init-ca
  tokensaver-egress help

Commands:
  serve      Start the forward proxy (explicit / MITM / transparent)
  init-ca    Generate the local MITM CA (~/.tokensaver-egress/ca)
  help       Show this message

Quick start:
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
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    logging.getLogger("asyncio").addFilter(_AsyncioNoiseFilter())
    ca = MitmCA()
    if mitm_enabled() and not ca.is_initialized():
        logging.error("EGRESS_MITM_ENABLED but CA missing — run: tokensaver-egress init-ca")
        print("\n  ✖ MITM enabled but CA is missing.", file=sys.stderr)
        print("    Run:  tokensaver-egress init-ca\n", file=sys.stderr)
        return 1
    if mitm_enabled():
        logging.info("MITM TLS enabled for known LLM provider hosts (port 443)")
    transparent = bool(getattr(args, "transparent", False))
    if transparent and mitm_enabled():
        logging.warning(
            "transparent mode captures metadata only (no MITM); ignoring MITM for tunnelled flows"
        )
    try:
        asyncio.run(
            _serve_quiet(
                host=args.host,
                port=args.port,
                ca=ca if ca.is_initialized() else None,
                transparent=transparent,
            )
        )
    except KeyboardInterrupt:
        print("\n  Stopped.", file=sys.stderr)
        return 0
    except OSError as exc:
        err = getattr(exc, "strerror", None) or str(exc)
        logging.error("cannot bind %s:%s — %s", args.host, args.port, err)
        print(f"\n  ✖ Cannot listen on {args.host}:{args.port} ({err}).", file=sys.stderr)
        print("    Is another tokensaver-egress (or process) already using that port?", file=sys.stderr)
        print(f"    Try:  tokensaver-egress serve --port {int(args.port) + 1}\n", file=sys.stderr)
        return 1
    return 0


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

    if not raw or raw[0] in ("-h", "--help", "help"):
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
    if args.command == "init-ca":
        raise SystemExit(_cmd_init_ca(args))
    if args.command == "serve":
        raise SystemExit(_cmd_serve(args))

    print_help()
    raise SystemExit(2)


if __name__ == "__main__":
    main()
