"""Shared ``serve`` runner used by the CLI and the interactive wizard."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from tokensaver_egress.ca import MitmCA
from tokensaver_egress.mitm import mitm_enabled
from tokensaver_egress.proxy import serve
from tokensaver_egress.wizard import apply_saved_env

# Benign asyncio noise emitted by loop.start_tls() + the low-level streams API
# when a client closes a keep-alive TLS connection mid-flight (MITM mode).
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
    asyncio.get_running_loop().set_exception_handler(_quiet_loop_exception_handler)
    await serve(**kwargs)


def resolve_port(explicit: int = 8888, *, default: int = 8888) -> int:
    """Use CLI port when not the default; otherwise honour ``EGRESS_PORT`` if set."""
    if explicit != default:
        return explicit
    raw = (os.environ.get("EGRESS_PORT") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return explicit


def run_serve(
    *,
    host: str = "0.0.0.0",
    port: int = 8888,
    log_level: str = "INFO",
    transparent: bool = False,
    load_saved_env: bool = True,
) -> int:
    """Start the forward proxy. Returns a process exit code."""
    if load_saved_env:
        apply_saved_env(only_if_unset=True)
    port = resolve_port(port)

    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))
    logging.getLogger("asyncio").addFilter(_AsyncioNoiseFilter())
    ca = MitmCA()
    if mitm_enabled() and not ca.is_initialized():
        logging.error("EGRESS_MITM_ENABLED but CA missing — run: tokensaver-egress init-ca")
        print("\n  ✖ MITM enabled but CA is missing.", file=sys.stderr)
        print("    Run:  tokensaver-egress setup   (or: tokensaver-egress init-ca)\n", file=sys.stderr)
        return 1
    if mitm_enabled():
        logging.info("MITM TLS enabled for known LLM provider hosts (port 443)")
    if transparent and mitm_enabled():
        logging.warning(
            "transparent mode captures metadata only (no MITM); ignoring MITM for tunnelled flows"
        )
    try:
        asyncio.run(
            _serve_quiet(
                host=host,
                port=port,
                ca=ca if ca.is_initialized() else None,
                transparent=transparent,
            )
        )
    except KeyboardInterrupt:
        from tokensaver_egress.proxy_env import print_stopped_unproxy_hint

        print_stopped_unproxy_hint(port=port)
        return 0
    except OSError as exc:
        err = getattr(exc, "strerror", None) or str(exc)
        logging.error("cannot bind %s:%s — %s", host, port, err)
        print(f"\n  ✖ Cannot listen on {host}:{port} ({err}).", file=sys.stderr)
        print("    Is another tokensaver-egress (or process) already using that port?", file=sys.stderr)
        print(f"    Try:  tokensaver-egress serve --port {int(port) + 1}\n", file=sys.stderr)
        return 1
    return 0
