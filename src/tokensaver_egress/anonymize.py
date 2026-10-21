"""Inline PII anonymization of outbound LLM prompts (ACP-4 §4.5)."""

from __future__ import annotations

import logging
import os
import time

from tokensaver_egress.http_backend import backend_post
from tokensaver_egress.llm_body import (
    all_segments_benign,
    extract_pii_segments,
    merge_masked_segments,
)

logger = logging.getLogger("tokensaver-egress.anonymize")

_DEFAULT_POLICY_TTL_S = 120.0
_DEFAULT_TIMEOUT_S = 45.0


def _derive_url(ingest_url: str, suffix: str) -> str:
    base = (ingest_url or "").strip()
    if base.endswith("/ingest"):
        return base[: -len("/ingest")] + "/" + suffix
    return base.rstrip("/") + "/" + suffix if base else ""


def _path_to_json(path: tuple) -> list:
    out: list = []
    for step in path:
        if isinstance(step, int):
            out.append(step)
        else:
            out.append(step)
    return out


class PromptAnonymizer:
    """Masks PII in outbound LLM request bodies via the backend, with a policy cache."""

    def __init__(
        self,
        *,
        anonymize_url: str | None = None,
        policy_url: str | None = None,
        api_key: str | None = None,
        policy_ttl_s: float = _DEFAULT_POLICY_TTL_S,
        fail_open: bool | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        ingest_url = os.environ.get("TOKENSAVER_INGEST_URL", "")
        self.anonymize_url = anonymize_url if anonymize_url is not None else _derive_url(ingest_url, "anonymize")
        self.policy_url = policy_url if policy_url is not None else _derive_url(ingest_url, "pii-policy")
        self.api_key = api_key if api_key is not None else os.environ.get("TOKENSAVER_API_KEY", "")
        self.policy_ttl_s = policy_ttl_s
        self.timeout_s = timeout_s
        if fail_open is not None:
            self.fail_open = fail_open
        else:
            raw = (os.environ.get("EGRESS_PII_FAIL_OPEN") or "").strip().lower()
            self.fail_open = raw not in ("0", "false", "no")
        raw_fast = (os.environ.get("EGRESS_PII_FASTPATH") or "1").strip().lower()
        self.fastpath_enabled = raw_fast not in ("0", "false", "no")
        self._policy_enabled: bool | None = None
        self._policy_expires_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._configured()

    def _configured(self) -> bool:
        return bool(self.anonymize_url and self.policy_url and self.api_key)

    async def warm_policy(self) -> None:
        """Prefetch PII policy (call in parallel with other policy warms)."""
        await self._pii_enabled()

    async def _pii_enabled(self) -> bool:
        now = time.monotonic()
        if self._policy_enabled is not None and self._policy_expires_at > now:
            return bool(self._policy_enabled)
        enabled = await self._fetch_policy()
        if enabled is None:
            return bool(self._policy_enabled) if self._policy_enabled is not None else False
        self._policy_enabled = bool(enabled)
        self._policy_expires_at = now + self.policy_ttl_s
        return self._policy_enabled

    async def _fetch_policy(self) -> bool | None:
        try:
            from tokensaver_egress.http_backend import backend_get

            resp = await backend_get(self.policy_url, api_key=self.api_key or "")
            if resp.status_code != 200:
                logger.debug("pii-policy HTTP %s", resp.status_code)
                return None
            return bool(resp.json().get("pii_enabled"))
        except Exception:
            logger.debug("pii-policy call failed", exc_info=True)
            return None

    async def anonymize(
        self,
        body: bytes,
        *,
        provider: str | None,
        flow_kind: str | None,
        content_type: str | None,
    ) -> tuple[bytes, dict] | None:
        if not self._configured() or not body:
            return None
        if (flow_kind or "").lower() != "llm":
            return None
        if not await self._pii_enabled():
            return None

        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None

        segments = extract_pii_segments(text)
        if not segments:
            return None

        if self.fastpath_enabled and all_segments_benign(segments):
            logger.debug(
                "egress anonymize fast-path: skip backend (%s segment(s), %s body chars)",
                len(segments),
                len(text),
            )
            return None

        t0 = time.perf_counter()
        try:
            payload = {
                "provider": provider,
                "flow_kind": flow_kind,
                "content_type": content_type,
                "segments": [{"path": _path_to_json(s.path), "text": s.text} for s in segments],
            }
            resp = await backend_post(self.anonymize_url, api_key=self.api_key or "", json=payload)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code != 200:
                logger.debug(
                    "anonymize HTTP %s in %.0fms → fail-%s",
                    resp.status_code,
                    elapsed_ms,
                    "open" if self.fail_open else "closed",
                )
                return None
            data = resp.json()
            if not data.get("enabled"):
                return None
            if not data.get("modified"):
                logger.debug("egress anonymize: no PII in %.0fms (%s chars body)", elapsed_ms, len(text))
                return None
            out_segments = data.get("segments")
            if isinstance(out_segments, list):
                masked_body = merge_masked_segments(text, out_segments)
            else:
                masked = data.get("body")
                if not isinstance(masked, str):
                    return None
                masked_body = masked.encode("utf-8")
            pii = data.get("pii") or {}
            logger.info(
                "egress anonymize: provider=%s masked %s entit(y/ies) in %.0fms (%s chars body, %s segments)",
                provider,
                pii.get("total"),
                elapsed_ms,
                len(text),
                len(segments),
            )
            return masked_body, pii
        except Exception:
            logger.debug("anonymize call failed → fail-%s", "open" if self.fail_open else "closed", exc_info=True)
            return None


_ANONYMIZER: PromptAnonymizer | None = None


def get_anonymizer() -> PromptAnonymizer:
    global _ANONYMIZER
    if _ANONYMIZER is None:
        _ANONYMIZER = PromptAnonymizer()
    return _ANONYMIZER


def reset_anonymizer() -> None:
    global _ANONYMIZER
    _ANONYMIZER = None
