"""Gateway budget precheck client (Keep my Claude / MITM)."""

from __future__ import annotations

import json
from unittest.mock import patch

from tokensaver_egress.budget_precheck import (
    budget_precheck_enabled,
    precheck_gateway_budget,
)


def test_budget_precheck_default_enabled(monkeypatch):
    monkeypatch.delenv("TOKENSAVER_BUDGET_PRECHECK", raising=False)
    assert budget_precheck_enabled() is True
    monkeypatch.setenv("TOKENSAVER_BUDGET_PRECHECK", "0")
    assert budget_precheck_enabled() is False


def test_precheck_allow(monkeypatch):
    monkeypatch.setenv("TOKENSAVER_BUDGET_PRECHECK", "1")
    monkeypatch.setenv("TOKENSAVER_INGEST_URL", "http://localhost:8000/api/v1/egress/ingest")
    monkeypatch.setenv("TOKENSAVER_API_KEY", "tsv_test")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"decision": "allow", "action": "allow"}).encode()

    with patch("urllib.request.urlopen", return_value=_Resp()):
        v = precheck_gateway_budget(provider="anthropic", model="claude", projected_tokens=10)
    assert v.allowed is True
    assert v.status_code == 200


def test_precheck_deny_402(monkeypatch):
    import io
    import urllib.error

    monkeypatch.setenv("TOKENSAVER_BUDGET_PRECHECK", "1")
    monkeypatch.setenv("TOKENSAVER_INGEST_URL", "http://localhost:8000/api/v1/egress/ingest")
    monkeypatch.setenv("TOKENSAVER_API_KEY", "tsv_test")

    payload = json.dumps(
        {
            "detail": {
                "error_code": "GATEWAY_BUDGET_EXCEEDED",
                "message": "tokens 1100/1000",
            }
        }
    ).encode()
    err = urllib.error.HTTPError(
        "http://x",
        402,
        "Payment Required",
        hdrs=None,
        fp=io.BytesIO(payload),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        v = precheck_gateway_budget(provider="anthropic", model="claude")
    assert v.allowed is False
    assert v.status_code == 402
    assert v.error_code == "GATEWAY_BUDGET_EXCEEDED"


def test_precheck_hard_deny_revoked_403(monkeypatch):
    import io
    import urllib.error

    monkeypatch.setenv("TOKENSAVER_BUDGET_PRECHECK", "1")
    monkeypatch.setenv("TOKENSAVER_BUDGET_PRECHECK_FAIL_OPEN", "1")
    monkeypatch.setenv("TOKENSAVER_INGEST_URL", "http://localhost:8000/api/v1/egress/ingest")
    monkeypatch.setenv("TOKENSAVER_API_KEY", "tsv_test")

    err = urllib.error.HTTPError(
        "http://x",
        403,
        "Forbidden",
        hdrs=None,
        fp=io.BytesIO(json.dumps({"detail": "TokenSaver virtual key has been revoked"}).encode()),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        v = precheck_gateway_budget(provider="anthropic", model="claude")
    assert v.allowed is False
    assert v.status_code == 403
    assert v.error_code == "VIRTUAL_KEY_REVOKED"
