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
LOCAL_INGEST_URL = "http://localhost:8000/api/v1/egress/ingest"
CONFIG_DIR = Path(os.environ.get("EGRESS_CONFIG_DIR", str(Path.home() / ".tokensaver-egress")))
ENV_FILE = CONFIG_DIR / "env"
CLIENT_ENV_FILE = CONFIG_DIR / "client-env.sh"
PLATFORM_URL = "https://platform.tokensaver.fr"

# Keys we persist to ~/.tokensaver-egress/env
_PERSIST_KEYS = (
    "TOKENSAVER_API_KEY",
    "TOKENSAVER_INGEST_URL",
    "TOKENSAVER_LOOP_PRECHECK",
    "EGRESS_MITM_ENABLED",
    "EGRESS_CAPTURE_BODIES",
    "EGRESS_PORT",
)

# Always take these from the egress config file when present — otherwise a shell
# export / monorepo ``.env`` (Cursor) silently wins over ``tokensaver-egress setup``.
_FILE_WINS_KEYS = frozenset({"TOKENSAVER_API_KEY", "TOKENSAVER_INGEST_URL"})


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
    """Load ~/.tokensaver-egress/env into os.environ.

    ``TOKENSAVER_API_KEY`` and ``TOKENSAVER_INGEST_URL`` always win from the
    file when set there, so ``setup`` key changes are not masked by a leftover
    shell export (e.g. monorepo ``.env`` in Cursor).
    """
    data = load_env_file()
    if "TOKENSAVER_API_KEY" in data:
        data["TOKENSAVER_API_KEY"] = normalize_api_key(data["TOKENSAVER_API_KEY"])
    for key, val in data.items():
        if key in _FILE_WINS_KEYS and str(val).strip():
            os.environ[key] = val
            continue
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
    no_proxy = (
        "127.0.0.1,localhost,::1,"
        "api.tokensaver.fr,platform.tokensaver.fr,"
        "mcp.tokensaver.fr,gateway.tokensaver.fr"
    )
    body = f"""# TokenSaver egress — client exports (other terminal)
# Prefer:  tokensaver-egress claude --no-start
# Or:      source ~/.tokensaver-egress/client-env.sh && claude
#
# When egress is stopped, clear the proxy in this shell:
#   source ~/.tokensaver-egress/client-unproxy.sh

export HTTPS_PROXY=http://127.0.0.1:{port}
export HTTP_PROXY=http://127.0.0.1:{port}
export NODE_EXTRA_CA_CERTS="{ca_path}"
export NO_PROXY="{no_proxy}"
export no_proxy="{no_proxy}"
"""
    # If a BusinessLoop is active, expose it here too (other terminal / Claude).
    loop_id = (os.environ.get("TOKENSAVER_LOOP_ID") or "").strip()
    if loop_id:
        kind = (os.environ.get("TOKENSAVER_LOOP_KIND") or "goal_based").strip()
        it = (os.environ.get("TOKENSAVER_LOOP_ITERATION") or "1").strip() or "1"
        pre = (os.environ.get("TOKENSAVER_LOOP_PRECHECK") or "").strip()
        body += f"""
# Active BusinessLoop (from claude --loop)
export TOKENSAVER_LOOP_ID="{loop_id}"
export TOKENSAVER_LOOP_KIND="{kind}"
export TOKENSAVER_LOOP_ITERATION="{it}"
"""
        if pre in ("1", "true", "yes", "on"):
            body += 'export TOKENSAVER_LOOP_PRECHECK=1\n'
    else:
        body += """
# Optional: source ~/.tokensaver-egress/current-loop.env after claude --loop
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
        _warn("API key     missing  (create an account in setup, or paste a ts_… key)")
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
    file_key = normalize_api_key(load_env_file().get("TOKENSAVER_API_KEY") or "")
    shell_key = normalize_api_key(os.environ.get("TOKENSAVER_API_KEY") or "")
    # Prefer the egress config file (source of truth after setup).
    current = file_key or shell_key
    if file_key and shell_key and file_key != shell_key:
        _warn(
            "Shell TOKENSAVER_API_KEY differs from ~/.tokensaver-egress/env — "
            "setup uses the file; serve will too after this fix."
        )
        _info(f"  file:  {_mask_key(file_key)}")
        _info(f"  shell: {_mask_key(shell_key)}")
    if current:
        os.environ["TOKENSAVER_API_KEY"] = current
        _ok(f"API key already set ({_mask_key(current)})")
        if not _prompt_yes_no("  Keep this key?", default=True):
            current = ""
    if not current:
        current = _obtain_api_key_interactive()
    return current


def is_enterprise_egress_mode() -> bool:
    """Hide public signup when org is enterprise (P4).

    Triggers:
    - TOKENSAVER_ENTERPRISE=1|true|yes
    - TOKENSAVER_ORG_HINT set (slug / domain hint)
    """
    flag = (os.environ.get("TOKENSAVER_ENTERPRISE") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    hint = (os.environ.get("TOKENSAVER_ORG_HINT") or "").strip()
    return bool(hint)


def _obtain_api_key_interactive() -> str:
    """First-time path: signup, login, paste key, or open the console."""
    _out()
    if is_enterprise_egress_mode():
        hint = (os.environ.get("TOKENSAVER_ORG_HINT") or "").strip()
        _info("Enterprise mode — public signup is disabled for this tenant.")
        if hint:
            _info(f"  TOKENSAVER_ORG_HINT={hint}")
        _info("Log in (local password) or paste a ts_… key from the console after SSO.")
        choice = _prompt_choice(
            "  How do you want to continue?",
            [
                ("1", "Log in to an existing account  (email + password)"),
                ("2", "Paste an existing TOKENSAVER_API_KEY"),
                ("3", f"Open {PLATFORM_URL} in the browser"),
            ],
            default="1",
        )
        if choice == "1":
            key = _login_for_api_key()
            if key:
                return key
            _warn("Login did not finish — you can paste a key instead.")
        elif choice == "3":
            _info("Create a key at:  " + PLATFORM_URL)
            try:
                import webbrowser

                webbrowser.open(PLATFORM_URL)
                _ok("Opened the console in your browser.")
            except Exception:
                pass
        return _prompt_paste_api_key()

    choice = _prompt_choice(
        "  No API key yet — how do you want to continue?",
        [
            ("1", "Create a free TokenSaver account  (new users)"),
            ("2", "Log in to an existing account  (email + password)"),
            ("3", "Paste an existing TOKENSAVER_API_KEY"),
            ("4", f"Open {PLATFORM_URL} in the browser"),
        ],
        default="2",
    )
    if choice == "1":
        key = _signup_for_api_key()
        if key:
            return key
        _warn("Account setup did not finish — try login or paste a key.")
    elif choice == "2":
        key = _login_for_api_key()
        if key:
            return key
        _warn("Login did not finish — you can paste a key instead.")
    elif choice == "4":
        _info("Create a key at:  " + PLATFORM_URL)
        try:
            import webbrowser

            webbrowser.open(PLATFORM_URL)
            _ok("Opened the console in your browser.")
        except Exception:
            pass

    return _prompt_paste_api_key()


def _prompt_paste_api_key() -> str:
    _out()
    _info("Paste a key that looks like:  ts_…")
    while True:
        entered = normalize_api_key(_prompt("  TOKENSAVER_API_KEY", secret=True))
        if entered:
            os.environ["TOKENSAVER_API_KEY"] = entered
            _ok(f"API key set ({_mask_key(entered)}) — will write ~/.tokensaver-egress/env")
            return entered
        _warn("A key is required to send audits to TokenSaver.")
        if not _prompt_yes_no("  Try again?", default=True):
            return ""


def _prompt_account_target() -> str:
    """Return API origin (SaaS or local) and seed ingest URL defaults."""
    from tokensaver_egress.signup import DEFAULT_API_ORIGIN, LOCAL_API_ORIGIN

    current_ingest = (os.environ.get("TOKENSAVER_INGEST_URL") or "").strip()
    default_target = (
        "2"
        if current_ingest and ("localhost" in current_ingest or "127.0.0.1" in current_ingest)
        else "1"
    )
    target = _prompt_choice(
        "  Which TokenSaver platform?",
        [
            ("1", f"TokenSaver SaaS   ({DEFAULT_API_ORIGIN})"),
            ("2", f"Local platform    ({LOCAL_API_ORIGIN})"),
        ],
        default=default_target,
    )
    if target == "1":
        os.environ.setdefault("TOKENSAVER_INGEST_URL", DEFAULT_INGEST_URL)
        return DEFAULT_API_ORIGIN
    os.environ.setdefault("TOKENSAVER_INGEST_URL", LOCAL_INGEST_URL)
    return LOCAL_API_ORIGIN


def _login_for_api_key() -> str:
    """Interactive login → create egress API key → returns plain key or \"\"."""
    from tokensaver_egress.signup import is_plausible_email, login_and_create_api_key

    _out()
    _out("  Log in to an existing TokenSaver account")
    _out("  ───────────────────────────────────────")
    _info("We'll sign you in and create an egress API key (ts_…).")

    api_origin = _prompt_account_target()

    while True:
        email = _prompt("  Email").strip().lower()
        if is_plausible_email(email):
            break
        _warn("Please enter a valid email address.")
    password = _prompt("  Password", secret=True)
    if not password:
        _warn("Password is required.")
        return ""

    _info(f"Signing in on {api_origin}…")
    try:
        data = login_and_create_api_key(
            email=email,
            password=password,
            api_origin=api_origin,
            key_label="Egress CLI",
        )
    except Exception as exc:
        _warn(str(exc))
        low = str(exc).lower()
        if "sso" in low:
            _info("SSO organisations: open the console after IdP login and paste a ts_… key.")
        elif "already" not in low:
            _info("You can also paste an existing TOKENSAVER_API_KEY from Settings → API keys.")
        return ""

    key = normalize_api_key(str(data.get("api_key_plain") or ""))
    if not key:
        _warn("Login OK but no API key was returned.")
        return ""

    os.environ["TOKENSAVER_API_KEY"] = key
    _ok(f"Signed in as {email}")
    _ok(f"API key ready ({_mask_key(key)}) — saved in the next step")
    _info(f"Console: {PLATFORM_URL}")
    return key


