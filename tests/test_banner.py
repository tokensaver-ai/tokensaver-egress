import sys

from tokensaver_egress.banner import banner_enabled, print_banner
from tokensaver_egress.logo import LOGO_LINES


def test_banner_enabled_default(monkeypatch) -> None:
    monkeypatch.delenv("TOKENSAVER_NO_BANNER", raising=False)
    assert banner_enabled() is True


def test_banner_disabled(monkeypatch) -> None:
    monkeypatch.setenv("TOKENSAVER_NO_BANNER", "1")
    assert banner_enabled() is False


def test_print_banner_skipped_when_stdout_not_tty(capsys, monkeypatch) -> None:
    monkeypatch.delenv("TOKENSAVER_NO_BANNER", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    print_banner()
    assert capsys.readouterr().out == ""


def test_print_banner_contains_logo(capsys, monkeypatch) -> None:
    monkeypatch.delenv("TOKENSAVER_NO_BANNER", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    print_banner()
    out = capsys.readouterr().out
    assert LOGO_LINES[0] in out
    assert "TokenSaver egress" in out
    assert "EGRESS" in out
