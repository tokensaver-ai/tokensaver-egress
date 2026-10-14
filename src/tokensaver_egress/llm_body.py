"""Lightweight LLM JSON body helpers for the egress proxy (no backend import)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_USER_QUERY_TAG_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE)
_MASKED_PLACEHOLDER_RE = re.compile(
    r"\[(?:PERSON|EMAIL|PHONE|LOCATION|ORGANIZATION|DATE_TIME|NRP|IP_ADDRESS|EMAIL_ADDRESS)\]"
)
# Obvious PII carriers — if none match on a short segment, skip the backend round-trip.
_PII_SIGNAL_RE = re.compile(
    r"@|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|"
    r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b|"
    r"\b\d{9,}\b|"
    r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b",
    re.IGNORECASE,
)

_STRUCTURAL_KEYS = frozenset(
    {
        "model",
        "type",
        "role",
        "name",
        "id",
        "tool_use_id",
        "tool_call_id",
        "cache_control",
        "schema",
        "format",
        "enum",
        "required",
        "service_tier",
    }
)


@dataclass(frozen=True)
class PiiSegment:
    path: tuple[Any, ...]
    text: str


@dataclass(frozen=True)
class CacheKeyHint:
    user_prompt: str
    hist_sig: str
    instr_sig: str
    model: str
    provider: str
    temperature: float | None
    stream: bool
    has_tool_use: bool


def normalize_user_prompt(s: str) -> str:
    return " ".join((s or "").strip().split())


def strip_user_query_wrapper(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    m = _USER_QUERY_TAG_RE.search(t)
    if m and (inner := m.group(1).strip()):
        return inner
    return t


def _message_content_text(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content") or ""))
        return "\n".join(parts)
    return str(content or "")


def _user_message_query_text(msg: dict[str, Any]) -> str | None:
    content = msg.get("content")
    if isinstance(content, str):
        text = strip_user_query_wrapper(content.strip())
        return text or None
    if isinstance(content, list):
        text_parts: list[str] = []
        has_non_tool_block = False
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = (block.get("type") or "").strip().lower()
            if btype == "text":
                t = str(block.get("text") or "").strip()
                if t:
                    text_parts.append(t)
            elif btype != "tool_result":
                has_non_tool_block = True
        if text_parts:
            return strip_user_query_wrapper(text_parts[-1]) or None
        if not has_non_tool_block and content:
            return None
    raw = strip_user_query_wrapper(str(content or "").strip())
    return raw or None


def extract_last_user_prompt(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if (msg.get("role") or "").strip().lower() != "user":
            continue
        raw = _user_message_query_text(msg)
        if raw:
            return normalize_user_prompt(raw)
    return ""


def _is_structural_key(key: str | None) -> bool:
    if not key:
        return False
    k = key.strip().lower()
    return k in _STRUCTURAL_KEYS or k.endswith("_id")


def _collect_maskable_leaves(
    obj: Any, path: tuple[Any, ...] = ()
) -> list[tuple[tuple[Any, ...], str]]:
    out: list[tuple[tuple[Any, ...], str]] = []
    if isinstance(obj, str):
        parent_key = path[-1] if path else None
        if not _is_structural_key(str(parent_key) if parent_key is not None else None):
            out.append((path, obj))
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_collect_maskable_leaves(v, path + (k,)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_collect_maskable_leaves(v, path + (i,)))
    return out


def extract_pii_segments(body_text: str) -> list[PiiSegment]:
    """Maskable string leaves across the full ``messages`` array (egress PII parity).

    Prior design scanned only the last user turn onward for latency; that left names
    in earlier assistant/user history verbatim when PII was enabled mid-session.
    """
    try:
        parsed = json.loads(body_text)
    except (json.JSONDecodeError, TypeError):
        return [PiiSegment((), body_text)] if body_text.strip() else []
    if not isinstance(parsed, dict):
        return []
    messages = parsed.get("messages")
    if not isinstance(messages, list):
        return []
    segments: list[PiiSegment] = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        for path, text in _collect_maskable_leaves(msg, ("messages", i)):
            if text and text.strip():
                segments.append(PiiSegment(path=path, text=text))
    return segments


def segment_likely_benign(text: str, *, max_fastpath_chars: int = 512) -> bool:
    if not text or not text.strip():
        return True
    if _MASKED_PLACEHOLDER_RE.search(text):
        stripped = _MASKED_PLACEHOLDER_RE.sub(" ", text)
        stripped = re.sub(r"[\s\W\d_]+", "", stripped)
        if len(stripped) < 3:
            return True
    if len(text) > max_fastpath_chars:
        return False
    if _PII_SIGNAL_RE.search(text):
        return False
    stripped = text.strip()
    # Only skip GLiNER for short, all-lowercase casual prompts ("salut", "ca va?").
    # Any capitalized token (e.g. "Christophe", "Hello") must be scanned — names
    # are the primary egress PII target and GLiNER catches them.
    if len(stripped) <= 48 and stripped.lower() == stripped:
        return True
    return False


def all_segments_benign(segments: list[PiiSegment]) -> bool:
    return bool(segments) and all(segment_likely_benign(s.text) for s in segments)


def _set_at_path(root: Any, path: tuple[Any, ...], value: str) -> None:
    cur = root
    for step in path[:-1]:
        cur = cur[step]
    cur[path[-1]] = value


def merge_masked_segments(body_text: str, segments: list[dict[str, Any]]) -> bytes:
    parsed = json.loads(body_text)
    if not isinstance(parsed, dict):
        return body_text.encode("utf-8")
    for item in segments:
        path_raw = item.get("path")
        text = item.get("text")
        if not isinstance(path_raw, list) or not isinstance(text, str):
            continue
        path: tuple[Any, ...] = tuple(int(p) if isinstance(p, str) and p.isdigit() else p for p in path_raw)
        try:
            _set_at_path(parsed, path, text)
        except (KeyError, IndexError, TypeError):
            continue
    return json.dumps(parsed, ensure_ascii=False).encode("utf-8")


def _build_instr_sig(instruction_text: str) -> str:
    payload = (instruction_text or "").strip()
    if not payload:
        return ""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _build_history_sig(messages: list[dict[str, Any]], *, message_countback: int = 2) -> str:
    if not messages:
        return ""
    tail = messages[-message_countback:] if message_countback else []
    canonical_parts: list[str] = []
    for m in tail:
        role = (m.get("role") or "").strip().lower() or "unknown"
        content = normalize_user_prompt(_message_content_text(m))
        if content:
            canonical_parts.append(f"{role}:{content}")
    canonical = "\n".join(canonical_parts).strip()
    if not canonical:
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _messages_for_history_sig(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for i in range(len(messages) - 1, -1, -1):
        role = (messages[i].get("role") or "").strip().lower()
        if role != "user":
            continue
        if _user_message_query_text(messages[i]) is not None:
            return list(messages[:i])
    return list(messages)


def _request_has_tool_use(parsed: dict[str, Any]) -> bool:
    raw = parsed.get("messages")
    if not isinstance(raw, list):
        return False
    for msg in raw:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_calls"):
                    return True
        if msg.get("tool_calls"):
            return True
    return False


def build_cache_key_hint(body_text: str, *, provider: str | None) -> CacheKeyHint | None:
    try:
        parsed = json.loads(body_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    mod = str(parsed.get("model") or "").strip()
    if not mod:
        return None
    messages = [m for m in (parsed.get("messages") or []) if isinstance(m, dict)]
    user_prompt = extract_last_user_prompt(messages)
    if not user_prompt and "prompt" in parsed:
        user_prompt = normalize_user_prompt(str(parsed.get("prompt") or ""))
    if not user_prompt:
        return None
    system = parsed.get("system")
    instr = ""
    if isinstance(system, str):
        instr = system.strip()
    elif isinstance(system, list):
        instr = "\n\n".join(
            str(b.get("text") or "") for b in system if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
    temp = parsed.get("temperature")
    try:
        temperature = float(temp) if temp is not None else None
    except (TypeError, ValueError):
        temperature = None
    return CacheKeyHint(
        user_prompt=user_prompt,
        hist_sig=_build_history_sig(_messages_for_history_sig(messages)),
        instr_sig=_build_instr_sig(instr),
        model=mod,
        provider=(provider or "").strip() or "unknown",
        temperature=temperature,
        stream=bool(parsed.get("stream")),
        has_tool_use=_request_has_tool_use(parsed),
    )
