"""Tests for client unproxy helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tokensaver_egress import proxy_env


def test_unproxy_sh_snippet() -> None:
    sh = proxy_env.unproxy_sh_snippet()
    assert "unset HTTPS_PROXY" in sh
    assert "HTTP_PROXY" in sh


def test_write_client_unproxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "client-unproxy.sh"
    monkeypatch.setattr(proxy_env, "CLIENT_UNPROXY_FILE", path)
    out = proxy_env.write_client_unproxy()
    assert out == path
    text = path.read_text(encoding="utf-8")
    assert "unset HTTPS_PROXY" in text


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