def _signup_for_api_key() -> str:
    """Interactive signup → returns api_key_plain or \"\"."""
    from tokensaver_egress.signup import (
        create_account,
        is_plausible_email,
    )

    _out()
    _out("  Create a free TokenSaver account")
    _out("  ────────────────────────────────")
    _info("We'll create your org + workspace and generate an API key for egress.")

    api_origin = _prompt_account_target()

    name = _prompt("  Your name", default="")
    while True:
        email = _prompt("  Email").strip().lower()
        if is_plausible_email(email):
            break
        _warn("Please enter a valid email address.")
    while True:
        password = _prompt("  Password (min 8 characters)", secret=True)
        if len(password.encode("utf-8")) < 8:
            _warn("Password too short.")
            continue
        if len(password.encode("utf-8")) > 72:
            _warn("Password must be at most 72 bytes.")
            continue
        confirm = _prompt("  Confirm password", secret=True)
        if password != confirm:
            _warn("Passwords do not match.")
            continue
        break
    org = _prompt("  Organisation name", default="My organisation")

    _info(f"Creating account on {api_origin}…")
    try:
        data = create_account(
            email=email,
            password=password,
            name=name,
            organisation_name=org or None,
            api_origin=api_origin,
        )
    except Exception as exc:
        _warn(str(exc))
        if "already exists" in str(exc).lower():
            _info("If you already have an account, choose Log in (option 2) or paste a key.")
        return ""

    key = normalize_api_key(str(data.get("api_key_plain") or ""))
    if not key:
        _warn("Account created but no API key was returned.")
        return ""

    os.environ["TOKENSAVER_API_KEY"] = key
    _ok(f"Account created for {email}")
    _ok(f"API key ready ({_mask_key(key)}) — saved in the next step")
    _info(f"Console: {PLATFORM_URL}")
    if data.get("email_verified") is False:
        _info("Check your inbox to verify your email when prompted.")
    return key


