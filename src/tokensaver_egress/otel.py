"""Optional OTLP span export from tokensaver-egress (ACP-3 + ACP-4 §4.6)."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("tokensaver-egress.otel")

_INIT = False


def _endpoint() -> str | None:
    raw = (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip().rstrip("/")
    if not raw:
        return None
    if raw.endswith("/v1/traces"):
        return raw
    return f"{raw}/v1/traces"


def init_egress_tracing() -> None:
    global _INIT
    if _INIT:
        return
    endpoint = _endpoint()
    if not endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "tokensaver-egress"}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        _INIT = True
        logger.info("egress OTLP traces → %s", endpoint)
    except Exception:
        logger.debug("egress OTLP init failed", exc_info=True)


@contextmanager
def egress_span(
    name: str,
    *,
    host: str | None = None,
    capture_mode: str = "tunnel",
    execution_trace_id: str | None = None,
    attrs: dict[str, Any] | None = None,
) -> Iterator[Any]:
    init_egress_tracing()
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("tokensaver-egress")
    except Exception:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        if host:
            span.set_attribute("tokensaver_egress.host", host[:256])
        span.set_attribute("tokensaver_egress.capture_mode", capture_mode)
        if execution_trace_id:
            span.set_attribute("execution_trace_id", execution_trace_id[:128])
        if attrs:
            for k, v in attrs.items():
                if v is not None and k not in ("body",):
                    span.set_attribute(f"tokensaver_egress.{k}", str(v)[:256])
        yield span
