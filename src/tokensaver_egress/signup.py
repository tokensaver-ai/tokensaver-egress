"""Create a TokenSaver account (and API key) from the egress CLI."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_API_ORIGIN = "https://api.tokensaver.fr"
LOCAL_API_ORIGIN = "http://localhost:8000"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def api_origin_from_ingest_url(ingest_url: str | None) -> str:
    """Derive ``https://api…`` origin from an egress ingest URL."""
    raw = (ingest_url or "").strip()
    if not raw:
        return DEFAULT_API_ORIGIN
    try:
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    if "localhost" in raw or "127.0.0.1" in raw:
        return LOCAL_API_ORIGIN
    return DEFAULT_API_ORIGIN


def signup_url(api_origin: str) -> str:
    base = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    return f"{base}/api/v1/auths/signup"


def signin_url(api_origin: str) -> str:
    base = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    return f"{base}/api/v1/auths/signin"


def api_keys_url(api_origin: str) -> str:
    base = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    return f"{base}/api/v1/api-keys"


def check_email_url(api_origin: str) -> str:
    base = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    return f"{base}/api/v1/auths/check-email"


def is_plausible_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))


def create_account(
    *,
    email: str,
    password: str,
    name: str = "",
    organisation_name: str | None = None,
    workspace_name: str | None = None,
    api_origin: str | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """POST public signup with ``create_key=true``.

    Returns the JSON body (includes ``api_key_plain`` when successful).
    Raises ``httpx.HTTPStatusError`` on non-2xx, ``ValueError`` on bad input /
    missing key in response.
    """
    email_n = (email or "").strip().lower()
    if not is_plausible_email(email_n):
        raise ValueError("Invalid email address")
    if len((password or "").encode("utf-8")) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len((password or "").encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes")

    origin = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    payload = {
        "email": email_n,
        "password": password,
        "name": (name or "").strip() or email_n.split("@")[0],
        "create_key": True,
        "signup_source": "tokensaver-egress",
    }
    if organisation_name and organisation_name.strip():
        payload["organisation_name"] = organisation_name.strip()
    if workspace_name and workspace_name.strip():
        payload["workspace_name"] = workspace_name.strip()

    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(
            signup_url(origin),
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        # Prefer structured error message when present
        if resp.status_code >= 400:
            detail = _error_message(resp)
            raise RuntimeError(detail or f"Signup failed (HTTP {resp.status_code})")
        data = resp.json()

    key = str(data.get("api_key_plain") or "").strip()
    if not key:
        raise RuntimeError(
            "Account created but no API key was returned — open the console to create one."
        )
    return data


def login_and_create_api_key(
    *,
    email: str,
    password: str,
    api_origin: str | None = None,
    key_label: str = "Egress CLI",
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Sign in then create a TokenSaver API key for egress.

    Flow: ``POST /auths/signin`` → JWT → ``POST /api-keys`` (plain key once).
    Returns a dict with ``api_key_plain``, ``email``, and session fields.
    """
    email_n = (email or "").strip().lower()
    if not is_plausible_email(email_n):
        raise ValueError("Invalid email address")
    if not (password or "").strip():
        raise ValueError("Password is required")

    origin = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(
            signin_url(origin),
            json={"email": email_n, "password": password},
            headers=headers,
        )
        if resp.status_code >= 400:
            detail = _error_message(resp)
            raise RuntimeError(detail or f"Login failed (HTTP {resp.status_code})")
        session = resp.json()
        if not isinstance(session, dict):
            raise RuntimeError("Unexpected login response")

        token = str(session.get("token") or "").strip()
        if not token:
            raise RuntimeError("Login succeeded but no session token was returned")

        key_resp = client.post(
            api_keys_url(origin),
            json={"name": (key_label or "Egress CLI").strip() or "Egress CLI"},
            headers={**headers, "Authorization": f"Bearer {token}"},
        )
        if key_resp.status_code >= 400:
            detail = _error_message(key_resp)
            # Helpful copy for common cases
            low = (detail or "").lower()
            if "email" in low and "verif" in low:
                raise RuntimeError(
                    "Email not verified — verify in the console, then retry login "
                    f"or paste a key from {origin.replace('api.', 'platform.')}."
                )
            if "quota" in low or "maximum number" in low:
                raise RuntimeError(
                    "API key quota reached — delete an unused key in the console "
                    "or paste an existing TOKENSAVER_API_KEY."
                )
            if "sso" in low:
                raise RuntimeError(
                    detail
                    or "SSO-only organisation — paste a ts_… key from the console after IdP login."
                )
            raise RuntimeError(detail or f"Could not create API key (HTTP {key_resp.status_code})")

        key_body = key_resp.json()
        key = str((key_body or {}).get("api_key") or "").strip()
        if not key:
            raise RuntimeError(
                "Login OK but no API key was returned — create one in the console and paste it."
            )

    out = dict(session)
    out["api_key_plain"] = key
    out["email"] = email_n
    return out


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        return (resp.text or "").strip()[:240]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        msg = detail.get("message") or detail.get("error_code")
        if msg:
            return str(msg)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])
    return f"HTTP {resp.status_code}"
