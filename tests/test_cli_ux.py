"""CLI UX: bare invoke shows help; serve catches bind errors."""

from __future__ import annotations

import pytest

from tokensaver_egress.__main__ import main, print_help


def test_print_help_mentions_serve(capsys) -> None:
    print_help()
    out = capsys.readouterr().out
    assert "tokensaver-egress serve" in out
    assert "init-ca" in out
    assert "Quick start" in out


def test_bare_invoke_shows_help_not_serve(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TOKENSAVER_NO_BANNER", "1")
    with pytest.raises(SystemExit) as ei:
        main([])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "Usage:" in out
    assert "tokensaver-egress serve" in out


def test_help_subcommand(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TOKENSAVER_NO_BANNER", "1")
    with pytest.raises(SystemExit) as ei:
        main(["help"])
    assert ei.value.code == 0
    assert "Quick start" in capsys.readouterr().out


def test_serve_bind_error_is_friendly(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TOKENSAVER_NO_BANNER", "1")
    monkeypatch.delenv("EGRESS_MITM_ENABLED", raising=False)

    async def _boom(**_kwargs):
        raise OSError(48, "Address already in use")

    monkeypatch.setattr("tokensaver_egress.__main__._serve_quiet", _boom)
    with pytest.raises(SystemExit) as ei:
        main(["serve", "--port", "8888"])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "Cannot listen" in err
    assert "8889" in err
