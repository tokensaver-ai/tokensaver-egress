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
SERVE_PID_FILE = CONFIG_DIR / "serve.pid"
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


def _read_pid_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def _read_pid() -> int | None:
    return _read_pid_file(PID_FILE)


def _unlink_pid_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)  # type: ignore[call-arg]
    except TypeError:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


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


def _pids_listening_on_port(port: int) -> list[int]:
    """PIDs with a TCP LISTEN socket on ``port`` (macOS/Linux via ``lsof``).

    Uses ``Popen`` (not ``run``/``check_output``) so tests that stub
    ``subprocess.run`` for Claude launches do not break ``stop``.
    """
    pids: set[int] = set()
    try:
        proc = subprocess.Popen(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out, _ = proc.communicate(timeout=5)
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return []
    if not out:
        return []
    for token in out.split():
        try:
            pid = int(token.strip())
        except ValueError:
            continue
        if pid > 0:
            pids.add(pid)
    return sorted(pids)


def stop_proxy(*, port: int | None = None) -> bool:
    """Stop any tokensaver-egress proxy for ``port``.

    Covers:
    - auto-proxy from ``claude`` / ``vscode`` / ``run`` (``auto-proxy.pid``)
    - foreground ``serve`` (``serve.pid`` + listeners on the port)
    - leftover ``serve`` / scripts without a pid file (kill by port)

    Returns True if at least one process was stopped or a pid file cleared.
    """
    target = int(port) if port is not None else resolve_port(8888)
    stopped = False
    self_pid = os.getpid()
    candidates: list[int] = []

    for path in (PID_FILE, SERVE_PID_FILE):
        pid = _read_pid_file(path)
        if pid is not None and pid != self_pid:
            candidates.append(pid)
        _unlink_pid_file(path)

    for pid in _pids_listening_on_port(target):
        if pid != self_pid:
            candidates.append(pid)

    # Dedupe while preserving order
    seen: set[int] = set()
    unique: list[int] = []
    for pid in candidates:
        if pid in seen:
            continue
        seen.add(pid)
        unique.append(pid)

    for pid in unique:
        if _pid_alive(pid):
            _stop_pid(pid)
            stopped = True

    # Wait briefly for the port to free (TIME_WAIT / late exit).
    for _ in range(30):
        if not _port_open(target):
            break
        time.sleep(0.1)

    if _port_open(target):
        # Last resort: kill any remaining listeners again.
        for pid in _pids_listening_on_port(target):
            if pid == self_pid:
                continue
            _stop_pid(pid)
            stopped = True
        time.sleep(0.2)

    return stopped or (not _port_open(target) and bool(unique))


def stop_auto_proxy() -> bool:
    """Backward-compatible alias: stop auto-proxy **and** any serve on the egress port."""
    return stop_proxy()


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
    extras = (
        "127.0.0.1,localhost,::1,"
        "api.tokensaver.fr,platform.tokensaver.fr,"
        "mcp.tokensaver.fr,gateway.tokensaver.fr"
    )
    env["NO_PROXY"] = f"{no_proxy},{extras}" if no_proxy else extras
    env["no_proxy"] = env["NO_PROXY"]
    # Agentic-graph P0: point Claude OTEL + hooks at the capture side-channel.
    try:
        from tokensaver_egress.claude_capture import client_environ_for_claude_capture

        env.update(client_environ_for_claude_capture())
    except Exception:
        pass
    return env


def ensure_proxy(
    *,
    port: int,
    start_if_needed: bool = True,
    force_restart: bool = False,
) -> tuple[bool, subprocess.Popen[bytes] | None]:
    """Return (we_started, process). process is set only if we spawned serve."""
    if force_restart and _port_open(port):
        # Free auto-proxy *or* leftover serve so LOOP_* env is picked up fresh.
        stop_proxy(port=port)
        if _port_open(port):
            print(
                f"✖ Port {port} is already in use by another process.\n"
                f"  Stop it:  tokensaver-egress stop\n"
                f"  Then retry — BusinessLoop binding requires the proxy to inherit "
                f"TOKENSAVER_LOOP_ID.",
                file=sys.stderr,
            )
            return False, None

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
    with_loop: bool = False,
    loop_kind: str = "goal_based",
    loop_max_turns: int | None = 5,
    loop_goal: str | None = None,
) -> int:
    """Run ``argv`` with HTTPS_PROXY pointing at egress; auto-start proxy if needed."""
    if not argv:
        print("Usage: tokensaver-egress run [--keep-proxy] [--no-start] -- CMD [ARGS…]", file=sys.stderr)
        print("   or: tokensaver-egress claude [--loop] [ARGS…]", file=sys.stderr)
        return 2

    # Strip a lone leading "--" from argparse REMAINDER.
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("✖ Missing command to run.", file=sys.stderr)
        return 2

    apply_saved_env(only_if_unset=True)

    if with_loop:
        from tokensaver_egress.business_loop import apply_loop_env, start_business_loop

        try:
            row = start_business_loop(
                kind=loop_kind,
                max_turns=loop_max_turns,
                goal=loop_goal,
            )
        except Exception as exc:
            print(f"✖ Could not start BusinessLoop: {exc}", file=sys.stderr)
            print(
                "  Check TOKENSAVER_API_KEY + TOKENSAVER_INGEST_URL "
                "(setup → Local for localhost:8000).",
                file=sys.stderr,
            )
            return 1
        lid = str(row.get("loop_id") or "")
        kind = str(row.get("loop_kind") or loop_kind)
        apply_loop_env(loop_id=lid, loop_kind=kind, iteration=1, precheck=True)
        print(f"  ✓  BusinessLoop started: {lid} ({kind})", file=sys.stderr)
        print(
            "  ·  Saved ~/.tokensaver-egress/current-loop.env "
            "(tokensaver loop status --local works in any shell)",
            file=sys.stderr,
        )
        print("  ·  Proxy will restart so TOKENSAVER_LOOP_ID is bound.", file=sys.stderr)

    ca = MitmCA()
    if not ca.is_initialized():
        print("  ·  Creating MITM CA …", file=sys.stderr)
        ca.init_ca()
    else:
        ca.ensure_leaves_match_ca()
    port = resolve_port(8888)
    write_client_env(port, ca)
    write_client_unproxy()

    binary = argv[0]
    bin_path = Path(binary)
    missing = shutil.which(binary) is None and not bin_path.is_file()
    if missing and bin_path.name in ("claude", "cursor", "code", "code-insiders", "codium"):
        print(
            f"✖ `{binary}` not found on PATH.\n"
            f"  Install Claude Code / VS Code, then retry:  tokensaver-egress claude | vscode",
            file=sys.stderr,
        )
        return 127

    _sync_ide_proxy_config(argv, port=port, ca=ca)

    we_started, _proc = ensure_proxy(
        port=port,
        start_if_needed=start_if_needed,
        force_restart=with_loop,
    )
    if start_if_needed and not _port_open(port):
        return 1
    if not start_if_needed and not _port_open(port):
        return 1
    if with_loop and not we_started and not (os.environ.get("TOKENSAVER_LOOP_ID") or "").strip():
        return 1

    env = client_environ(port=port, ca=ca)
    # Ensure Claude's process also carries loop env (headers / tooling).
    for k in (
        "TOKENSAVER_LOOP_ID",
        "TOKENSAVER_LOOP_KIND",
        "TOKENSAVER_LOOP_ITERATION",
        "TOKENSAVER_LOOP_PRECHECK",
    ):
        if os.environ.get(k):
            env[k] = os.environ[k]
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
            print("  ✓  Proxy stopped (port free).", file=sys.stderr)
        elif we_started and keep_proxy:
            print(
                f"  ·  Proxy left running (pid file {PID_FILE}).\n"
                "     Stop later:  tokensaver-egress stop\n"
                '     Then clear shell:  eval "$(tokensaver-egress unproxy --sh)"',
                file=sys.stderr,
            )
        # Parent shell may still have HTTPS_PROXY if client-env was sourced earlier
        # (tokensaver-egress never mutates the parent shell — regression footgun).
        proxy_now = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()
        if proxy_now and ("8888" in proxy_now or f":{port}" in proxy_now or "127.0.0.1" in proxy_now):
            print(
                "  !  This shell still has HTTPS_PROXY set — pip/curl will hit the proxy.\n"
                '     Clear with:  eval "$(tokensaver-egress unproxy --sh)"',
                file=sys.stderr,
            )
    return code


