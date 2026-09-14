"""Interactive guided setup for first-time / neophyte users."""

from __future__ import annotations

import getpass
import os
import platform
import subprocess
import sys
from pathlib import Path

from tokensaver_egress import __version__
from tokensaver_egress.ca import MitmCA
from tokensaver_egress.plugin_install import (
    install_claude_plugin,
    plugin_install_dir,
    plugin_is_installed,
)

DEFAULT_INGEST_URL = "https://api.tokensaver.fr/api/v1/egress/ingest"
CONFIG_DIR = Path(os.environ.get("EGRESS_CONFIG_DIR", str(Path.home() / ".tokensaver-egress")))
ENV_FILE = CONFIG_DIR / "env"
CLIENT_ENV_FILE = CONFIG_DIR / "client-env.sh"
PLATFORM_URL = "https://platform.tokensaver.fr"

# Keys we persist to ~/.tokensaver-egress/env
_PERSIST_KEYS = (
    "TOKENSAVER_API_KEY",
    "TOKENSAVER_INGEST_URL",
    "EGRESS_MITM_ENABLED",
    "EGRESS_CAPTURE_BODIES",
    "EGRESS_PORT",
)


def is_interactive() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def _out(msg: str = "") -> None:
    print(msg, file=sys.stdout)


def _ok(msg: str) -> None:
    _out(f"  ✓  {msg}")


def _warn(msg: str) -> None:
    _out(f"  !  {msg}")


def _info(msg: str) -> None:
    _out(f"  ·  {msg}")


def _mask_key(value: str) -> str:
    v = (value or "").strip()
    if len(v) <= 8:
        return "***" if v else "(empty)"
    return f"{v[:4]}…{v[-4:]}"


def normalize_api_key(value: str) -> str:
    """Strip whitespace and undo accidental double-paste (``ts_…`` + ``ts_…``)."""
    key = (value or "").strip().strip("'").strip('"')
    if not key:
        return key
    # Exact duplication is a common paste mistake (len often ~100 for a ~50-char key).
    half = len(key) // 2
    if half >= 20 and len(key) % 2 == 0 and key[:half] == key[half:]:
        return key[:half]
    return key


def _prompt(message: str, *, default: str | None = None, secret: bool = False) -> str:
    hint = f" [{default}]" if default not in (None, "") else ""
    label = f"{message}{hint}: "
    if secret:
        raw = getpass.getpass(label)
    else:
        try:
            raw = input(label)
        except EOFError:
            return default or ""
    raw = (raw or "").strip()
    if not raw and default is not None:
        return default
    return raw


