"""CLI entrypoint: ``python -m tokensaver_egress --port 8888`` (ACP-4)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from tokensaver_egress.banner import print_banner
from tokensaver_egress.ca import MitmCA
from tokensaver_egress.mitm import mitm_enabled
from tokensaver_egress.proxy import serve

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


def _cmd_init_ca(args: argparse.Namespace) -> int:
    ca = MitmCA()
    path = ca.init_ca()
    print(f"CA certificate written to: {path}")
    print("Trust this CA on client machines, then set EGRESS_MITM_ENABLED=true")
    print("  macOS: sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain", path)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    logging.getLogger("asyncio").addFilter(_AsyncioNoiseFilter())
    ca = MitmCA()
    if mitm_enabled() and not ca.is_initialized():
        logging.error("EGRESS_MITM_ENABLED but CA missing — run: python -m tokensaver_egress init-ca")
        return 1
    if mitm_enabled():
        logging.info("MITM TLS enabled for known LLM provider hosts (port 443)")
    transparent = bool(getattr(args, "transparent", False))
    if transparent and mitm_enabled():
        logging.warning("transparent mode captures metadata only (no MITM); ignoring MITM for tunnelled flows")
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
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tokensaver-egress", description="Egress capture proxy (ACP-4)")
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="Run forward proxy (default)")
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8888)
    serve_p.add_argument("--log-level", default="INFO")
    serve_p.add_argument(
        "--transparent",
        action="store_true",
        help="Transparent mode (TPROXY/iptables-REDIRECT): recover original dst + SNI, tunnel",
    )

    init_p = sub.add_parser("init-ca", help="Generate local MITM CA (required for EGRESS_MITM_ENABLED)")
    init_p.set_defaults(func=_cmd_init_ca)

    # Back-compat: ``python -m tokensaver_egress --port 8888`` → serve
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()
    print_banner()
    if args.command == "init-ca":
        sys.exit(_cmd_init_ca(args))
    if args.command == "serve" or args.command is None:
        if args.command == "serve":
            serve_args = args
        else:
            serve_args = args
        sys.exit(_cmd_serve(serve_args))
    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