def _sync_ide_proxy_config(argv: list[str], *, port: int, ca: MitmCA) -> None:
    """Refresh Claude / VS Code persistent proxy env when launching those clients."""
    name = Path(argv[0]).name.lower()
    if name == "open" or (len(argv) >= 3 and argv[1] in ("-na", "-a")):
        name = "code"
    if name not in ("claude", "code", "code-insiders", "codium", "cursor"):
        return
    try:
        from tokensaver_egress.ide_config import apply_all, preferred_client_from_env
        from tokensaver_egress.wizard import CLIENT_ENV_FILE

        pref = preferred_client_from_env()
        apply_all(
            port=port,
            ca_cert=ca.ca_cert_path,
            preferred=pref,
            client_env=CLIENT_ENV_FILE,
            claude=True,
            vscode=name.startswith("code") or name == "codium" or pref in ("vscode", "both"),
            shell=False,
        )
    except Exception as exc:  # noqa: BLE001 — never block the agent launch
        print(f"  ·  Could not refresh IDE proxy settings: {exc}", file=sys.stderr)


def run_vscode(
    extra: list[str] | None = None,
    *,
    start_if_needed: bool = True,
    keep_proxy: bool = True,
    with_loop: bool = False,
    loop_kind: str = "goal_based",
    loop_max_turns: int | None = 5,
    loop_goal: str | None = None,
) -> int:
    """Start proxy (kept running) and open VS Code with proxy env."""
    from tokensaver_egress.ide_config import find_vscode_command

    cmd = find_vscode_command()
    if not cmd:
        print(
            "✖ VS Code / Cursor not found.\n"
            "  Install Visual Studio Code or Cursor, then retry.\n"
            "  Optional: Command Palette → Shell Command: Install 'code' command in PATH\n"
            "  Or:  tokensaver-egress claude",
            file=sys.stderr,
        )
        return 127
    forwarded = list(extra or [])
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    if cmd[0].endswith("/open") or Path(cmd[0]).name == "open":
        argv = [*cmd, *(["--args", *forwarded] if forwarded else [])]
    else:
        argv = [*cmd, *forwarded]
    print(
        "  ·  VS Code stays open independently — proxy is left running.\n"
        "     Stop later:  tokensaver-egress stop",
        file=sys.stderr,
    )
    return run_with_proxy(
        argv,
        start_if_needed=start_if_needed,
        keep_proxy=keep_proxy,
        with_loop=with_loop,
        loop_kind=loop_kind,
        loop_max_turns=loop_max_turns,
        loop_goal=loop_goal,
    )


