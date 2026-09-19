"""Tests for BusinessLoop helpers used by ``claude --loop``."""

from __future__ import annotations

import pytest

from tokensaver_egress.business_loop import (
    apply_loop_env,
    loops_url_from_ingest,
    start_business_loop,
)


@pytest.mark.parametrize(
    "ingest,expected",
    [
        (
            "https://api.tokensaver.fr/api/v1/egress/ingest",
            "https://api.tokensaver.fr/api/v1/loops",
        ),
        (
            "http://localhost:8000/api/v1/egress/ingest",
            "http://localhost:8000/api/v1/loops",
        ),
        (
            "http://127.0.0.1:8000/api/v1/egress/ingest/",
            "http://127.0.0.1:8000/api/v1/loops",
        ),
    ],
)
def test_loops_url_from_ingest(ingest: str, expected: str) -> None:
    assert loops_url_from_ingest(ingest) == expected


def test_apply_loop_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import os

    monkeypatch.setattr(
        "tokensaver_egress.wizard.CONFIG_DIR",
        tmp_path,
    )
    apply_loop_env(loop_id="loop_abc", loop_kind="goal_based", iteration=2, precheck=True)
    assert os.environ["TOKENSAVER_LOOP_ID"] == "loop_abc"
    assert os.environ["TOKENSAVER_LOOP_KIND"] == "goal_based"
    assert os.environ["TOKENSAVER_LOOP_ITERATION"] == "2"
    assert os.environ["TOKENSAVER_LOOP_PRECHECK"] == "1"
    loop_file = tmp_path / "current-loop.env"
    assert loop_file.is_file()
    text = loop_file.read_text(encoding="utf-8")
    assert "TOKENSAVER_LOOP_ID='loop_abc'" in text



def test_start_business_loop_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    calls: list[tuple[str, dict]] = []

    class _Resp:
        status_code = 200
        content = b'{"loop_id":"loop_from_api","loop_kind":"goal_based","status":"running"}'
        text = content.decode()

        def json(self):
            return {
                "loop_id": "loop_from_api",
                "loop_kind": "goal_based",
                "status": "running",
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            calls.append((url, json or {}))
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    row = start_business_loop(
        kind="goal_based",
        max_turns=3,
        goal="lab",
        api_key="ts_test",
        ingest_url="http://localhost:8000/api/v1/egress/ingest",
    )
    assert row["loop_id"] == "loop_from_api"
    assert calls[0][0] == "http://localhost:8000/api/v1/loops"
    assert calls[0][1]["loop_kind"] == "goal_based"
    assert calls[0][1]["goal"]["max_turns"] == 3
    assert len(calls) >= 1


def test_start_goal_based_requires_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKENSAVER_API_KEY", "ts_test")
    with pytest.raises(RuntimeError, match="GOAL_REQUIRED|--goal"):
        start_business_loop(kind="goal_based", max_turns=5, goal=None)


def test_run_with_proxy_loop(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from tokensaver_egress import run_cmd

    monkeypatch.setattr(run_cmd, "apply_saved_env", lambda **_: {})
    monkeypatch.setattr(run_cmd, "MitmCA", lambda: _FakeCA(tmp_path / "ca"))
    monkeypatch.setattr(run_cmd, "write_client_env", lambda *_a, **_k: tmp_path / "e.sh")
    monkeypatch.setattr(run_cmd, "write_client_unproxy", lambda: tmp_path / "u.sh")
    monkeypatch.setattr(run_cmd, "resolve_port", lambda *_a, **_k: 18888)
    monkeypatch.setattr(run_cmd, "ensure_proxy", lambda **kw: (True, None))
    monkeypatch.setattr(run_cmd, "_port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "tokensaver_egress.business_loop.start_business_loop",
        lambda **_: {"loop_id": "loop_xyz", "loop_kind": "goal_based"},
    )

    class _Done:
        returncode = 0

    seen: dict = {}

    def _run(argv, env=None, check=False):
        seen["loop"] = (env or {}).get("TOKENSAVER_LOOP_ID")
        return _Done()

    monkeypatch.setattr(run_cmd.subprocess, "run", _run)
    assert (
        run_cmd.run_with_proxy(
            ["/bin/echo", "hi"],
            start_if_needed=True,
            with_loop=True,
            loop_max_turns=3,
        )
        == 0
    )
    assert seen["loop"] == "loop_xyz"


class _FakeCA:
    def __init__(self, ca_dir):
        from pathlib import Path

        self.ca_dir = Path(ca_dir)
        self.ca_dir.mkdir(parents=True, exist_ok=True)
        self.ca_cert_path = self.ca_dir / "ca.crt"
        self.ca_cert_path.write_text("cert", encoding="utf-8")

    def is_initialized(self) -> bool:
        return True

    def init_ca(self):
        return self.ca_cert_path

    def ensure_leaves_match_ca(self) -> int:
        return 0
