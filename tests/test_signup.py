"""Tests for CLI account signup helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tokensaver_egress import signup


def test_api_origin_from_ingest_url() -> None:
    assert (
        signup.api_origin_from_ingest_url(
            "https://api.tokensaver.fr/api/v1/egress/ingest"
        )
        == "https://api.tokensaver.fr"
    )
    assert (
        signup.api_origin_from_ingest_url("http://localhost:8000/api/v1/egress/ingest")
        == "http://localhost:8000"
    )
    assert signup.api_origin_from_ingest_url("") == signup.DEFAULT_API_ORIGIN


def test_signup_url() -> None:
    assert (
        signup.signup_url("https://api.tokensaver.fr")
        == "https://api.tokensaver.fr/api/v1/auths/signup"
    )


def test_is_plausible_email() -> None:
    assert signup.is_plausible_email("a@b.co")
    assert not signup.is_plausible_email("not-an-email")


def test_create_account_success() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "email": "new@example.com",
        "api_key_plain": "ts_abc123_test_key_zzzz",
        "email_verified": True,
    }

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("tokensaver_egress.signup.httpx.Client", return_value=mock_client):
        data = signup.create_account(
            email="new@example.com",
            password="password123",
            name="Ada",
            organisation_name="Ada Org",
            api_origin="https://api.tokensaver.fr",
        )

    assert data["api_key_plain"].startswith("ts_")
    call_kwargs = mock_client.post.call_args
    assert call_kwargs.args[0].endswith("/api/v1/auths/signup")
    body = call_kwargs.kwargs["json"]
    assert body["email"] == "new@example.com"
    assert body["create_key"] is True
    assert body["signup_source"] == "tokensaver-egress"
    assert body["organisation_name"] == "Ada Org"


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


def test_ensure_api_key_offers_signup(monkeypatch: pytest.MonkeyPatch) -> None:
    from tokensaver_egress import wizard

    monkeypatch.setattr(wizard, "load_env_file", lambda: {})
    monkeypatch.delenv("TOKENSAVER_API_KEY", raising=False)

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
            "api_key_plain": "ts_signup_generated_key_xxxx",
            "email": kwargs["email"],
            "email_verified": True,
        },
    )

    key = wizard.ensure_api_key()
    assert key == "ts_signup_generated_key_xxxx"
    assert wizard.os.environ["TOKENSAVER_API_KEY"] == key
