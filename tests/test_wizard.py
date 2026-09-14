"""Tests for interactive setup helpers (non-TTY / mocked input)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tokensaver_egress import wizard


def test_save_and_load_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "env"
    monkeypatch.setattr(wizard, "ENV_FILE", env_file)
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path)

    path = wizard.save_env_file(
        {
            "TOKENSAVER_API_KEY": "ts_test_key_abcdefgh",
            "TOKENSAVER_INGEST_URL": "https://api.tokensaver.fr/api/v1/egress/ingest",
            "EGRESS_MITM_ENABLED": "true",
            "EGRESS_CAPTURE_BODIES": "1",
            "EGRESS_PORT": "8899",
        }
    )
    assert path == env_file
    data = wizard.load_env_file()
    assert data["TOKENSAVER_API_KEY"] == "ts_test_key_abcdefgh"
    assert data["EGRESS_PORT"] == "8899"

    monkeypatch.delenv("TOKENSAVER_API_KEY", raising=False)
    monkeypatch.delenv("EGRESS_PORT", raising=False)
    wizard.apply_saved_env(only_if_unset=True)
    assert wizard.os.environ["TOKENSAVER_API_KEY"] == "ts_test_key_abcdefgh"
    assert wizard.os.environ["EGRESS_PORT"] == "8899"


def test_normalize_api_key_dedupes_double_paste() -> None:
    key = "ts_" + ("a" * 48)
    assert wizard.normalize_api_key(key + key) == key
    assert wizard.normalize_api_key(key) == key
    assert wizard.normalize_api_key(f"  {key}  ") == key


def test_guided_setup_persists_without_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(wizard, "ENV_FILE", tmp_path / "env")
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wizard, "CLIENT_ENV_FILE", tmp_path / "client-env.sh")
    monkeypatch.setattr(wizard, "MitmCA", lambda: _FakeCA(tmp_path / "ca"))
    monkeypatch.setattr(wizard, "plugin_is_installed", lambda: False)
    monkeypatch.setattr(
        wizard,
        "install_claude_plugin",
        lambda force=False: tmp_path / "skills" / "tokensaver-router",
    )
    monkeypatch.setattr(wizard, "plugin_install_dir", lambda: tmp_path / "skills" / "tokensaver-router")

    answers = iter(
        [
            "ts_live_key_1234567890",  # api key (getpass)
            "",  # ingest default
            "y",  # mitm
            "y",  # bodies
            "8891",  # port
            "n",  # skip skills install
        ]
    )

    def fake_input(prompt: str = "") -> str:
        return next(answers)

    def fake_getpass(prompt: str = "") -> str:
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(wizard.getpass, "getpass", fake_getpass)
    monkeypatch.setattr(wizard.platform, "system", lambda: "Linux")
    for key in (
        "TOKENSAVER_API_KEY",
        "TOKENSAVER_INGEST_URL",
        "EGRESS_MITM_ENABLED",
        "EGRESS_CAPTURE_BODIES",
        "EGRESS_PORT",
    ):
        monkeypatch.delenv(key, raising=False)

    code = wizard.run_guided_setup(start_after=False)
    assert code == 0
    data = wizard.load_env_file()
    assert data["TOKENSAVER_API_KEY"].startswith("ts_live")
    assert data["EGRESS_PORT"] == "8891"
    assert data["EGRESS_MITM_ENABLED"] == "true"
    assert (tmp_path / "client-env.sh").is_file()
    out = capsys.readouterr().out
    assert "Guided setup" in out
    assert "8891" in out


def test_menu_quit(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(wizard, "apply_saved_env", lambda **_: {})
    monkeypatch.setattr(wizard, "print_status", lambda *_a, **_k: None)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "7")
    assert wizard.run_menu() == 0
    assert "Bye" in capsys.readouterr().out


def test_help_mentions_setup(capsys: pytest.CaptureFixture[str]) -> None:
    from tokensaver_egress.__main__ import print_help

    print_help()
    out = capsys.readouterr().out
    assert "tokensaver-egress setup" in out
    assert "Guided setup" in out or "setup" in out


def test_bare_invoke_uses_menu_when_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    from tokensaver_egress.__main__ import main

    monkeypatch.setenv("TOKENSAVER_NO_BANNER", "1")
    monkeypatch.setattr("tokensaver_egress.__main__.is_interactive", lambda: True)
    monkeypatch.setattr("tokensaver_egress.__main__.run_menu", lambda: 42)
    with pytest.raises(SystemExit) as ei:
        main([])
    assert ei.value.code == 42


class _FakeCA:
    def __init__(self, ca_dir: Path) -> None:
        self.ca_dir = ca_dir
        self.ca_dir.mkdir(parents=True, exist_ok=True)
        self._cert = ca_dir / "ca.crt"
        self._key = ca_dir / "ca.key"

    @property
    def ca_cert_path(self) -> Path:
        return self._cert

    def is_initialized(self) -> bool:
        return self._cert.is_file() and self._key.is_file()

    def init_ca(self) -> Path:
        self._key.write_text("key", encoding="utf-8")
        self._cert.write_text("cert", encoding="utf-8")
        return self._cert