def ensure_ingest_url() -> str:
    current = (os.environ.get("TOKENSAVER_INGEST_URL") or "").strip() or DEFAULT_INGEST_URL
    default_choice = "2" if "localhost" in current or "127.0.0.1" in current else "1"
    _out()
    choice = _prompt_choice(
        "  Where should audits go?",
        [
            ("1", f"TokenSaver SaaS   ({DEFAULT_INGEST_URL})"),
            ("2", f"Local platform    ({LOCAL_INGEST_URL})"),
            ("3", "Custom URL"),
        ],
        default=default_choice,
    )
    if choice == "1":
        entered = DEFAULT_INGEST_URL
    elif choice == "2":
        entered = LOCAL_INGEST_URL
    else:
        entered = _prompt("  TOKENSAVER_INGEST_URL", default=current)
    os.environ["TOKENSAVER_INGEST_URL"] = entered.strip() or DEFAULT_INGEST_URL
    _ok(f"Ingest URL → {os.environ['TOKENSAVER_INGEST_URL']}")
    return os.environ["TOKENSAVER_INGEST_URL"]


def ensure_loop_precheck() -> bool:
    """Persist soft precheck when a BusinessLoop id is later set (skills / advanced)."""
    _out()
    _out("  Soft precheck if a BusinessLoop id is present (skills / advanced).")
    _out("  Day-to-day: leave this on — you do not need ``claude --loop``.")
    use = _prompt_yes_no("  Enable TOKENSAVER_LOOP_PRECHECK by default?", default=True)
    os.environ["TOKENSAVER_LOOP_PRECHECK"] = "1" if use else "0"
    return use


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
    _out("  Recommended (two terminals):")
    _out("  ────────────────────────────")
    _out(f"    # Terminal 1 — proxy logs on :{port}")
    _out("    tokensaver-egress serve")
    _out("    # Terminal 2 — Claude through that proxy")
    _out("    tokensaver-egress claude --no-start")
    _out()
    _out("  One-command alternative (no log window):")
    _out("  ───────────────────────────────────────")
    _out("    tokensaver-egress claude")
    _out("    # or:  tokensaver-egress run -- curl -I https://example.com")
    _out()
    _out("  Manual client env (advanced):")
    _out("  ─────────────────────────────")
    _out(f"    source {path}")
    if mitm:
        _out(f"    # NODE_EXTRA_CA_CERTS → {ca.ca_cert_path}")
    _out("    claude")
    _out()
    _out("  Clear proxy env in a client shell:")
    _out(f"    source {CLIENT_UNPROXY_FILE}")
    _out("    # or:  eval \"$(tokensaver-egress unproxy --sh)\"")
    _out()
    _out(f"  Then open Flux IA → {PLATFORM_URL}")
    _out()


