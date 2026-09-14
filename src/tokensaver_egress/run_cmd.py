"""Run a command (e.g. Claude Code) with proxy env — no second terminal needed."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from tokensaver_egress.ca import MitmCA
from tokensaver_egress.proxy_env import CONFIG_DIR, write_client_unproxy
from tokensaver_egress.serve_cmd import resolve_port
from tokensaver_egress.wizard import apply_saved_env, write_client_env

PID_FILE = CONFIG_DIR / "auto-proxy.pid"
LOG_FILE = CONFIG_DIR / "auto-proxy.log"


def _port_open(port: int, *, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def _wait_for_port(port: int, *, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.15)
    return False


def _read_pid() -> int | None:
    try:
        raw = PID_FILE.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(40):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def stop_auto_proxy() -> bool:
    """Stop a proxy previously started by ``run`` / ``claude``. Returns True if stopped."""
    pid = _read_pid()
    if pid is None:
        return False
    if _pid_alive(pid):
        _stop_pid(pid)
    try:
        PID_FILE.unlink(missing_ok=True)  # type: ignore[call-arg]
    except TypeError:
        if PID_FILE.is_file():
            PID_FILE.unlink()
    except OSError:
        pass
    return True


def client_environ(*, port: int, ca: MitmCA) -> dict[str, str]:
    env = os.environ.copy()
    proxy = f"http://127.0.0.1:{port}"
    env["HTTPS_PROXY"] = proxy
    env["HTTP_PROXY"] = proxy
    env["https_proxy"] = proxy
    env["http_proxy"] = proxy
    env["NODE_EXTRA_CA_CERTS"] = str(ca.ca_cert_path)
    # Avoid proxying TokenSaver / local control-plane calls from the agent itself.
    no_proxy = env.get("NO_PROXY") or env.get("no_proxy") or ""
    extras = "127.0.0.1,localhost,api.tokensaver.fr,platform.tokensaver.fr"
    env["NO_PROXY"] = f"{no_proxy},{extras}" if no_proxy else extras
    env["no_proxy"] = env["NO_PROXY"]
    return env


def ensure_proxy(
    *,
    port: int,
    start_if_needed: bool = True,
) -> tuple[bool, subprocess.Popen[bytes] | None]:
    """Return (we_started, process). process is set only if we spawned serve."""
    if _port_open(port):
        return False, None
    if not start_if_needed:
        print(
            f"✖ No tokensaver-egress listening on 127.0.0.1:{port}.\n"
            f"  Start one with:  tokensaver-egress serve\n"
            f"  Or omit --no-start so this command starts it for you.",
            file=sys.stderr,
        )
        return False, None

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Stop a stale auto-proxy pid file if any.
    old = _read_pid()
    if old is not None and _pid_alive(old):
        _stop_pid(old)

    log_f = LOG_FILE.open("ab", buffering=0)
    cmd = [
        sys.executable,
        "-m",
        "tokensaver_egress",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "INFO",
    ]
    child_env = os.environ.copy()
    child_env.setdefault("TOKENSAVER_NO_BANNER", "1")
    print(f"  ·  Starting proxy on 127.0.0.1:{port} …", file=sys.stderr)
    proc = subprocess.Popen(
        cmd,
        env=child_env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    if not _wait_for_port(port, timeout_s=20.0):
        print(
            f"✖ Proxy did not become ready on port {port}.\n"
            f"  See log: {LOG_FILE}",
            file=sys.stderr,
        )
        _stop_pid(proc.pid)
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        return False, None
    print(f"  ✓  Proxy ready (pid {proc.pid}). Log: {LOG_FILE}", file=sys.stderr)
    return True, proc


def run_with_proxy(
    argv: list[str],
    *,
    start_if_needed: bool = True,
    keep_proxy: bool = False,
) -> int:
    """Run ``argv`` with HTTPS_PROXY pointing at egress; auto-start proxy if needed."""
    if not argv:
        print("Usage: tokensaver-egress run [--keep-proxy] [--no-start] -- CMD [ARGS…]", file=sys.stderr)
        print("   or: tokensaver-egress claude [ARGS…]", file=sys.stderr)
        return 2

    # Strip a lone leading "--" from argparse REMAINDER.
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("✖ Missing command to run.", file=sys.stderr)
        return 2

    apply_saved_env(only_if_unset=True)
    ca = MitmCA()
    if not ca.is_initialized():
        print("  ·  Creating MITM CA …", file=sys.stderr)
        ca.init_ca()
    port = resolve_port(8888)
    write_client_env(port, ca)
    write_client_unproxy()

    binary = argv[0]
    if shutil.which(binary) is None and binary in ("claude", "cursor"):
        print(
            f"✖ `{binary}` not found on PATH.\n"
            f"  Install Claude Code, then retry:  tokensaver-egress claude",
            file=sys.stderr,
        )
        return 127

    we_started, _proc = ensure_proxy(port=port, start_if_needed=start_if_needed)
    if start_if_needed and not _port_open(port):
        return 1
    if not start_if_needed and not _port_open(port):
        return 1

    env = client_environ(port=port, ca=ca)
    print(f"  ·  Running: {' '.join(argv)}  (via http://127.0.0.1:{port})", file=sys.stderr)
    try:
        completed = subprocess.run(argv, env=env, check=False)
        code = int(completed.returncode)
    except KeyboardInterrupt:
        print("\n  Interrupted.", file=sys.stderr)
        code = 130
    finally:
        if we_started and not keep_proxy:
            print("  ·  Stopping auto-started proxy …", file=sys.stderr)
            stop_auto_proxy()
        elif we_started and keep_proxy:
            print(
                f"  ·  Proxy left running (pid file {PID_FILE}). Stop with: tokensaver-egress stop",
                file=sys.stderr,
            )
    return code
