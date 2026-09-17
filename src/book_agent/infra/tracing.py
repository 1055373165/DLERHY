"""OpenTelemetry traces, optional.

Enabled with ``BOOK_AGENT_OTEL_TRACES_ENABLED=true`` and the ``otel`` extra
installed (``pip install 'book-agent[otel]'``). The OTLP/HTTP exporter reads
the standard variables (``OTEL_EXPORTER_OTLP_ENDPOINT``,
``OTEL_EXPORTER_OTLP_HEADERS``, ...); the service name comes from
``OTEL_SERVICE_NAME`` (default ``book-agent``).

Spans: HTTP requests (W3C trace context is taken from incoming headers),
run loop ticks, work items, provider requests, agent steps and tool calls.
When tracing is off every helper here is a no-op, so call sites never check.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_tracer: Any = None


def configure_tracing(enabled: bool, *, exporter: Any = None) -> bool:
    """Install a tracer provider. ``exporter`` overrides OTLP (tests pass an in-memory one)."""
    global _tracer
    if not enabled:
        _tracer = None
        return False
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError:
        logger.warning("OTel traces are enabled but opentelemetry-sdk is not installed; install book-agent[otel]")
        _tracer = None
        return False
    if exporter is None:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            logger.warning("OTel traces are enabled but the OTLP exporter is not installed; install book-agent[otel]")
            _tracer = None
            return False
        processor = BatchSpanProcessor(OTLPSpanExporter())
    else:
        processor = SimpleSpanProcessor(exporter)
    provider = TracerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME") or "book-agent"}))
    provider.add_span_processor(processor)
    # A private provider rather than the global one: reconfiguring (tests, reloads) must not
    # fight the SDK's set-once global.
    _tracer = provider.get_tracer("book_agent")
    return True


def enabled() -> bool:
    return _tracer is not None


def _clean(attributes: Mapping[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        clean[key] = value if isinstance(value, (bool, int, float, str)) else str(value)
    return clean


@contextmanager
def span(name: str, *, parent_headers: Mapping[str, str] | None = None, **attributes: Any) -> Iterator[Any]:
    """A span around the block (records an exception and error status if the block raises)."""
    if _tracer is None:
        yield None
        return
    context = None
    if parent_headers is not None:
        from opentelemetry.propagate import extract

        context = extract(dict(parent_headers))
    with _tracer.start_as_current_span(name, context=context, attributes=_clean(attributes)) as current:
        yield current


def set_attributes(current: Any, **attributes: Any) -> None:
    if current is not None:
        current.set_attributes(_clean(attributes))


def _current() -> Any:
    if _tracer is None:
        return None
    from opentelemetry import trace

    current = trace.get_current_span()
    return current if current.is_recording() else None


def annotate_current(**attributes: Any) -> None:
    set_attributes(_current(), **attributes)


def record_error(exc: BaseException) -> None:
    """Mark the current span failed when the error is handled below the span (so it never propagates)."""
    current = _current()
    if current is None:
        return
    from opentelemetry.trace import Status, StatusCode

    current.record_exception(exc)
    current.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {str(exc)[:200]}"))