def persist_current_config(port: int) -> Path:
    values = {
        "TOKENSAVER_API_KEY": normalize_api_key(os.environ.get("TOKENSAVER_API_KEY") or ""),
        "TOKENSAVER_INGEST_URL": (os.environ.get("TOKENSAVER_INGEST_URL") or "").strip()
        or DEFAULT_INGEST_URL,
        "TOKENSAVER_LOOP_PRECHECK": (os.environ.get("TOKENSAVER_LOOP_PRECHECK") or "1").strip()
        or "1",
        "EGRESS_MITM_ENABLED": (os.environ.get("EGRESS_MITM_ENABLED") or "false").strip() or "false",
        "EGRESS_CAPTURE_BODIES": (os.environ.get("EGRESS_CAPTURE_BODIES") or "0").strip() or "0",
        "EGRESS_PORT": str(port),
    }
    path = save_env_file(values)
    _ok(f"Config saved to {path}")
    _info(f"Active key: {_mask_key(values['TOKENSAVER_API_KEY'])}")
    _info(f"Reload later with:  source {path}")
    _info("serve / claude load this file (API key + ingest URL win over shell exports).")
    return path


def run_guided_setup(*, start_after: bool = True) -> int:
    """Full beginner flow. Returns process exit code (may launch Claude)."""
    apply_saved_env(only_if_unset=True)
    ca = MitmCA()

    _out()
    _out("  Guided setup — TokenSaver egress")
    _out("  ════════════════════════════════")
    _out("  We'll walk through account/API key → certificate → options.")
    print_status(ca)

    ensure_api_key()
    ensure_ingest_url()
    ensure_loop_precheck()
    mitm = ensure_mitm(ca)
    ensure_bodies()
    port = ensure_port()
    ensure_claude_skills()
    persist_current_config(port)
    print_client_howto(port, ca, mitm=mitm)

    if not start_after:
        _ok("Setup complete. Next (two terminals):")
        _info("  tokensaver-egress serve")
        _info("  tokensaver-egress claude --no-start")
        return 0

    if not _prompt_yes_no("  Launch Claude Code through the proxy now?", default=True):
        _info("OK — when ready (two terminals):")
        _info("  tokensaver-egress serve")
        _info("  tokensaver-egress claude --no-start")
        return 0

    from tokensaver_egress.run_cmd import run_with_proxy

    _out()
    _ok(f"Launching Claude (proxy on :{port} if needed)…")
    _out()
    return run_with_proxy(
        ["claude"],
        start_if_needed=True,
        keep_proxy=False,
        with_loop=False,
    )


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
                ("2", "Launch Claude Code through the proxy  (easiest)"),
                ("3", "Start proxy only      (advanced / other tools)"),
                ("4", "Create / check certificate (MITM CA)"),
                ("5", "Install Claude Code skills (if missing)"),
                ("6", "Clear client proxy env (unproxy)"),
                ("7", "Show status"),
                ("8", "Show classic help"),
                ("9", "Quit"),
            ],
            default="2",
        )
        if choice == "1":
            return run_guided_setup(start_after=True)
        if choice == "2":
            from tokensaver_egress.run_cmd import run_with_proxy

            return run_with_proxy(["claude"])
        if choice == "3":
            return run_quick_start()
        if choice == "4":
            run_ca_only()
            continue
        if choice == "5":
            ensure_claude_skills()
            continue
        if choice == "6":
            from tokensaver_egress.proxy_env import run_unproxy_cmd

            run_unproxy_cmd(sh_only=False)
            continue
        if choice == "7":
            apply_saved_env(only_if_unset=True)
            print_status(MitmCA())
            continue
        if choice == "8":
            from tokensaver_egress.__main__ import print_help

            print_help()
            continue
        if choice == "9":
            _out("  Bye.")
            return 0
