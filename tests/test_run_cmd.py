"""Tests for tokensaver-egress claude / run helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tokensaver_egress import run_cmd


def test_client_environ_sets_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _CA:
        ca_cert_path = tmp_path / "ca.crt"

    env = run_cmd.client_environ(port=8899, ca=_CA())  # type: ignore[arg-type]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:8899"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:8899"
    assert env["NODE_EXTRA_CA_CERTS"] == str(_CA.ca_cert_path)
    assert "127.0.0.1" in env["NO_PROXY"]
    assert "mcp.tokensaver.fr" in env["NO_PROXY"]
    assert "gateway.tokensaver.fr" in env["NO_PROXY"]


def test_run_with_proxy_missing_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(run_cmd, "apply_saved_env", lambda **_: {})
    monkeypatch.setattr(run_cmd, "MitmCA", lambda: _FakeCA(tmp_path / "ca"))
    monkeypatch.setattr(run_cmd, "write_client_env", lambda *_a, **_k: tmp_path / "e.sh")
    monkeypatch.setattr(run_cmd, "write_client_unproxy", lambda: tmp_path / "u.sh")
    monkeypatch.setattr(run_cmd, "resolve_port", lambda *_a, **_k: 18888)
    monkeypatch.setattr(run_cmd.shutil, "which", lambda _name: None)
    assert run_cmd.run_with_proxy(["claude"], start_if_needed=False) == 127


def test_run_with_proxy_executes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(run_cmd, "apply_saved_env", lambda **_: {})
    monkeypatch.setattr(run_cmd, "MitmCA", lambda: _FakeCA(tmp_path / "ca"))
    monkeypatch.setattr(run_cmd, "write_client_env", lambda *_a, **_k: tmp_path / "e.sh")
    monkeypatch.setattr(run_cmd, "write_client_unproxy", lambda: tmp_path / "u.sh")
    monkeypatch.setattr(run_cmd, "resolve_port", lambda *_a, **_k: 18888)
    monkeypatch.setattr(run_cmd, "ensure_proxy", lambda **_: (False, None))
    monkeypatch.setattr(run_cmd, "_port_open", lambda *_a, **_k: True)

    class _Done:
        returncode = 0

    seen: dict = {}

    def _run(argv, env=None, check=False):
        seen["argv"] = argv
        seen["proxy"] = env.get("HTTPS_PROXY")
        return _Done()

    monkeypatch.setattr(run_cmd.subprocess, "run", _run)
    assert run_cmd.run_with_proxy(["/bin/echo", "hi"], start_if_needed=False) == 0
    assert seen["argv"] == ["/bin/echo", "hi"]
    assert seen["proxy"] == "http://127.0.0.1:18888"


class _FakeCA:
    def __init__(self, ca_dir: Path) -> None:
        self.ca_dir = ca_dir
        ca_dir.mkdir(parents=True, exist_ok=True)
        self.ca_cert_path = ca_dir / "ca.crt"
        self.ca_cert_path.write_text("cert", encoding="utf-8")

    def is_initialized(self) -> bool:
        return True

    def init_ca(self) -> Path:
        return self.ca_cert_path

    def ensure_leaves_match_ca(self) -> int:
        return 0
