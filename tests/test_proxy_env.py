"""Tests for client unproxy helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tokensaver_egress import proxy_env


def test_unproxy_sh_snippet() -> None:
    sh = proxy_env.unproxy_sh_snippet()
    assert "unset HTTPS_PROXY" in sh
    assert "HTTP_PROXY" in sh
    assert "NODE_EXTRA_CA_CERTS" in sh


def test_write_client_unproxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "client-unproxy.sh"
    monkeypatch.setattr(proxy_env, "CLIENT_UNPROXY_FILE", path)
    out = proxy_env.write_client_unproxy()
    assert out == path
    text = path.read_text(encoding="utf-8")
    assert "unset HTTPS_PROXY" in text


def test_clear_persisted_proxy_neutralizes_and_unsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proxy_env, "CLIENT_UNPROXY_FILE", tmp_path / "u.sh")
    monkeypatch.setattr(proxy_env, "CLIENT_ENV_FILE", tmp_path / "client-env.sh")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", str(tmp_path / "ca.crt"))
    (tmp_path / "client-env.sh").write_text(
        "export HTTPS_PROXY=http://127.0.0.1:8888\n", encoding="utf-8"
    )
    report = proxy_env.clear_persisted_proxy(ide=False)
    assert "HTTPS_PROXY" not in __import__("os").environ
    assert "HTTPS_PROXY" in report["process_cleared"]
    text = Path(report["client_env_file"]).read_text(encoding="utf-8")
    assert "proxy stopped" in text.lower() or "neutralized" in text.lower()
    assert "unset HTTPS_PROXY" in text


def test_stop_clears_claude_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from tokensaver_egress.__main__ import main

    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "env": {
                    "HTTPS_PROXY": "http://127.0.0.1:8888",
                    "HTTP_PROXY": "http://127.0.0.1:8888",
                    "KEEP_ME": "1",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TOKENSAVER_NO_BANNER", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(proxy_env, "CLIENT_UNPROXY_FILE", tmp_path / "u.sh")
    monkeypatch.setattr(proxy_env, "CLIENT_ENV_FILE", tmp_path / "client-env.sh")
    monkeypatch.setattr(
        "tokensaver_egress.run_cmd.stop_proxy", lambda **_: False
    )
    monkeypatch.setattr("tokensaver_egress.run_cmd._port_open", lambda _p: False)
    monkeypatch.setattr(
        "tokensaver_egress.ide_config.clear_vscode_settings", lambda **_: []
    )
    monkeypatch.setattr("tokensaver_egress.ide_config.clear_shell_rc", lambda **_: [])

    with pytest.raises(SystemExit) as ei:
        main(["stop"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "Cleared client proxy env" in out
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert "HTTPS_PROXY" not in data.get("env", {})
    assert data["env"].get("KEEP_ME") == "1"


def test_unproxy_cli_sh(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(proxy_env, "CLIENT_UNPROXY_FILE", tmp_path / "u.sh")
    assert proxy_env.run_unproxy_cmd(sh_only=True) == 0
    assert "unset HTTPS_PROXY" in capsys.readouterr().out


def test_serve_stop_prints_unproxy_hint(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from tokensaver_egress.__main__ import main

    monkeypatch.setenv("TOKENSAVER_NO_BANNER", "1")
    monkeypatch.delenv("EGRESS_MITM_ENABLED", raising=False)
    monkeypatch.delenv("EGRESS_PORT", raising=False)
    monkeypatch.setattr(proxy_env, "CLIENT_UNPROXY_FILE", tmp_path / "client-unproxy.sh")
    monkeypatch.setattr("tokensaver_egress.serve_cmd.apply_saved_env", lambda **_: {})

    async def _boom(**_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("tokensaver_egress.serve_cmd._serve_quiet", _boom)
    with pytest.raises(SystemExit) as ei:
        main(["serve", "--port", "8888"])
    assert ei.value.code == 0
    err = capsys.readouterr().err
    assert "Stopped" in err
    assert "unproxy" in err.lower() or "client-unproxy" in err
