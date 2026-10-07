"""Stale BusinessLoop stamps must not block Claude / VS Code."""

from __future__ import annotations

import os

import pytest

from tokensaver_egress.loop_precheck import LoopPrecheckResult, allow_stale_terminal_loop


def test_allow_stale_terminal_loop_clears_stamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr("tokensaver_egress.wizard.CONFIG_DIR", tmp_path)
    monkeypatch.setenv("TOKENSAVER_LOOP_ID", "loop_dead")
    (tmp_path / "current-loop.env").write_text(
        "export TOKENSAVER_LOOP_ID='loop_dead'\n", encoding="utf-8"
    )
    verdict = LoopPrecheckResult(
        False,
        "deny",
        403,
        {"reason": "LOOP_TERMINAL", "status": "completed"},
    )
    assert allow_stale_terminal_loop(verdict) is True
    assert "TOKENSAVER_LOOP_ID" not in os.environ
    assert "TOKENSAVER_LOOP_ID" not in (tmp_path / "current-loop.env").read_text(
        encoding="utf-8"
    )


def test_allow_stale_terminal_loop_ignores_other_denies() -> None:
    verdict = LoopPrecheckResult(False, "deny", 403, {"reason": "GOAL_REQUIRED"})
    assert allow_stale_terminal_loop(verdict) is False
