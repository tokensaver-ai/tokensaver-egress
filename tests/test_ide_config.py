"""Persist proxy env into Claude / VS Code / shell rc."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokensaver_egress import ide_config


def test_proxy_env_values(tmp_path: Path) -> None:
    ca = tmp_path / "ca.crt"
    vals = ide_config.proxy_env_values(port=8899, ca_cert=ca)
    assert vals["HTTPS_PROXY"] == "http://127.0.0.1:8899"
    assert vals["NODE_EXTRA_CA_CERTS"] == str(ca)
    assert "mcp.tokensaver.fr" in vals["NO_PROXY"]


def test_apply_and_clear_claude_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "claude-sonnet-4-6"}}), encoding="utf-8")
    ca = tmp_path / "ca.crt"
    assert ide_config.apply_claude_settings(port=8888, ca_cert=ca, path=path) == "applied"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["env"]["HTTPS_PROXY"] == "http://127.0.0.1:8888"
    assert data["env"]["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
    assert ide_config.clear_claude_settings(path=path) == "cleared"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "HTTPS_PROXY" not in data["env"]
    assert data["env"]["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"


def test_skip_claude_settings_when_routed(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://api.tokensaver.fr/anthropic"}}),
        encoding="utf-8",
    )
    st = ide_config.apply_claude_settings(port=8888, ca_cert=tmp_path / "ca.crt", path=path)
    assert st == "skipped_route"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "HTTPS_PROXY" not in data["env"]


def test_vscode_jsonc_and_merge(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        '{\n  // comment\n  "editor.fontSize": 14,\n  "claudeCode.environmentVariables": ['
        '{"name": "FOO", "value": "bar"}]\n}\n',
        encoding="utf-8",
    )
    results = ide_config.apply_vscode_settings(
        port=8888, ca_cert=tmp_path / "ca.crt", paths=[path]
    )
    assert results == [(path, "applied")]
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {row["name"]: row["value"] for row in data["claudeCode.environmentVariables"]}
    assert names["FOO"] == "bar"
    assert names["HTTPS_PROXY"] == "http://127.0.0.1:8888"
    assert names["NODE_EXTRA_CA_CERTS"].endswith("ca.crt")
    cleared = ide_config.clear_vscode_settings(paths=[path])
    assert cleared == [(path, "cleared")]
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {row["name"]: row["value"] for row in data["claudeCode.environmentVariables"]}
    assert names == {"FOO": "bar"}


def test_shell_rc_block(tmp_path: Path) -> None:
    rc = tmp_path / ".zshrc"
    rc.write_text("# existing\n", encoding="utf-8")
    env = tmp_path / "client-env.sh"
    env.write_text("export HTTPS_PROXY=http://127.0.0.1:8888\n", encoding="utf-8")
    results = ide_config.apply_shell_rc(port=8888, client_env=env, rc_paths=[rc])
    assert results == [(rc, "applied")]
    text = rc.read_text(encoding="utf-8")
    assert ide_config.SHELL_BEGIN in text
    assert "8888" in text
    assert str(env) in text
    # idempotent replace
    results = ide_config.apply_shell_rc(port=9999, client_env=env, rc_paths=[rc])
    text = rc.read_text(encoding="utf-8")
    assert text.count(ide_config.SHELL_BEGIN) == 1
    assert "9999" in text
    assert ide_config.clear_shell_rc(rc_paths=[rc]) == [(rc, "cleared")]
    assert ide_config.SHELL_BEGIN not in rc.read_text(encoding="utf-8")
    assert "# existing" in rc.read_text(encoding="utf-8")


def test_normalize_preferred_client() -> None:
    assert ide_config.normalize_preferred_client("code") == "vscode"
    assert ide_config.normalize_preferred_client("BOTH") == "both"
    assert ide_config.normalize_preferred_client("nope") == "claude"


def test_apply_all_respects_preferred(tmp_path: Path) -> None:
    claude = tmp_path / "claude.json"
    vs = tmp_path / "vscode.json"
    vs.parent.mkdir(parents=True, exist_ok=True)
    report = ide_config.apply_all(
        port=8888,
        ca_cert=tmp_path / "ca.crt",
        preferred="claude",
        client_env=tmp_path / "client-env.sh",
        claude=True,
        vscode=False,
        shell=False,
        vscode_paths=[vs],
        claude_path=claude,
    )
    assert report["claude"] == "applied"
    assert report["vscode"] == []
    assert claude.is_file()
    assert not vs.is_file()


def test_find_vscode_command_uses_app_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ide_config.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ide_config.sys, "platform", "darwin")
    app_bin = tmp_path / "Applications" / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code"
    app_bin.parent.mkdir(parents=True)
    app_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        ide_config,
        "_darwin_editor_cli_paths",
        lambda: [app_bin],
    )
    assert ide_config.find_vscode_command() == [str(app_bin)]
    assert ide_config.find_vscode_binary() == app_bin


def test_find_vscode_command_open_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ide_config.sys, "platform", "darwin")
    monkeypatch.setattr(
        ide_config.shutil,
        "which",
        lambda name: "/usr/bin/open" if name == "open" else None,
    )
    monkeypatch.setattr(ide_config, "_darwin_editor_cli_paths", lambda: [])
    (tmp_path / "Cursor.app").mkdir()
    monkeypatch.setattr(ide_config, "_editor_app_roots", lambda: (tmp_path,))
    assert ide_config.find_vscode_command() == ["/usr/bin/open", "-na", "Cursor"]


def test_find_vscode_command_volume_applications(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ide_config.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ide_config.sys, "platform", "darwin")
    app_bin = tmp_path / "Applications" / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code"
    app_bin.parent.mkdir(parents=True)
    app_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(ide_config, "_editor_app_roots", lambda: (tmp_path / "Applications",))
    assert ide_config.find_vscode_command() == [str(app_bin)]
