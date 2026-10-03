"""Shared ``serve`` runner used by the CLI and the interactive wizard."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
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


def _port_in_use(port: int, *, host: str = "127.0.0.1") -> bool:
    """True if something already accepts TCP on host:port (typical leftover serve)."""
    try:
        with socket.create_connection((host, int(port)), timeout=0.35):
            return True
    except OSError:
        return False


def run_serve(
    *,
    host: str = "0.0.0.0",
    port: int = 8888,
    log_level: str = "INFO",
    transparent: bool = False,
    load_saved_env: bool = True,
    with_loop: bool = False,
    loop_kind: str = "goal_based",
    loop_max_turns: int | None = 5,
    loop_goal: str | None = None,
) -> int:
    """Start the forward proxy. Returns a process exit code."""
    if load_saved_env:
        apply_saved_env(only_if_unset=True)

    port = resolve_port(port)

    # Create the BusinessLoop *after* we know the listen port is free. Otherwise
    # ``--loop`` writes current-loop.env / TOKENSAVER_LOOP_ID for a loop that
    # never binds, while an old serve keeps serving a stale (often cancelled)
    # loop_id — ``tokensaver loop status`` then shows iters=0 and Claude gets 403.
    if _port_in_use(port):
        print(f"\n  ✖ Port {port} is already in use.", file=sys.stderr)
        print("    Stop the other proxy first:", file=sys.stderr)
        print("      tokensaver-egress stop", file=sys.stderr)
        print("      # or Ctrl+C on the terminal running `serve`", file=sys.stderr)
        print(
            "    Then retry. (Avoids a new Boucle while the old proxy keeps running.)\n",
            file=sys.stderr,
        )
        return 1

    if with_loop:
        from tokensaver_egress.business_loop import apply_loop_env, start_business_loop

        try:
            row = start_business_loop(
                kind=loop_kind,
                max_turns=loop_max_turns,
                goal=loop_goal,
            )
        except Exception as exc:
            print(f"\n  ✖ Could not start BusinessLoop: {exc}", file=sys.stderr)
            print(
                "    Check TOKENSAVER_API_KEY + ingest URL (tokensaver-egress setup).\n",
                file=sys.stderr,
            )
            return 1
        lid = str(row.get("loop_id") or "")
        kind = str(row.get("loop_kind") or loop_kind)
        apply_loop_env(loop_id=lid, loop_kind=kind, iteration=1, precheck=True)
        print(f"  ✓  BusinessLoop started: {lid} ({kind})", file=sys.stderr)
        if loop_goal:
            print(f"  ·  goal: {loop_goal}", file=sys.stderr)

    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))
    logging.getLogger("asyncio").addFilter(_AsyncioNoiseFilter())
    ca = MitmCA()
    if mitm_enabled() and not ca.is_initialized():
        logging.error("EGRESS_MITM_ENABLED but CA missing — run: tokensaver-egress init-ca")
        print("\n  ✖ MITM enabled but CA is missing.", file=sys.stderr)
        print("    Run:  tokensaver-egress setup   (or: tokensaver-egress init-ca)\n", file=sys.stderr)
        return 1
    if ca.is_initialized():
        ca.ensure_leaves_match_ca()
    if mitm_enabled():
        logging.info("MITM TLS enabled for known LLM provider hosts (port 443)")
    if transparent and mitm_enabled():
        logging.warning(
            "transparent mode captures metadata only (no MITM); ignoring MITM for tunnelled flows"
        )

    # Keep client-env.sh in sync with the real listen port (e.g. EGRESS_PORT=8080).
    if ca.is_initialized():
        try:
            from tokensaver_egress.wizard import write_client_env

            env_path = write_client_env(port, ca)
            print(file=sys.stderr)
            print("  Proxy ready — in another terminal run Claude via egress:", file=sys.stderr)
            print("    tokensaver-egress claude --no-start", file=sys.stderr)
            print("  (sets HTTPS_PROXY + NODE_EXTRA_CA_CERTS for you)", file=sys.stderr)
            print(f"  Or:  source {env_path} && claude", file=sys.stderr)
            if (os.environ.get("TOKENSAVER_LOOP_ID") or "").strip():
                print(
                    f"  ·  This serve stamps loop_id={os.environ['TOKENSAVER_LOOP_ID']}",
                    file=sys.stderr,
                )
            print(file=sys.stderr)
        except OSError:
            pass

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
        print("    Try:  tokensaver-egress stop", file=sys.stderr)
        print(f"    Or:   tokensaver-egress serve --port {int(port) + 1}\n", file=sys.stderr)
        return 1
    return 0
