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
    assert (
        signup.signin_url("https://api.tokensaver.fr")
        == "https://api.tokensaver.fr/api/v1/auths/signin"
    )
    assert (
        signup.api_keys_url("http://localhost:8000")
        == "http://localhost:8000/api/v1/api-keys"
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
            "api_key_plain": "ts_signup_generated_key_xxxx",
            "email": kwargs["email"],
            "email_verified": True,
        },
    )

    key = wizard.ensure_api_key()
    assert key == "ts_signup_generated_key_xxxx"
    assert wizard.os.environ["TOKENSAVER_API_KEY"] == key


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
            if url.endswith("/api-keys"):
                return _Resp(201, {"api_key": "ts_from_login_xxxxxxxx", "id": "k1"})
            return _Resp(404, {"detail": "not found"})

    mock_client = _Client()
    with patch("tokensaver_egress.signup.httpx.Client", return_value=mock_client):
        data = signup.login_and_create_api_key(
            email="a@b.co",
            password="password123",
            api_origin="https://api.tokensaver.fr",
        )

    assert data["api_key_plain"] == "ts_from_login_xxxxxxxx"
    assert mock_client.calls[0][0].endswith("/api/v1/auths/signin")
    assert mock_client.calls[1][0].endswith("/api/v1/api-keys")
    assert mock_client.calls[1][1]["name"] == "Egress CLI"
