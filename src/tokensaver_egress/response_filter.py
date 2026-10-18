"""Synchronous PII filtering of upstream LLM responses (ACP-4 §4.5).

When PII is enabled, the MITM proxy buffers the provider response, asks the backend
to mask assistant text, and re-synthesizes the HTTP payload before the client sees it
(cache-hit parity for live upstream calls).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from tokensaver_egress.anonymize import PromptAnonymizer, _derive_url
from tokensaver_egress.http_backend import backend_post

logger = logging.getLogger("tokensaver-egress.response_filter")

_DEFAULT_TIMEOUT_S = 60.0


@dataclass
class FilteredResponse:
    passthrough: bool
    synth_status: int | None = None
    synth_content_type: str | None = None
    synth_body: bytes | None = None
    pii_response: dict[str, Any] | None = None


class ResponsePiiFilter:
    def __init__(
        self,
        *,
        filter_url: str | None = None,
        policy_url: str | None = None,
        api_key: str | None = None,
        fail_open: bool | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        ingest_url = os.environ.get("TOKENSAVER_INGEST_URL", "")
        self.filter_url = filter_url if filter_url is not None else _derive_url(ingest_url, "filter-response")
        self.policy_url = policy_url if policy_url is not None else _derive_url(ingest_url, "pii-policy")
        self.api_key = api_key if api_key is not None else os.environ.get("TOKENSAVER_API_KEY", "")
        self.timeout_s = timeout_s
        if fail_open is not None:
            self.fail_open = fail_open
        else:
            raw = (os.environ.get("EGRESS_PII_FAIL_OPEN") or "").strip().lower()
            self.fail_open = raw not in ("0", "false", "no")
        self._policy = PromptAnonymizer(
            policy_url=self.policy_url,
            api_key=self.api_key,
            fail_open=self.fail_open,
            timeout_s=timeout_s,
        )

    @property
    def enabled(self) -> bool:
        raw = (os.environ.get("EGRESS_PII_FILTER_RESPONSE") or "1").strip().lower()
        if raw in ("0", "false", "no"):
            return False
        return self._configured()

    def _configured(self) -> bool:
        return bool(self.filter_url and self.api_key)

    async def warm_policy(self) -> None:
        await self._policy.warm_policy()

    async def _pii_enabled(self) -> bool:
        return await self._policy._pii_enabled()

    async def filter(
        self,
        *,
        response_sample: bytes,
        request_body: bytes | None,
        provider: str | None,
        model: str | None,
        tokens_input: int | None,
        tokens_output: int | None,
    ) -> FilteredResponse | None:
        if not self.enabled or not response_sample:
            return None
        if not await self._pii_enabled():
            return None

        req_text = ""
        if request_body:
            try:
                req_text = request_body.decode("utf-8", errors="replace")
            except Exception:
                req_text = ""
        try:
            resp_text = response_sample.decode("utf-8", errors="replace")
        except Exception:
            return None

        t0 = time.perf_counter()
        try:
            payload = {
                "provider": provider,
                "model": model,
                "flow_kind": "llm",
                "request_body": req_text or None,
                "response_sample": resp_text,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
            }
            resp = await backend_post(self.filter_url, api_key=self.api_key or "", json=payload)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code != 200:
                logger.debug(
                    "filter-response HTTP %s in %.0fms → fail-%s",
                    resp.status_code,
                    elapsed_ms,
                    "open" if self.fail_open else "closed",
                )
                return None
            data = resp.json()
            if not data.get("enabled"):
                return None
            if data.get("passthrough"):
                logger.debug("egress filter-response: passthrough in %.0fms", elapsed_ms)
                return FilteredResponse(passthrough=True)
            synth = data.get("synth") if isinstance(data.get("synth"), dict) else {}
            body = synth.get("body")
            if not isinstance(body, str):
                return None
            pii_response = data.get("pii_response") if isinstance(data.get("pii_response"), dict) else None
            logger.info(
                "egress filter-response: provider=%s modified in %.0fms (%s chars)",
                provider,
                elapsed_ms,
                len(body),
            )
            return FilteredResponse(
                passthrough=False,
                synth_status=int(synth.get("status") or 200),
                synth_content_type=str(synth.get("content_type") or "application/json"),
                synth_body=body.encode("utf-8"),
                pii_response=pii_response,
            )
        except Exception:
            logger.debug(
                "filter-response call failed → fail-%s",
                "open" if self.fail_open else "closed",
                exc_info=True,
            )
            return None


_FILTER: ResponsePiiFilter | None = None


def get_response_filter() -> ResponsePiiFilter:
    global _FILTER
    if _FILTER is None:
        _FILTER = ResponsePiiFilter()
    return _FILTER


def reset_response_filter() -> None:
    global _FILTER
    _FILTER = None
