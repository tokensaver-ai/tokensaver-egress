"""Inline model_routing for egress MITM — policy-driven target model."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from tokensaver_egress.http_backend import backend_get, backend_post

logger = logging.getLogger(__name__)

_DEFAULT_POLICY_TTL_S = 120.0
_DEFAULT_TIMEOUT_S = 30.0


def _derive_url(ingest_url: str, suffix: str) -> str:
    base = (ingest_url or "").strip()
    if base.endswith("/ingest"):
        return base[: -len("/ingest")] + "/" + suffix
    return base.rstrip("/") + "/" + suffix if base else ""


@dataclass(frozen=True)
class ModelRoutingPolicy:
    routing_enabled: bool
    force_provider: str | None = None
    force_model: str | None = None
    force_region: str | None = None


@dataclass(frozen=True)
class ModelRoutingApplyResult:
    applied: bool
    body: bytes | None = None
    force_provider: str | None = None
    force_model: str | None = None
    force_region: str | None = None
    upstream_host: str | None = None
    upstream_path: str | None = None
    client_format: str | None = None
    upstream_format: str | None = None
    upstream_headers: dict[str, str] | None = None


@dataclass(frozen=True)
class ModelRoutingTranslateResult:
    passthrough: bool
    modified: bool = False
    synth_status: int = 200
    synth_content_type: str | None = None
    synth_body: bytes | None = None


class EgressModelRoutingClient:
    """Backend model_routing policy + apply (short-lived policy cache)."""

    def __init__(
        self,
        *,
        policy_url: str | None = None,
        apply_url: str | None = None,
        translate_url: str | None = None,
        api_key: str | None = None,
        policy_ttl_s: float = _DEFAULT_POLICY_TTL_S,
        fail_open: bool | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        ingest_url = os.environ.get("TOKENSAVER_INGEST_URL", "")
        self.policy_url = policy_url if policy_url is not None else _derive_url(ingest_url, "model-routing-policy")
        self.apply_url = apply_url if apply_url is not None else _derive_url(ingest_url, "apply-model-routing")
        self.translate_url = translate_url if translate_url is not None else _derive_url(
            ingest_url, "translate-llm-response"
        )
        self.api_key = api_key if api_key is not None else os.environ.get("TOKENSAVER_API_KEY", "")
        self.policy_ttl_s = policy_ttl_s
        self.timeout_s = timeout_s
        if fail_open is not None:
            self.fail_open = fail_open
        else:
            raw = (os.environ.get("EGRESS_MODEL_ROUTING_FAIL_OPEN") or "true").strip().lower()
            self.fail_open = raw not in ("0", "false", "no")
        self._policy: ModelRoutingPolicy | None = None
        self._policy_expires_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._configured()

    def _configured(self) -> bool:
        return bool(self.apply_url and self.api_key)

    async def warm_policy(self) -> None:
        await self._fetch_policy()

    async def _fetch_policy(self) -> ModelRoutingPolicy | None:
        now = time.monotonic()
        cached = self._policy
        if cached is not None and self._policy_expires_at > now:
            return cached
        if not self.policy_url or not self.api_key:
            return None
        try:
            resp = await backend_get(self.policy_url, api_key=self.api_key)
            if resp.status_code != 200:
                return None
            data = resp.json()
            pol = ModelRoutingPolicy(
                routing_enabled=bool(data.get("routing_enabled")),
                force_provider=data.get("force_provider"),
                force_model=data.get("force_model"),
                force_region=data.get("force_region"),
            )
            self._policy = pol
            self._policy_expires_at = now + self.policy_ttl_s
            return pol
        except Exception:
            logger.debug("egress model-routing-policy call failed", exc_info=True)
            return None

    async def routing_enabled(self) -> bool:
        pol = await self._fetch_policy()
        return bool(pol and pol.routing_enabled)

    async def apply(
        self,
        body: bytes,
        *,
        host: str | None,
        path: str | None,
        content_type: str | None = None,
        flow_kind: str | None = "llm",
    ) -> ModelRoutingApplyResult | None:
        if not self.enabled:
            return None
        pol = await self._fetch_policy()
        if not pol or not pol.routing_enabled:
            return ModelRoutingApplyResult(applied=False)
        if not self.apply_url:
            return None
        try:
            payload = {
                "body": body.decode("utf-8") if body else "",
                "host": host,
                "path": path,
                "content_type": content_type,
                "flow_kind": flow_kind,
            }
            resp = await backend_post(self.apply_url, api_key=self.api_key, json=payload)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data.get("applied"):
                return ModelRoutingApplyResult(applied=False)
            out_body = data.get("body")
            return ModelRoutingApplyResult(
                applied=True,
                body=out_body.encode("utf-8") if isinstance(out_body, str) else None,
                force_provider=data.get("force_provider"),
                force_model=data.get("force_model"),
                force_region=data.get("force_region"),
                upstream_host=data.get("upstream_host"),
                upstream_path=data.get("upstream_path"),
                client_format=data.get("client_format"),
                upstream_format=data.get("upstream_format"),
                upstream_headers=data.get("upstream_headers")
                if isinstance(data.get("upstream_headers"), dict)
                else None,
            )
        except Exception:
            logger.debug("egress apply-model-routing failed", exc_info=True)
            if self.fail_open:
                return None
            raise

    async def translate_response(
        self,
        *,
        response_sample: bytes,
        request_body: bytes | None,
        client_format: str | None,
        upstream_format: str | None,
        upstream_provider: str | None,
        client_model: str | None,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
    ) -> ModelRoutingTranslateResult | None:
        if not self.translate_url or not self.api_key:
            return None
        try:
            payload = {
                "response_sample": response_sample.decode("utf-8", errors="replace"),
                "request_body": (request_body or b"").decode("utf-8", errors="replace") or None,
                "client_format": client_format,
                "upstream_format": upstream_format,
                "upstream_provider": upstream_provider,
                "client_model": client_model,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
            }
            resp = await backend_post(self.translate_url, api_key=self.api_key, json=payload)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("passthrough", True):
                return ModelRoutingTranslateResult(passthrough=True)
            synth = data.get("synth") if isinstance(data.get("synth"), dict) else {}
            body = synth.get("body")
            return ModelRoutingTranslateResult(
                passthrough=False,
                modified=bool(data.get("modified")),
                synth_status=int(synth.get("status") or 200),
                synth_content_type=synth.get("content_type"),
                synth_body=body.encode("utf-8") if isinstance(body, str) else None,
            )
        except Exception:
            logger.debug("egress translate-llm-response failed", exc_info=True)
            if self.fail_open:
                return None
            raise


_routing_client: EgressModelRoutingClient | None = None


def get_routing_client() -> EgressModelRoutingClient:
    global _routing_client
    if _routing_client is None:
        _routing_client = EgressModelRoutingClient()
    return _routing_client
