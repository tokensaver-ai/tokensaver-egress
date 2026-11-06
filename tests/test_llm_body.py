"""LLM body helpers for egress fast-path / compact cache."""

from __future__ import annotations

import json

from tokensaver_egress.llm_body import (
    PiiSegment,
    all_segments_benign,
    build_cache_key_hint,
    extract_pii_segments,
    segment_likely_benign,
)


def test_salut_is_benign_fastpath():
    body = json.dumps(
        {
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "salut"}],
        }
    )
    segs = extract_pii_segments(body)
    assert len(segs) == 1
    assert all_segments_benign(segs)
    assert segment_likely_benign("salut")


def test_email_triggers_scan():
    assert not segment_likely_benign("contact secret@acme.com")


def test_name_like_text_is_not_benign():
    assert not segment_likely_benign("Hi John Doe")
    assert not segment_likely_benign("je suis Christophe")


def test_mixed_placeholder_and_name_is_not_benign():
    text = "Bonjour [PERSON], je suis Christophe Paouloff"
    assert not segment_likely_benign(text)
    assert not all_segments_benign([PiiSegment((), text)])


def test_extract_pii_segments_includes_prior_assistant_turn():
    body = json.dumps(
        {
            "model": "claude-sonnet-5",
            "messages": [
                {"role": "assistant", "content": "Tu es Christophe."},
                {"role": "user", "content": "quel est mon nom"},
            ],
        }
    )
    segs = extract_pii_segments(body)
    texts = [s.text for s in segs]
    assert any("Christophe" in t for t in texts)
    assert any("quel est mon nom" in t for t in texts)


def test_cache_key_hint_from_large_body():
    history = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
    history.append({"role": "user", "content": "salut"})
    body = json.dumps({"model": "claude-sonnet-5", "messages": history})
    hint = build_cache_key_hint(body, provider="anthropic")
    assert hint is not None
    assert hint.user_prompt == "salut"
    assert hint.model == "claude-sonnet-5"
