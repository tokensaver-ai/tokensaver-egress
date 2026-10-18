"""Install the bundled Claude Code plugin/skills into ``~/.claude/skills/``.

Same layout as ``tokensaver-cli`` so either package can install
``tokensaver-router`` (CCR, onboarding, MCP tools, slash commands).
Egress users do not need ``tokensaver-cli`` for Claude Code skills.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

PLUGIN_SKILL_NAME = "tokensaver-router"


def plugin_install_dir() -> Path:
    return Path.home() / ".claude" / "skills" / PLUGIN_SKILL_NAME


def plugin_is_installed() -> bool:
    dest = plugin_install_dir()
    return dest.is_dir() and (dest / ".claude-plugin" / "plugin.json").is_file()


def _dev_plugin_root() -> Path | None:
    here = Path(__file__).resolve().parent / "claude_plugin"
    if (here / ".claude-plugin" / "plugin.json").is_file():
        return here
    return None


def install_claude_plugin(*, force: bool = False) -> Path | None:
    """Copy the bundled plugin into Claude Code's skills auto-load path.

    Returns the install directory, or ``None`` if the bundle is missing.
    When ``force`` is False and the plugin is already present, returns the
    existing path without overwriting.

    Prefers the in-tree ``claude_plugin`` next to this module (editable /
    monorepo) so skill edits are not masked by an older wheel in site-packages.
    """
    dest = plugin_install_dir()
    if dest.exists() and not force:
        return dest
    if dest.exists() and force:
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)

    src = _dev_plugin_root()
    if src is not None:
        shutil.copytree(src, dest)
        _chmod_hook_scripts(dest)
        return dest

    try:
        root = resources.files("tokensaver_egress").joinpath("claude_plugin")
        with resources.as_file(root) as path:
            if path.is_dir() and (path / ".claude-plugin" / "plugin.json").is_file():
                shutil.copytree(path, dest)
                _chmod_hook_scripts(dest)
                return dest
    except (TypeError, ModuleNotFoundError, AttributeError, FileNotFoundError, NotADirectoryError, OSError):
        pass
    return None


def _chmod_hook_scripts(dest: Path) -> None:
    hooks = dest / "hooks"
    if not hooks.is_dir():
        return
    for path in hooks.glob("*.sh"):
        try:
            path.chmod(0o755)
        except OSError:
            pass
