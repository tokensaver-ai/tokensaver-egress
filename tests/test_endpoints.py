"""CP/DP endpoint resolution after ADR-023 cutover."""

from __future__ import annotations

import pytest

from tokensaver_egress import endpoints as ep


def test_defaults_are_cloud() -> None:
    assert ep.DEFAULT_INGEST_URL.startswith("https://api.tokensaver.cloud/")
    assert ep.DEFAULT_CONTROL_ORIGIN == "https://control.tokensaver.cloud"
    assert "control.tokensaver.cloud" in ep.NO_PROXY_HOSTS
    assert "api.tokensaver.cloud" in ep.NO_PROXY_HOSTS


def test_remap_dp_ingest_to_control(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKENSAVER_CONTROL_URL", raising=False)
    monkeypatch.delenv("TOKENSAVER_API_BASE", raising=False)
    monkeypatch.delenv("TOKENSAVER_AGENTIC_INGEST_URL", raising=False)
    ingest = "https://api.tokensaver.cloud/api/v1/egress/ingest"
    assert ep.control_origin(ingest=ingest) == "https://control.tokensaver.cloud"
    assert ep.agentic_events_url(ingest=ingest) == (
        "https://control.tokensaver.cloud/api/v1/egress/agentic-events"
    )
    assert ep.loops_url(ingest=ingest) == "https://control.tokensaver.cloud/api/v1/loops"


def test_legacy_fr_dp_remaps_to_cloud_control(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKENSAVER_CONTROL_URL", raising=False)
    monkeypatch.delenv("TOKENSAVER_API_BASE", raising=False)
    monkeypatch.delenv("TOKENSAVER_AGENTIC_INGEST_URL", raising=False)
    ingest = "https://api.tokensaver.fr/api/v1/egress/ingest"
    assert ep.control_origin(ingest=ingest) == "https://control.tokensaver.cloud"
    assert ep.api_origin_for_auth(ingest=ingest) == "https://control.tokensaver.cloud"


def test_control_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKENSAVER_CONTROL_URL", "https://control.example.com/api/v1")
    assert ep.control_origin() == "https://control.example.com"
    assert ep.agentic_events_url() == (
        "https://control.example.com/api/v1/egress/agentic-events"
    )


def test_localhost_monolith_keeps_same_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKENSAVER_CONTROL_URL", raising=False)
    monkeypatch.delenv("TOKENSAVER_API_BASE", raising=False)
    monkeypatch.delenv("TOKENSAVER_AGENTIC_INGEST_URL", raising=False)
    ingest = "http://localhost:8000/api/v1/egress/ingest"
    assert ep.control_origin(ingest=ingest) == "http://localhost:8000"
    assert ep.loops_url(ingest=ingest) == "http://localhost:8000/api/v1/loops"
