"""Tests for Claude skills install from tokensaver-egress."""

from __future__ import annotations

from pathlib import Path

import pytest

from tokensaver_egress import plugin_install


def test_install_claude_plugin_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest_root = tmp_path / "skills" / plugin_install.PLUGIN_SKILL_NAME
    monkeypatch.setattr(plugin_install, "plugin_install_dir", lambda: dest_root)
    monkeypatch.setattr(plugin_install, "plugin_is_installed", lambda: False)

    assert not dest_root.exists()
    out = plugin_install.install_claude_plugin(force=False)
    assert out == dest_root
    assert (dest_root / ".claude-plugin" / "plugin.json").is_file()
    assert (dest_root / "skills" / "ccr" / "SKILL.md").is_file()
    assert (dest_root / "skills" / "onboarding" / "SKILL.md").is_file()
    assert (dest_root / "skills" / "mcp-tools" / "SKILL.md").is_file()


def test_install_skips_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest_root = tmp_path / "skills" / plugin_install.PLUGIN_SKILL_NAME
    monkeypatch.setattr(plugin_install, "plugin_install_dir", lambda: dest_root)

    first = plugin_install.install_claude_plugin(force=False)
    marker = dest_root / "KEEP_ME"
    marker.write_text("x", encoding="utf-8")
    second = plugin_install.install_claude_plugin(force=False)
    assert first == second == dest_root
    assert marker.is_file()

    plugin_install.install_claude_plugin(force=True)
    assert not marker.exists()
    assert (dest_root / "skills" / "ccr" / "SKILL.md").is_file()


def test_install_skills_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from tokensaver_egress.__main__ import main

    dest_root = tmp_path / "tokensaver-router"
    monkeypatch.setattr(plugin_install, "plugin_install_dir", lambda: dest_root)
    monkeypatch.setenv("TOKENSAVER_NO_BANNER", "1")

    with pytest.raises(SystemExit) as ei:
        main(["install-skills"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "installed" in out.lower() or "Claude skills" in out
    assert (dest_root / "skills" / "ccr" / "SKILL.md").is_file()
