"""Persist HTTPS proxy env for Claude Code CLI, VS Code, and interactive shells.

Claude Code in VS Code does not inherit ``tokensaver-egress claude`` child env.
Official hooks:

- ``~/.claude/settings.json`` ``env`` (CLI + some IDE sessions)
- VS Code ``claudeCode.environmentVariables`` (extension login / spawn)
- Optional guarded snippet in ``~/.zshrc`` / ``~/.bashrc`` (only if :port is up)

Skip Claude ``settings.json`` proxy injection when TokenSaver **route** is active
(``ANTHROPIC_BASE_URL`` points at TokenSaver) — that path must not go through
a dead local MITM.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

MANAGED_ENV_KEYS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "https_proxy",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NODE_EXTRA_CA_CERTS",
    "NO_PROXY",
    "no_proxy",
    # Agentic capture side-channel (Claude OTEL + hooks forward).
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA",
    "TOKENSAVER_CLAUDE_HOOKS_URL",
)

PREFERRED_CLIENTS = ("claude", "vscode", "both")

SHELL_BEGIN = "# >>> tokensaver-egress >>>"
SHELL_END = "# <<< tokensaver-egress <<<"

_VSCODE_ENV_SETTING = "claudeCode.environmentVariables"

_NO_PROXY = (
    "127.0.0.1,localhost,::1,"
    "api.tokensaver.fr,platform.tokensaver.fr,"
    "mcp.tokensaver.fr,gateway.tokensaver.fr"
)


def proxy_url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def proxy_env_values(*, port: int, ca_cert: Path | str) -> dict[str, str]:
    url = proxy_url(port)
    return {
        "HTTPS_PROXY": url,
        "HTTP_PROXY": url,
        "NODE_EXTRA_CA_CERTS": str(ca_cert),
        "NO_PROXY": _NO_PROXY,
    }


def normalize_preferred_client(raw: str | None) -> str:
    val = (raw or "").strip().lower()
    if val in ("code", "vs-code", "vs_code", "visual-studio-code"):
        return "vscode"
    if val in PREFERRED_CLIENTS:
        return val
    return "claude"


def preferred_client_from_env() -> str:
    return normalize_preferred_client(os.environ.get("EGRESS_PREFERRED_CLIENT"))


def claude_settings_path() -> Path:
    override = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    root = Path(override).expanduser() if override else Path.home() / ".claude"
    return root / "settings.json"


def vscode_user_settings_paths() -> list[Path]:
    """User ``settings.json`` for VS Code / Insiders / VSCodium if the app dir exists."""
    home = Path.home()
    system = sys.platform
    names = (
        "Code",
        "Code - Insiders",
        "VSCodium",
        "Cursor",
    )
    out: list[Path] = []
    if system == "darwin":
        base = home / "Library" / "Application Support"
        candidates = [base / n / "User" / "settings.json" for n in names]
    elif system == "win32":
        appdata = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
        candidates = [appdata / n / "User" / "settings.json" for n in names]
    else:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
        candidates = [config / n / "User" / "settings.json" for n in names]
    for path in candidates:
        if path.parent.is_dir() or path.is_file():
            out.append(path)
    return out


def find_vscode_binary() -> Path | None:
    """CLI binary inside an editor .app / PATH. None when only ``open -a`` is available."""
    cmd = find_vscode_command()
    if not cmd or Path(cmd[0]).name == "open":
        return None
    return Path(cmd[0])


def _editor_app_roots() -> tuple[Path, ...]:
    """System/user Applications folders, plus extra macOS volume Application dirs."""
    roots: list[Path] = [Path("/Applications"), Path.home() / "Applications"]
    if sys.platform == "darwin":
        try:
            for vol in Path("/Volumes").iterdir():
                extra = vol / "Applications"
                if extra.is_dir():
                    roots.append(extra)
        except OSError:
            pass
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        try:
            key = str(root.resolve()) if root.exists() else str(root)
        except OSError:
            key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return tuple(out)


def _darwin_editor_cli_paths() -> list[Path]:
    apps = (
        "Visual Studio Code.app/Contents/Resources/app/bin/code",
        "Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code",
        "Cursor.app/Contents/Resources/app/bin/cursor",
        "VSCodium.app/Contents/Resources/app/bin/codium",
    )
    return [root / rel for root in _editor_app_roots() for rel in apps]


def find_vscode_command() -> list[str] | None:
    """Argv to start VS Code / Cursor / VSCodium (PATH, .app bundle, or ``open -a``)."""
    for name in ("code", "code-insiders", "cursor", "codium"):
        found = shutil.which(name)
        if found:
            return [found]
    if sys.platform == "darwin":
        for path in _darwin_editor_cli_paths():
            if path.is_file():
                return [str(path)]
        opener = shutil.which("open")
        if opener:
            for app_name in (
                "Visual Studio Code",
                "Visual Studio Code - Insiders",
                "Cursor",
                "VSCodium",
            ):
                for root in _editor_app_roots():
                    if (root / f"{app_name}.app").is_dir():
                        return [opener, "-na", app_name]
    return None


def looks_like_tokensaver_route(env: dict[str, Any] | None) -> bool:
    if not env:
        return False
    base = str(env.get("ANTHROPIC_BASE_URL") or "").lower()
    return "tokensaver" in base


def _strip_jsonc(text: str) -> str:
    """Drop // and /* */ comments and trailing commas enough for json.loads."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    escape = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            continue
        out.append(ch)
        i += 1
    cleaned = "".join(out)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def load_json_file(path: Path) -> tuple[dict[str, Any], bool]:
    """Return (object, used_jsonc_fallback). Missing file → empty dict."""
    if not path.is_file():
        return {}, False
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}, False
    try:
        data = json.loads(raw)
        return (data if isinstance(data, dict) else {}), False
    except json.JSONDecodeError:
        data = json.loads(_strip_jsonc(raw))
        if not isinstance(data, dict):
            return {}, True
        return data, True