def launch_preferred_client(
    *,
    preferred: str | None = None,
    extra: list[str] | None = None,
    start_if_needed: bool = True,
    keep_proxy: bool | None = None,
    with_loop: bool = False,
    loop_kind: str = "goal_based",
    loop_max_turns: int | None = 5,
    loop_goal: str | None = None,
) -> int:
    from tokensaver_egress.ide_config import (
        find_vscode_command,
        normalize_preferred_client,
        preferred_client_from_env,
    )
    from tokensaver_egress.wizard import _prompt_choice, is_interactive

    pref = normalize_preferred_client(preferred or preferred_client_from_env())
    extra = list(extra or [])
    if extra and extra[0] == "--":
        extra = extra[1:]

    if pref == "both" and is_interactive():
        choice = _prompt_choice(
            "  Launch which client?",
            [
                ("1", "Claude Code CLI (`claude`)"),
                ("2", "VS Code (Claude Code extension)"),
            ],
            default="2" if find_vscode_command() else "1",
        )
        pref = "vscode" if choice == "2" else "claude"
    elif pref == "both":
        pref = "vscode" if find_vscode_command() else "claude"
        print(f"  ·  Preferred client is 'both' — launching {pref} (non-interactive).", file=sys.stderr)

    if pref == "vscode":
        return run_vscode(
            extra,
            start_if_needed=start_if_needed,
            keep_proxy=True if keep_proxy is None else keep_proxy,
            with_loop=with_loop,
            loop_kind=loop_kind,
            loop_max_turns=loop_max_turns,
            loop_goal=loop_goal,
        )
    return run_with_proxy(
        ["claude", *extra],
        start_if_needed=start_if_needed,
        keep_proxy=bool(keep_proxy) if keep_proxy is not None else False,
        with_loop=with_loop,
        loop_kind=loop_kind,
        loop_max_turns=loop_max_turns,
        loop_goal=loop_goal,
    )
