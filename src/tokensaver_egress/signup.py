"""Create a TokenSaver account (and virtual key) from the egress CLI."""

from __future__ import annotations

import re
from typing import Any

import httpx

from tokensaver_egress.endpoints import (
    DEFAULT_API_ORIGIN,
    LOCAL_API_ORIGIN,
    api_origin_for_auth,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def api_origin_from_ingest_url(ingest_url: str | None) -> str:
    """Resolve CP auth origin (signup / VK). Remaps DP ingest hosts to control."""
    return api_origin_for_auth(ingest=ingest_url)


def signup_url(api_origin: str) -> str:
    base = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    return f"{base}/api/v1/auths/signup"


def signin_url(api_origin: str) -> str:
    base = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    return f"{base}/api/v1/auths/signin"


def virtual_keys_url(api_origin: str) -> str:
    base = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    return f"{base}/api/v1/gateway/virtual-keys"


def check_email_url(api_origin: str) -> str:
    base = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    return f"{base}/api/v1/auths/check-email"


def is_plausible_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))


def is_virtual_key(raw: str | None) -> bool:
    """True for TokenSaver virtual keys (``tsv_…``). Legacy ``ts_…`` is rejected."""
    return (raw or "").strip().startswith("tsv_")


def _create_virtual_key(
    client: httpx.Client,
    *,
    origin: str,
    token: str,
    key_label: str,
    headers: dict[str, str],
) -> str:
    """POST /gateway/virtual-keys — returns plaintext ``tsv_…`` (shown once)."""
    key_resp = client.post(
        virtual_keys_url(origin),
        json={"name": (key_label or "Egress CLI").strip() or "Egress CLI"},
        headers={**headers, "Authorization": f"Bearer {token}"},
    )
    if key_resp.status_code >= 400:
        detail = _error_message(key_resp)
        low = (detail or "").lower()
        if "email" in low and "verif" in low:
            raise RuntimeError(
                "Email not verified — verify in the console, then retry login "
                f"or paste a virtual key from {origin.replace('api.', 'platform.')}."
            )
        if "quota" in low or "maximum number" in low or "forbidden" in low:
            raise RuntimeError(
                "Could not create a virtual key — open Console → Clés virtuelles "
                "or paste an existing TOKENSAVER_API_KEY (tsv_…)."
            )
        if "sso" in low:
            raise RuntimeError(
                detail
                or "SSO-only organisation — paste a tsv_… virtual key from the console after IdP login."
            )
        raise RuntimeError(detail or f"Could not create virtual key (HTTP {key_resp.status_code})")

    key_body = key_resp.json()
    key = str((key_body or {}).get("key") or "").strip()
    if not key or not is_virtual_key(key):
        raise RuntimeError(
            "Login OK but no virtual key was returned — create one in Console → Clés virtuelles and paste it."
        )
    return key


def create_account(
    *,
    email: str,
    password: str,
    name: str = "",
    organisation_name: str | None = None,
    workspace_name: str | None = None,
    api_origin: str | None = None,
    key_label: str = "Egress CLI",
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """POST public signup, then mint a virtual key (``tsv_…``).

    Signup uses ``create_key=false`` (no legacy ``ts_…``). Returns the session JSON
    plus ``api_key_plain`` set to the virtual-key secret (shown once).
    """
    email_n = (email or "").strip().lower()
    if not is_plausible_email(email_n):
        raise ValueError("Invalid email address")
    if len((password or "").encode("utf-8")) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len((password or "").encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes")

    origin = (api_origin or DEFAULT_API_ORIGIN).rstrip("/")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    payload = {
        "email": email_n,
        "password": password,
        "name": (name or "").strip() or email_n.split("@")[0],
        "create_key": False,
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
            headers=headers,
        )
        if resp.status_code >= 400:
            detail = _error_message(resp)
            raise RuntimeError(detail or f"Signup failed (HTTP {resp.status_code})")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected signup response")

        token = str(data.get("token") or "").strip()
        if not token:
            raise RuntimeError("Account created but no session token was returned")

        key = _create_virtual_key(
            client,
            origin=origin,
            token=token,
            key_label=key_label,
            headers=headers,
        )

    out = dict(data)
    out["api_key_plain"] = key
    return out


def login_and_create_api_key(
    *,
    email: str,
    password: str,
    api_origin: str | None = None,
    key_label: str = "Egress CLI",
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Sign in then create a virtual key for egress.

    Flow: ``POST /auths/signin`` → JWT → ``POST /gateway/virtual-keys`` (plain ``tsv_…`` once).
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

        key = _create_virtual_key(
            client,
            origin=origin,
            token=token,
            key_label=key_label,
            headers=headers,
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
