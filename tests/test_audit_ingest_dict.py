"""Egress audit → SaaS ingest payload must forward pipeline / PII metadata."""

from tokensaver_egress.audit import EgressRecord, record_to_ingest_dict


def test_record_to_ingest_dict_forwards_pipeline_and_pii_metadata():
    rec = EgressRecord(
        host="api.anthropic.com",
        capture_mode="mitm",
        attrs={
            "flow_kind": "llm",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "pipeline_sync": True,
            "pipeline_modules": ["pii", "llm"],
            "pii_anonymized": {"detected": False, "total": 0, "types": {}},
            "request_body": '{"messages":[{"role":"user","content":"[PERSON]"}]}',
            "compression": {"enabled": True},
        },
    )
    payload = record_to_ingest_dict(rec)
    assert payload["pipeline_sync"] is True
    assert payload["pipeline_modules"] == ["pii", "llm"]
    assert payload["pii_anonymized"]["total"] == 0
    assert payload["request_body"].startswith('{"messages"')
    assert payload.get("attrs", {}).get("compression") == {"enabled": True}