def _prompt_yes_no(message: str, *, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        try:
            raw = input(f"{message}{suffix}: ").strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        if raw in ("y", "yes", "o", "oui"):
            return True
        if raw in ("n", "no", "non"):
            return False
        _out("    Please answer y or n.")


def _prompt_choice(message: str, choices: list[tuple[str, str]], *, default: str = "1") -> str:
    """Return the chosen key (usually \"1\"..\"n\")."""
    _out()
    _out(message)
    for key, label in choices:
        _out(f"  {key}) {label}")
    _out()
    valid = {c[0] for c in choices}
    while True:
        try:
            raw = input(f"  Your choice [{default}]: ").strip() or default
        except EOFError:
            return default
        if raw in valid:
            return raw
        _out(f"    Pick one of: {', '.join(sorted(valid))}")


def load_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_FILE
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key:
            data[key] = val
    return data


def apply_saved_env(*, only_if_unset: bool = True) -> dict[str, str]:
    """Load ~/.tokensaver-egress/env into os.environ."""
    data = load_env_file()
    if "TOKENSAVER_API_KEY" in data:
        data["TOKENSAVER_API_KEY"] = normalize_api_key(data["TOKENSAVER_API_KEY"])
    for key, val in data.items():
        if only_if_unset and (os.environ.get(key) or "").strip():
            continue
        os.environ[key] = val
    # Also normalize a key already present in the process env (e.g. double-paste export).
    if (os.environ.get("TOKENSAVER_API_KEY") or "").strip():
        os.environ["TOKENSAVER_API_KEY"] = normalize_api_key(os.environ["TOKENSAVER_API_KEY"])
    return data


def save_env_file(values: dict[str, str], path: Path | None = None) -> Path:
    path = path or ENV_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_env_file(path)
    existing.update({k: v for k, v in values.items() if v is not None})
    lines = [
        "# TokenSaver egress — written by `tokensaver-egress setup`",
        "# source this file:  source ~/.tokensaver-egress/env",
        "",
    ]
    for key in _PERSIST_KEYS:
        if key in existing and existing[key] != "":
            lines.append(f"{key}={existing[key]}")
    for key, val in sorted(existing.items()):
        if key in _PERSIST_KEYS or val == "":
            continue
        lines.append(f"{key}={val}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def write_client_env(port: int, ca: MitmCA) -> Path:
    from tokensaver_egress.proxy_env import write_client_unproxy

    CLIENT_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ca_path = ca.ca_cert_path
    body = f"""# TokenSaver egress — client exports (other terminal)
# source ~/.tokensaver-egress/client-env.sh
#
# When egress is stopped, clear the proxy in this shell:
#   source ~/.tokensaver-egress/client-unproxy.sh

export HTTPS_PROXY=http://127.0.0.1:{port}
export HTTP_PROXY=http://127.0.0.1:{port}
export NODE_EXTRA_CA_CERTS="{ca_path}"
"""
    CLIENT_ENV_FILE.write_text(body, encoding="utf-8")
    write_client_unproxy()
    return CLIENT_ENV_FILE


def status_snapshot(ca: MitmCA | None = None) -> dict:
    ca = ca or MitmCA()
    key = (os.environ.get("TOKENSAVER_API_KEY") or "").strip()
    ingest = (os.environ.get("TOKENSAVER_INGEST_URL") or "").strip()
    mitm = (os.environ.get("EGRESS_MITM_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")
    bodies = (os.environ.get("EGRESS_CAPTURE_BODIES") or "").strip().lower() in ("1", "true", "yes", "on")
    port = (os.environ.get("EGRESS_PORT") or "8888").strip() or "8888"
    return {
        "api_key": key,
        "api_key_set": bool(key),
        "ingest_url": ingest or DEFAULT_INGEST_URL,
        "ingest_set": bool(ingest),
        "mitm": mitm,
        "bodies": bodies,
        "port": port,
        "ca_ok": ca.is_initialized(),
        "ca_path": str(ca.ca_cert_path),
        "env_file": ENV_FILE.is_file(),
    }


def print_status(ca: MitmCA | None = None) -> None:
    s = status_snapshot(ca)
    _out()
    _out("  Current setup")
    _out("  ─────────────")
    if s["api_key_set"]:
        _ok(f"API key     {_mask_key(s['api_key'])}")
    else:
        _warn("API key     missing  (get one at platform.tokensaver.fr)")
    if s["ingest_set"]:
        _ok(f"Ingest URL  {s['ingest_url']}")
    else:
        _info(f"Ingest URL  (default) {DEFAULT_INGEST_URL}")
    if s["ca_ok"]:
        _ok(f"MITM CA     {s['ca_path']}")
    else:
        _warn("MITM CA     not created yet")
    _info(f"MITM mode   {'on' if s['mitm'] else 'off'}")
    _info(f"Bodies      {'on' if s['bodies'] else 'off (metadata only)'}")
    _info(f"Port        {s['port']}")
    if s["env_file"]:
        _ok(f"Saved env   {ENV_FILE}")
    else:
        _info(f"Saved env   (none yet — will create {ENV_FILE})")
    if plugin_is_installed():
        _ok(f"Claude skills  {plugin_install_dir()}")
    else:
        _warn("Claude skills  not installed  (CCR / onboarding for Claude Code)")
    _out()


def ensure_claude_skills() -> Path | None:
    """Install Claude Code skills if missing (no tokensaver-cli required)."""
    _out()
    _out("  Claude Code skills (CCR, onboarding, MCP helpers)")
    _out("  ────────────────────────────────────────────────")
    if plugin_is_installed():
        _ok(f"Already installed: {plugin_install_dir()}")
        if not _prompt_yes_no("  Reinstall / update skills?", default=False):
            return plugin_install_dir()
        dest = install_claude_plugin(force=True)
    else:
        _info("Not found under ~/.claude/skills/tokensaver-router")
        if not _prompt_yes_no("  Install them now? (recommended with Claude Code)", default=True):
            _info("Skipped — later:  tokensaver-egress install-skills")
            return None
        dest = install_claude_plugin(force=False)

    if dest is None:
        _warn("Plugin bundle missing from this install — cannot copy skills.")
        return None
    _ok(f"Installed: {dest}")
    _info("Skills: tokensaver-ccr, tokensaver-onboarding, tokensaver-mcp-tools")
    _info("Restart Claude Code (or open a new session) to load them.")
    return dest


def ensure_api_key() -> str:
    current = normalize_api_key(os.environ.get("TOKENSAVER_API_KEY") or "")
    if current:
        os.environ["TOKENSAVER_API_KEY"] = current
        _ok(f"API key already set ({_mask_key(current)})")
        if not _prompt_yes_no("  Keep this key?", default=True):
            current = ""
    if not current:
        _out()
        _info("Create a key at:  " + PLATFORM_URL)
        _info("It usually looks like:  ts_…")
        while True:
            entered = normalize_api_key(_prompt("  Paste your TOKENSAVER_API_KEY", secret=True))
            if entered:
                os.environ["TOKENSAVER_API_KEY"] = entered
                _ok(f"API key saved for this session ({_mask_key(entered)})")
                return entered
            _warn("A key is required to send audits to TokenSaver.")
            if not _prompt_yes_no("  Try again?", default=True):
                return ""
    return current


def ensure_ingest_url() -> str:
    current = (os.environ.get("TOKENSAVER_INGEST_URL") or "").strip() or DEFAULT_INGEST_URL
    _out()
    _info(f"Default control plane: {DEFAULT_INGEST_URL}")
    entered = _prompt("  TOKENSAVER_INGEST_URL", default=current)
    os.environ["TOKENSAVER_INGEST_URL"] = entered
    _ok(f"Ingest URL → {entered}")
    return entered


def ensure_mitm(ca: MitmCA) -> bool:
    _out()
    _out("  MITM decrypts HTTPS to known LLM hosts so you see model, tokens, tools.")
    _out("  (Requires trusting a local certificate once on this machine.)")
    use = _prompt_yes_no("  Enable MITM? (recommended)", default=True)
    os.environ["EGRESS_MITM_ENABLED"] = "true" if use else "false"
    if not use:
        _info("MITM off — metadata only (host, latency, bytes).")
        return False

    if ca.is_initialized():
        _ok(f"CA already exists: {ca.ca_cert_path}")
        if _prompt_yes_no("  Regenerate a new CA? (rarely needed)", default=False):
            for p in (ca.ca_dir / "ca.key", ca.ca_cert_path):
                try:
                    p.unlink()
                except OSError:
                    pass
            path = ca.init_ca()
            _ok(f"New CA written: {path}")
    else:
        _info("No CA found — creating one now…")
        path = ca.init_ca()
        _ok(f"CA created: {path}")

    _print_trust_instructions(ca)
    if platform.system() == "Darwin":
        if _prompt_yes_no("  Trust this CA in the macOS System keychain now? (needs sudo)", default=False):
            _trust_ca_macos(ca)
    return True


def _print_trust_instructions(ca: MitmCA) -> None:
    path = ca.ca_cert_path
    system = platform.system()
    _out()
    _out("  Trust the CA (one-time)")
    _out("  ───────────────────────")
    if system == "Darwin":
        _out("  macOS:")
        _out(
            "    sudo security add-trusted-cert -d -r trustRoot "
            f"-k /Library/Keychains/System.keychain {path}"
        )
    elif system == "Linux":
        _out("  Linux (example):")
        _out(f"    sudo cp {path} /usr/local/share/ca-certificates/tokensaver-egress.crt")
        _out("    sudo update-ca-certificates")
    else:
        _out(f"  Import this file as a trusted root CA: {path}")
    _out()
    _out("  Always for Node / Claude Code:")
    _out(f'    export NODE_EXTRA_CA_CERTS="{path}"')
    _out()


def _trust_ca_macos(ca: MitmCA) -> None:
    cmd = [
        "sudo",
        "security",
        "add-trusted-cert",
        "-d",
        "-r",
        "trustRoot",
        "-k",
        "/Library/Keychains/System.keychain",
        str(ca.ca_cert_path),
    ]
    _info("Running: " + " ".join(cmd))
    try:
        subprocess.run(cmd, check=False)
        _ok("Keychain command finished (enter your password if prompted).")
    except FileNotFoundError:
        _warn("`security` not found — run the command manually.")


def ensure_bodies() -> bool:
    _out()
    _out("  Capture request/response bodies in TokenSaver? (auth headers stay masked)")
    use = _prompt_yes_no("  Enable body capture?", default=True)
    os.environ["EGRESS_CAPTURE_BODIES"] = "1" if use else "0"
    return use


def ensure_port() -> int:
    current = (os.environ.get("EGRESS_PORT") or "8888").strip() or "8888"
    _out()
    while True:
        raw = _prompt("  Proxy port", default=current)
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                os.environ["EGRESS_PORT"] = str(port)
                return port
        except ValueError:
            pass
        _warn("Enter a number between 1 and 65535.")


def print_client_howto(port: int, ca: MitmCA, *, mitm: bool) -> None:
    from tokensaver_egress.proxy_env import CLIENT_UNPROXY_FILE

    path = write_client_env(port, ca)
    _out()
    _out("  Next: point your tools at the proxy (other terminal)")
    _out("  ────────────────────────────────────────────────────")
    _out(f"    source {path}")
    _out("    # or manually:")
    _out(f"    export HTTPS_PROXY=http://127.0.0.1:{port}")
    _out(f"    export HTTP_PROXY=http://127.0.0.1:{port}")
    if mitm:
        _out(f'    export NODE_EXTRA_CA_CERTS="{ca.ca_cert_path}"')
    _out("    claude   # or your agent / curl")
    _out()
    _out("  After stopping egress (Ctrl+C), clear the proxy in that shell:")
    _out(f"    source {CLIENT_UNPROXY_FILE}")
    _out('    # or:  eval "$(tokensaver-egress unproxy --sh)"')
    _out()
    _out(f"  Then open Flux IA → {PLATFORM_URL}")
    _out()


def persist_current_config(port: int) -> Path:
    values = {
        "TOKENSAVER_API_KEY": normalize_api_key(os.environ.get("TOKENSAVER_API_KEY") or ""),
        "TOKENSAVER_INGEST_URL": (os.environ.get("TOKENSAVER_INGEST_URL") or "").strip()
        or DEFAULT_INGEST_URL,
        "EGRESS_MITM_ENABLED": (os.environ.get("EGRESS_MITM_ENABLED") or "false").strip() or "false",
        "EGRESS_CAPTURE_BODIES": (os.environ.get("EGRESS_CAPTURE_BODIES") or "0").strip() or "0",
        "EGRESS_PORT": str(port),
    }
    path = save_env_file(values)
    _ok(f"Config saved to {path}")
    _info(f"Reload later with:  source {path}")
    return path


def run_guided_setup(*, start_after: bool = True) -> int:
    """Full beginner flow. Returns process exit code (serve may block)."""
    from tokensaver_egress.serve_cmd import run_serve

    apply_saved_env(only_if_unset=True)
    ca = MitmCA()

    _out()
    _out("  Guided setup — TokenSaver egress")
    _out("  ════════════════════════════════")
    _out("  We'll walk through API key → certificate → options → start.")
    print_status(ca)

    ensure_api_key()
    ensure_ingest_url()
    mitm = ensure_mitm(ca)
    ensure_bodies()
    port = ensure_port()
    ensure_claude_skills()
    persist_current_config(port)
    print_client_howto(port, ca, mitm=mitm)

    if not start_after:
        _ok("Setup complete. Start later with:  tokensaver-egress serve")
        return 0

    if not _prompt_yes_no("  Start the proxy now?", default=True):
        _info("OK — when ready:  tokensaver-egress serve")
        return 0

    _out()
    _ok(f"Starting proxy on 0.0.0.0:{port} …  (Ctrl+C to stop)")
    _out()
    return run_serve(host="0.0.0.0", port=port, load_saved_env=False)


def run_ca_only() -> int:
    apply_saved_env(only_if_unset=True)
    ca = MitmCA()
    _out()
    _out("  Certificate (MITM CA)")
    _out("  ─────────────────────")
    if ca.is_initialized():
        _ok(f"CA present: {ca.ca_cert_path}")
        if _prompt_yes_no("  Recreate it?", default=False):
            for p in (ca.ca_dir / "ca.key", ca.ca_cert_path):
                try:
                    p.unlink()
                except OSError:
                    pass
            _ok(f"Created: {ca.init_ca()}")
    else:
        _ok(f"Created: {ca.init_ca()}")
    _print_trust_instructions(ca)
    if platform.system() == "Darwin" and _prompt_yes_no(
        "  Trust in macOS System keychain now?", default=False
    ):
        _trust_ca_macos(ca)
    return 0


def run_quick_start() -> int:
    """Start with saved/current env; offer minimal prompts if incomplete."""
    from tokensaver_egress.serve_cmd import run_serve

    apply_saved_env(only_if_unset=True)
    ca = MitmCA()
    print_status(ca)
    s = status_snapshot(ca)

    if not s["api_key_set"]:
        _warn("No API key — run guided setup first.")
        if _prompt_yes_no("  Run guided setup now?", default=True):
            return run_guided_setup(start_after=True)
        return 1

    if s["mitm"] and not s["ca_ok"]:
        _warn("MITM is on but CA is missing.")
        if _prompt_yes_no("  Create the CA now?", default=True):
            ca.init_ca()
            _ok(f"CA: {ca.ca_cert_path}")
            _print_trust_instructions(ca)
        else:
            return 1

    port = int(s["port"])
    if not (os.environ.get("TOKENSAVER_INGEST_URL") or "").strip():
        os.environ["TOKENSAVER_INGEST_URL"] = DEFAULT_INGEST_URL

    print_client_howto(port, ca, mitm=s["mitm"] or ca.is_initialized())
    _out()
    _ok(f"Starting proxy on 0.0.0.0:{port} …  (Ctrl+C to stop)")
    _out()
    return run_serve(host="0.0.0.0", port=port, load_saved_env=False)


def run_menu() -> int:
    """Top-level interactive menu (TTY)."""
    apply_saved_env(only_if_unset=True)
    ca = MitmCA()

    _out(f"  Welcome — interactive helper (v{__version__})")
    print_status(ca)

    while True:
        choice = _prompt_choice(
            "  What do you want to do?",
            [
                ("1", "Guided setup & start  (recommended for first time)"),
                ("2", "Start proxy now       (use saved config)"),
                ("3", "Create / check certificate (MITM CA)"),
                ("4", "Install Claude Code skills (if missing)"),
                ("5", "Clear client proxy env (unproxy)"),
                ("6", "Show status"),
                ("7", "Show classic help"),
                ("8", "Quit"),
            ],
            default="1",
        )
        if choice == "1":
            return run_guided_setup(start_after=True)
        if choice == "2":
            return run_quick_start()
        if choice == "3":
            run_ca_only()
            continue
        if choice == "4":
            ensure_claude_skills()
            continue
        if choice == "5":
            from tokensaver_egress.proxy_env import run_unproxy_cmd

            run_unproxy_cmd(sh_only=False)
            continue
        if choice == "6":
            apply_saved_env(only_if_unset=True)
            print_status(MitmCA())
            continue
        if choice == "7":
            from tokensaver_egress.__main__ import print_help

            print_help()
            continue
        if choice == "8":
            _out("  Bye.")
            return 0
