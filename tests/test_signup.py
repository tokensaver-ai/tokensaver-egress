"""Tests for CLI account signup helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tokensaver_egress import signup


def test_api_origin_from_ingest_url(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "TOKENSAVER_CONTROL_URL",
        "TOKENSAVER_API_BASE",
        "TOKENSAVER_AGENTIC_INGEST_URL",
        "TOKENSAVER_INGEST_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    assert (
        signup.api_origin_from_ingest_url(
            "https://api.tokensaver.cloud/api/v1/egress/ingest"
        )
        == "https://control.tokensaver.cloud"
    )
    assert (
        signup.api_origin_from_ingest_url("http://localhost:8000/api/v1/egress/ingest")
        == "http://localhost:8000"
    )
    assert signup.api_origin_from_ingest_url("") == signup.DEFAULT_API_ORIGIN


def test_signup_url() -> None:
    assert (
        signup.signup_url("https://control.tokensaver.cloud")
        == "https://control.tokensaver.cloud/api/v1/auths/signup"
    )
    assert (
        signup.signin_url("https://control.tokensaver.cloud")
        == "https://control.tokensaver.cloud/api/v1/auths/signin"
    )
    assert (
        signup.virtual_keys_url("http://localhost:8000")
        == "http://localhost:8000/api/v1/gateway/virtual-keys"
    )


def test_is_plausible_email() -> None:
    assert signup.is_plausible_email("a@b.co")
    assert not signup.is_plausible_email("not-an-email")


def test_is_virtual_key() -> None:
    assert signup.is_virtual_key("tsv_abc123_test_key_zzzzzzzz")
    assert not signup.is_virtual_key("ts_legacy_key_xxxxxxxxxxxx")
    assert not signup.is_virtual_key("")


def test_create_account_success() -> None:
    signup_resp = MagicMock()
    signup_resp.status_code = 200
    signup_resp.json.return_value = {
        "email": "new@example.com",
        "token": "jwt-signup",
        "email_verified": True,
    }

    vk_resp = MagicMock()
    vk_resp.status_code = 201
    vk_resp.json.return_value = {
        "key": "tsv_abc123_test_key_zzzz",
        "id": "vk-1",
        "key_prefix": "tsv_abc123",
    }

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.side_effect = [signup_resp, vk_resp]

    with patch("tokensaver_egress.signup.httpx.Client", return_value=mock_client):
        data = signup.create_account(
            email="new@example.com",
            password="password123",
            name="Ada",
            organisation_name="Ada Org",
            api_origin="https://api.tokensaver.fr",
        )

    assert data["api_key_plain"].startswith("tsv_")
    calls = mock_client.post.call_args_list
    assert calls[0].args[0].endswith("/api/v1/auths/signup")
    body = calls[0].kwargs["json"]
    assert body["email"] == "new@example.com"
    assert body["create_key"] is False
    assert body["signup_source"] == "tokensaver-egress"
    assert body["organisation_name"] == "Ada Org"
    assert calls[1].args[0].endswith("/api/v1/gateway/virtual-keys")
    assert calls[1].kwargs["json"]["name"] == "Egress CLI"


def test_create_account_rejects_short_password() -> None:
    with pytest.raises(ValueError, match="at least 8"):
        signup.create_account(email="a@b.co", password="short")


def test_create_account_http_error() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "detail": {
            "error_code": "EMAIL_ALREADY_EXISTS",
            "message": "An account with this email already exists",
        }
    }
    mock_resp.text = ""

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("tokensaver_egress.signup.httpx.Client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="already exists"):
            signup.create_account(
                email="dup@example.com",
                password="password123",
            )


def test_ensure_api_key_offers_signup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokensaver_egress import wizard

    monkeypatch.setattr(wizard, "ENV_FILE", tmp_path / "env")
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path)
    monkeypatch.delenv("TOKENSAVER_API_KEY", raising=False)
    monkeypatch.delenv("TOKENSAVER_ENTERPRISE", raising=False)
    monkeypatch.delenv("TOKENSAVER_ORG_HINT", raising=False)

    answers = iter(
        [
            "1",  # create account
            "1",  # SaaS
            "Ada",  # name
            "ada@example.com",  # email
            "password123",  # password (getpass)
            "password123",  # confirm (getpass)
            "Ada Org",  # org
        ]
    )

    def fake_input(prompt: str = "") -> str:
        return next(answers)

    def fake_getpass(prompt: str = "") -> str:
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(wizard.getpass, "getpass", fake_getpass)
    monkeypatch.setattr(
        "tokensaver_egress.signup.create_account",
        lambda **kwargs: {
            "api_key_plain": "tsv_signup_generated_key_xxxx",
            "email": kwargs["email"],
            "email_verified": True,
        },
    )

    key = wizard.ensure_api_key()
    assert key == "tsv_signup_generated_key_xxxx"
    assert wizard.os.environ["TOKENSAVER_API_KEY"] == key
    assert wizard.load_env_file()["TOKENSAVER_API_KEY"] == key


def test_login_and_create_api_key_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from tokensaver_egress import signup

    class _Resp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = ""

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *a, **k):
            self.calls: list[tuple[str, dict]] = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url: str, json: dict | None = None, headers: dict | None = None):
            self.calls.append((url, json or {}))
            if url.endswith("/auths/signin"):
                return _Resp(200, {"token": "jwt-test", "email": "a@b.co"})
            if url.endswith("/gateway/virtual-keys"):
                return _Resp(201, {"key": "tsv_from_login_xxxxxxxx", "id": "k1"})
            return _Resp(404, {"detail": "not found"})

    mock_client = _Client()
    with patch("tokensaver_egress.signup.httpx.Client", return_value=mock_client):
        data = signup.login_and_create_api_key(
            email="a@b.co",
            password="password123",
            api_origin="https://api.tokensaver.fr",
        )

    assert data["api_key_plain"] == "tsv_from_login_xxxxxxxx"
    assert mock_client.calls[0][0].endswith("/api/v1/auths/signin")
    assert mock_client.calls[1][0].endswith("/api/v1/gateway/virtual-keys")
    assert mock_client.calls[1][1]["name"] == "Egress CLI"
