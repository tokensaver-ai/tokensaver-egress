"""Egress classification (host + optional MITM JSON body) — mirror of SaaS classify."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

_PROVIDER_HOSTS: list[tuple[str, str]] = [
    ("api.openai.com", "openai"),
    ("openai.azure.com", "azure_openai"),
    ("api.anthropic.com", "anthropic"),
    ("generativelanguage.googleapis.com", "google"),
    ("aiplatform.googleapis.com", "google_vertex"),
    ("api.mistral.ai", "mistral"),
    ("api.groq.com", "groq"),
    ("api.x.ai", "xai"),
    ("api.cohere.ai", "cohere"),
    ("api.deepseek.com", "deepseek"),
    ("api.together.xyz", "together"),
    ("openrouter.ai", "openrouter"),
    ("bedrock", "aws_bedrock"),
]

# Hosts that carry MCP JSON-RPC even when the body is invisible (tunnel / no MITM).
_MCP_HOST_MARKERS: tuple[str, ...] = (
    "mcp.tokensaver.",
    "gateway.tokensaver.",
    "mcp-proxy.anthropic.com",
    ".mcp.tokensaver.",
)


@dataclass
class EgressClassification:
    flow_kind: str = "unknown"
    provider: str | None = None
    model: str | None = None
    method: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    args_hash: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


def provider_for_host(host: str | None) -> str | None:
    if not host:
        return None
    h = host.lower()
    for needle, provider in _PROVIDER_HOSTS:
        if needle in h:
            return provider
    return None


def is_mcp_host(host: str | None) -> bool:
    """True when the hostname is a known MCP gateway / proxy (tunnel-safe hint)."""
    if not host:
        return False
    h = host.lower().split(":")[0]
    if h.startswith("mcp.") or h.endswith(".mcp") or ".mcp." in h:
        return True
    return any(marker in h for marker in _MCP_HOST_MARKERS)


def is_mitm_candidate_host(host: str | None) -> bool:
    """LLM provider hosts + known MCP gateways (opt-in MITM decrypt)."""
    return provider_for_host(host) is not None or is_mcp_host(host)


def args_hash(body: Any) -> str:
    try:
        text = body if isinstance(body, str) else json.dumps(body, sort_keys=True, default=str)
    except Exception:
        text = str(body)
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _extract_llm(body: dict[str, Any], provider: str | None) -> EgressClassification:
    model = body.get("model")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    tokens_input = usage.get("prompt_tokens") or usage.get("input_tokens")
    tokens_output = usage.get("completion_tokens") or usage.get("output_tokens")
    return EgressClassification(
        flow_kind="llm",
        provider=provider,
        model=str(model)[:256] if model else None,
        tokens_input=int(tokens_input) if isinstance(tokens_input, int) else None,
        tokens_output=int(tokens_output) if isinstance(tokens_output, int) else None,
        args_hash=args_hash(body),
    )


def _is_jsonrpc(body: dict[str, Any]) -> bool:
    return body.get("jsonrpc") == "2.0" or "method" in body


# MITM paths that carry outbound LLM prompts (Anthropic/OpenAI chat APIs).
_LLM_PATH_MARKERS: tuple[str, ...] = (
    "/v1/messages",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
    "/chat/completions",
)


def _is_llm_path(path: str | None) -> bool:
    if not path:
        return False
    p = (path.split("?")[0] or "").lower()
    return any(marker in p for marker in _LLM_PATH_MARKERS)


def _body_looks_like_llm_request(parsed: dict[str, Any]) -> bool:
    """True when the JSON body is an outbound chat/completions request, not telemetry."""
    if "messages" in parsed or "prompt" in parsed:
        return True
    # ``model`` alone appears in many Anthropic side-channel payloads — require chat fields.
    if "model" in parsed and any(k in parsed for k in ("max_tokens", "stream", "input", "messages")):
        return True
    return False


def _body_looks_like_llm_response(parsed: dict[str, Any]) -> bool:
    """True for provider chat completion *responses* (model + usage tokens)."""
    usage = parsed.get("usage")
    if not isinstance(usage, dict):
        return False
    if "model" not in parsed:
        return False
    return any(
        k in usage
        for k in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "total_tokens")
    )


def classify_egress(
    *,
    host: str | None,
    content_type: str | None = None,
    body: Any | None = None,
    path: str | None = None,
) -> EgressClassification:
    provider = provider_for_host(host)
    parsed: dict[str, Any] | None = None
    if isinstance(body, dict):
        parsed = body
    elif isinstance(body, str) and body.strip().startswith("{"):
        try:
            maybe = json.loads(body)
            parsed = maybe if isinstance(maybe, dict) else None
        except Exception:
            parsed = None

    if parsed is not None:
        if _is_jsonrpc(parsed):
            method = str(parsed.get("method") or "")[:128]
            if method.startswith("message/") or method.startswith("tasks/"):
                return EgressClassification(
                    flow_kind="a2a", provider=provider, method=method, args_hash=args_hash(parsed)
                )
            attrs: dict[str, Any] = {}
            if method == "tools/call" or method.startswith("tools/call"):
                params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
                tool_name = params.get("name") if isinstance(params, dict) else None
                if isinstance(tool_name, str) and tool_name.strip():
                    attrs["mcp_tool"] = tool_name.strip()[:256]
                    attrs["tool_calls"] = [tool_name.strip()[:256]]
                    attrs["tool_call_count"] = 1
                    attrs["mcp_tool_calls"] = [tool_name.strip()[:256]]
            return EgressClassification(
                flow_kind="mcp",
                provider=provider,
                method=method or None,
                args_hash=args_hash(parsed),
                attrs=attrs,
            )
        if _body_looks_like_llm_request(parsed) or _body_looks_like_llm_response(parsed):
            return _extract_llm(parsed, provider)
        if is_mcp_host(host):
            return EgressClassification(flow_kind="mcp", provider=provider)
        if provider is not None:
            return EgressClassification(flow_kind="http", provider=provider)

    if _is_llm_path(path):
        return EgressClassification(flow_kind="llm", provider=provider)
    if is_mcp_host(host):
        return EgressClassification(flow_kind="mcp", provider=provider)
    # Blind tunnel (no body, no path): keep provider host → llm for audit parity.
    if provider is not None and path is None:
        return EgressClassification(flow_kind="llm", provider=provider)
    if provider is not None:
        return EgressClassification(flow_kind="http", provider=provider)
    return EgressClassification(flow_kind="http", provider=None)


def classify_host(host: str | None) -> EgressClassification:
    return classify_egress(host=host, body=None)
