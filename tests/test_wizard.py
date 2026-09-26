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


def test_apply_saved_env_file_key_wins_over_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monorepo / Cursor .env must not mask a key written by ``setup``."""
    env_file = tmp_path / "env"
    monkeypatch.setattr(wizard, "ENV_FILE", env_file)
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path)
    wizard.save_env_file(
        {
            "TOKENSAVER_API_KEY": "ts_from_file_key_xxxxxxxxxxxx",
            "TOKENSAVER_INGEST_URL": "https://api.tokensaver.fr/api/v1/egress/ingest",
        }
    )
    monkeypatch.setenv("TOKENSAVER_API_KEY", "ts_from_shell_key_yyyyyyyyyyyy")
    monkeypatch.setenv("TOKENSAVER_INGEST_URL", "http://localhost:8000/api/v1/egress/ingest")
    wizard.apply_saved_env(only_if_unset=True)
    assert wizard.os.environ["TOKENSAVER_API_KEY"] == "ts_from_file_key_xxxxxxxxxxxx"
    assert wizard.os.environ["TOKENSAVER_INGEST_URL"] == (
        "https://api.tokensaver.fr/api/v1/egress/ingest"
    )


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
            "3",  # paste existing key
            "ts_live_key_1234567890",  # api key (getpass)
            "1",  # ingest SaaS
            "y",  # loop precheck
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
        "TOKENSAVER_LOOP_PRECHECK",
        "EGRESS_MITM_ENABLED",
        "EGRESS_CAPTURE_BODIES",
        "EGRESS_PORT",
    ):
        monkeypatch.delenv(key, raising=False)

    code = wizard.run_guided_setup(start_after=False)
    assert code == 0
    data = wizard.load_env_file()
    assert data["TOKENSAVER_API_KEY"].startswith("ts_live")
    assert data["TOKENSAVER_INGEST_URL"] == wizard.DEFAULT_INGEST_URL
    assert data["TOKENSAVER_LOOP_PRECHECK"] == "1"
    assert data["EGRESS_PORT"] == "8891"
    assert data["EGRESS_MITM_ENABLED"] == "true"
    assert (tmp_path / "client-env.sh").is_file()
    out = capsys.readouterr().out
    assert "Guided setup" in out
    assert "8891" in out
    assert "claude --loop" in out


def test_enterprise_mode_hides_signup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKENSAVER_ENTERPRISE", raising=False)
    monkeypatch.delenv("TOKENSAVER_ORG_HINT", raising=False)
    assert wizard.is_enterprise_egress_mode() is False
    monkeypatch.setenv("TOKENSAVER_ENTERPRISE", "1")
    assert wizard.is_enterprise_egress_mode() is True
    monkeypatch.delenv("TOKENSAVER_ENTERPRISE", raising=False)
    monkeypatch.setenv("TOKENSAVER_ORG_HINT", "acme")
    assert wizard.is_enterprise_egress_mode() is True


def test_obtain_api_key_enterprise_skips_signup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TOKENSAVER_ENTERPRISE", "true")
    monkeypatch.setenv("TOKENSAVER_ORG_HINT", "acme")
    # 2 = paste key (1 = login)
    answers = iter(["2", "ts_enterprise_key_xxxxxxxxxxxxxxxx"])

    def fake_input(_prompt: str = "") -> str:
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(wizard.getpass, "getpass", lambda *_a, **_k: "ts_enterprise_key_xxxxxxxxxxxxxxxx")
    key = wizard._obtain_api_key_interactive()
    assert key.startswith("ts_")
    out = capsys.readouterr().out
    assert "Enterprise mode" in out
    assert "Create a free" not in out


def test_obtain_api_key_login_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKENSAVER_ENTERPRISE", raising=False)
    monkeypatch.delenv("TOKENSAVER_ORG_HINT", raising=False)
    monkeypatch.delenv("TOKENSAVER_API_KEY", raising=False)
    monkeypatch.setattr(wizard, "load_env_file", lambda: {})

    answers = iter(
        [
            "2",  # log in
            "1",  # SaaS
            "ada@example.com",
            "password123",
        ]
    )

    def fake_input(prompt: str = "") -> str:
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(wizard.getpass, "getpass", lambda *_a, **_k: "password123")
    monkeypatch.setattr(
        "tokensaver_egress.signup.login_and_create_api_key",
        lambda **kwargs: {
            "api_key_plain": "ts_login_generated_key_xxxx",
            "email": kwargs["email"],
        },
    )

    key = wizard.ensure_api_key()
    assert key == "ts_login_generated_key_xxxx"
    assert wizard.os.environ["TOKENSAVER_API_KEY"] == key


def test_help_mentions_loop(capsys: pytest.CaptureFixture[str]) -> None:
    from tokensaver_egress.__main__ import print_help

    print_help()
    out = capsys.readouterr().out
    assert "--loop" in out
    assert "BusinessLoop" in out or "Boucle" in out or "loop" in out.lower()


def test_menu_quit(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(wizard, "apply_saved_env", lambda **_: {})
    monkeypatch.setattr(wizard, "print_status", lambda *_a, **_k: None)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "9")
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


def test_write_client_env_includes_ca_and_noproxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wizard, "CLIENT_ENV_FILE", tmp_path / "client-env.sh")
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path)
    ca = _FakeCA(tmp_path / "ca")
    ca.init_ca()
    path = wizard.write_client_env(8080, ca)  # type: ignore[arg-type]
    text = path.read_text(encoding="utf-8")
    assert "HTTPS_PROXY=http://127.0.0.1:8080" in text
    assert "NODE_EXTRA_CA_CERTS=" in text
    assert "mcp.tokensaver.fr" in text
    assert "tokensaver-egress claude --no-start" in text


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