def dump_json_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_env_block(existing: dict[str, Any] | None, values: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(existing, dict):
        for key, val in existing.items():
            if key in MANAGED_ENV_KEYS:
                continue
            out[str(key)] = "" if val is None else str(val)
    out.update(values)
    return out


def capture_env_values() -> dict[str, str]:
    """OTEL + hooks URL for Claude Code (agentic-graph P0). Safe when capture disabled."""
    try:
        from tokensaver_egress.claude_capture import (
            capture_enabled,
            client_environ_for_claude_capture,
        )

        if not capture_enabled():
            return {}
        return client_environ_for_claude_capture()
    except Exception:
        return {}


def apply_claude_settings(*, port: int, ca_cert: Path | str, path: Path | None = None) -> str:
    """Merge proxy + capture env into Claude settings. Returns applied|skipped_route|error:..."""
    target = path or claude_settings_path()
    try:
        data, _jsonc = load_json_file(target)
    except (OSError, json.JSONDecodeError) as exc:
        return f"error:{exc}"
    current_env = data.get("env") if isinstance(data.get("env"), dict) else {}
    if looks_like_tokensaver_route(current_env):
        return "skipped_route"
    merged = proxy_env_values(port=port, ca_cert=ca_cert)
    merged.update(capture_env_values())
    data["env"] = merge_env_block(current_env, merged)
    dump_json_file(target, data)
    return "applied"


def clear_claude_settings(*, path: Path | None = None) -> str:
    target = path or claude_settings_path()
    if not target.is_file():
        return "absent"
    try:
        data, _ = load_json_file(target)
    except (OSError, json.JSONDecodeError) as exc:
        return f"error:{exc}"
    env = data.get("env")
    if not isinstance(env, dict):
        return "absent"
    changed = False
    for key in MANAGED_ENV_KEYS:
        if key in env:
            env.pop(key, None)
            changed = True
    if not env:
        data.pop("env", None)
        changed = True
    else:
        data["env"] = env
    if changed:
        dump_json_file(target, data)
        return "cleared"
    return "absent"


def _vscode_env_list(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in values.items()]


def merge_vscode_env_list(existing: Any, values: dict[str, str]) -> list[dict[str, str]]:
    kept: list[dict[str, str]] = []
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if name in MANAGED_ENV_KEYS or name in values:
                continue
            kept.append({"name": name, "value": str(item.get("value") or "")})
    return kept + _vscode_env_list(values)


def vscode_env_list_is_routed(existing: Any) -> bool:
    if not isinstance(existing, list):
        return False
    env: dict[str, Any] = {}
    for item in existing:
        if isinstance(item, dict) and item.get("name"):
            env[str(item["name"])] = item.get("value")
    return looks_like_tokensaver_route(env)


def apply_vscode_settings(*, port: int, ca_cert: Path | str, paths: list[Path] | None = None) -> list[tuple[Path, str]]:
    values = proxy_env_values(port=port, ca_cert=ca_cert)
    results: list[tuple[Path, str]] = []
    targets = paths if paths is not None else vscode_user_settings_paths()
    if not targets:
        return results
    for target in targets:
        try:
            data, _ = load_json_file(target)
            if vscode_env_list_is_routed(data.get(_VSCODE_ENV_SETTING)):
                results.append((target, "skipped_route"))
                continue
            data[_VSCODE_ENV_SETTING] = merge_vscode_env_list(data.get(_VSCODE_ENV_SETTING), values)
            dump_json_file(target, data)
            results.append((target, "applied"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append((target, f"error:{exc}"))
    return results


def clear_vscode_settings(*, paths: list[Path] | None = None) -> list[tuple[Path, str]]:
    results: list[tuple[Path, str]] = []
    targets = paths if paths is not None else vscode_user_settings_paths()
    for target in targets:
        if not target.is_file():
            results.append((target, "absent"))
            continue
        try:
            data, _ = load_json_file(target)
            existing = data.get(_VSCODE_ENV_SETTING)
            if not isinstance(existing, list):
                results.append((target, "absent"))
                continue
            kept = [
                item
                for item in existing
                if isinstance(item, dict) and str(item.get("name") or "") not in MANAGED_ENV_KEYS
            ]
            if kept:
                data[_VSCODE_ENV_SETTING] = kept
            else:
                data.pop(_VSCODE_ENV_SETTING, None)
            dump_json_file(target, data)
            results.append((target, "cleared"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append((target, f"error:{exc}"))
    return results


def shell_rc_candidates() -> list[Path]:
    home = Path.home()
    shell = Path(os.environ.get("SHELL") or "").name
    ordered: list[Path] = []
    if shell == "zsh" or not shell:
        ordered.extend([home / ".zshrc", home / ".zprofile"])
    if shell == "bash":
        ordered.extend([home / ".bashrc", home / ".bash_profile", home / ".profile"])
    if shell not in ("zsh", "bash"):
        ordered.extend([home / ".zshrc", home / ".bashrc", home / ".profile"])
    # Unique, prefer files that already exist, else first writable candidate.
    seen: list[Path] = []
    for path in ordered:
        if path not in seen:
            seen.append(path)
    existing = [p for p in seen if p.is_file()]
    return existing or seen[:1]


def shell_rc_snippet(*, port: int, client_env: Path) -> str:
    env_path = str(client_env)
    return (
        f"{SHELL_BEGIN}\n"
        "# Load TokenSaver egress proxy only while the local listener is up.\n"
        f'_ts_e_env="{env_path}"\n'
        f"_ts_e_port={int(port)}\n"
        'if [ -f "$_ts_e_env" ] && command -v python3 >/dev/null 2>&1; then\n'
        '  if python3 -c "import socket,sys;s=socket.socket();s.settimeout(0.2);'
        "s.connect(('127.0.0.1',int(sys.argv[1])));s.close()\" \"$_ts_e_port\" 2>/dev/null; then\n"
        '    . "$_ts_e_env"\n'
        "  fi\n"
        "fi\n"
        "unset _ts_e_env _ts_e_port\n"
        f"{SHELL_END}\n"
    )


def _replace_marked_block(text: str, block: str) -> str:
    if SHELL_BEGIN in text and SHELL_END in text:
        pattern = re.compile(
            re.escape(SHELL_BEGIN) + r".*?" + re.escape(SHELL_END) + r"\n?",
            flags=re.S,
        )
        return pattern.sub(block.rstrip() + "\n", text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + block


def apply_shell_rc(*, port: int, client_env: Path, rc_paths: list[Path] | None = None) -> list[tuple[Path, str]]:
    block = shell_rc_snippet(port=port, client_env=client_env)
    targets = rc_paths if rc_paths is not None else shell_rc_candidates()
    results: list[tuple[Path, str]] = []
    for target in targets:
        try:
            previous = target.read_text(encoding="utf-8") if target.is_file() else ""
            updated = _replace_marked_block(previous, block)
            if updated == previous:
                results.append((target, "unchanged"))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(updated, encoding="utf-8")
            results.append((target, "applied"))
        except OSError as exc:
            results.append((target, f"error:{exc}"))
    return results


def clear_shell_rc(*, rc_paths: list[Path] | None = None) -> list[tuple[Path, str]]:
    targets = rc_paths if rc_paths is not None else shell_rc_candidates()
    results: list[tuple[Path, str]] = []
    pattern = re.compile(
        re.escape(SHELL_BEGIN) + r".*?" + re.escape(SHELL_END) + r"\n?",
        flags=re.S,
    )
    for target in targets:
        if not target.is_file():
            results.append((target, "absent"))
            continue
        try:
            previous = target.read_text(encoding="utf-8")
            updated = pattern.sub("", previous)
            if updated == previous:
                results.append((target, "absent"))
                continue
            target.write_text(updated, encoding="utf-8")
            results.append((target, "cleared"))
        except OSError as exc:
            results.append((target, f"error:{exc}"))
    return results


def apply_all(
    *,
    port: int,
    ca_cert: Path | str,
    preferred: str = "claude",
    client_env: Path | None = None,
    claude: bool = True,
    vscode: bool | None = None,
    shell: bool = False,
    vscode_paths: list[Path] | None = None,
    rc_paths: list[Path] | None = None,
    claude_path: Path | None = None,
) -> dict[str, Any]:
    pref = normalize_preferred_client(preferred)
    do_vscode = vscode if vscode is not None else pref in ("vscode", "both")
    report: dict[str, Any] = {"preferred": pref, "claude": None, "vscode": [], "shell": []}
    if claude:
        report["claude"] = apply_claude_settings(port=port, ca_cert=ca_cert, path=claude_path)
    if do_vscode:
        report["vscode"] = apply_vscode_settings(port=port, ca_cert=ca_cert, paths=vscode_paths)
    if shell and client_env is not None:
        report["shell"] = apply_shell_rc(port=port, client_env=client_env, rc_paths=rc_paths)
    return report
