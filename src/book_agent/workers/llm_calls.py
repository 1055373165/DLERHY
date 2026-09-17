"""Uniform accounting for every model call that is not a packet translation.

Packet translation already records ``llm.call.*`` events around its three
transactions (see ``services/translation.py``). Terminology extraction,
term surveys, concept resolution and provider smoke tests used to call the
provider without leaving a trace, so their tokens never reached the cost
rollup or the run budget. :func:`observed_llm_call` closes that gap: wrap the
provider call, hand it the usage, and exactly one ``llm.call.completed`` or
``llm.call.failed`` event lands in the caller's session with a ``call_kind``
that says what the tokens were spent on.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED, LLM_CALL_FAILED
from book_agent.infra.repositories.events import emit_event
from book_agent.workers.failures import classify_failure

CALL_KIND_TRANSLATE = "translate"
CALL_KIND_GLOSSARY_EXTRACT = "glossary.extract"
CALL_KIND_TERM_SURVEY = "terminology.survey"
CALL_KIND_CONCEPT_RESOLVE = "concept.resolve"
CALL_KIND_PROVIDER_TEST = "provider.test"


def usage_payload(usage: Any) -> dict[str, Any]:
    """Serialise any usage-like object (``TranslationUsage`` or a stand-in)."""
    token_in = int(getattr(usage, "token_in", 0) or 0)
    token_out = int(getattr(usage, "token_out", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0) or (token_in + token_out)
    cost = getattr(usage, "cost_usd", None)
    return {
        "token_in": token_in,
        "token_out": token_out,
        "total_tokens": total,
        "cost_usd": float(cost) if cost is not None else None,
        "latency_ms": int(getattr(usage, "latency_ms", 0) or 0),
        "provider_request_id": getattr(usage, "provider_request_id", None),
    }


@dataclass(slots=True)
class LLMCallObservation:
    call_id: str
    call_kind: str
    model: str
    backend: str
    started_at: float
    usage: Any | None = None
    extra_payload: dict[str, Any] = field(default_factory=dict)

    def complete(self, usage: Any | None) -> None:
        self.usage = usage


@contextmanager
def observed_llm_call(
    session: Session,
    *,
    call_kind: str,
    model: str,
    backend: str = "openai_compatible",
    run_id: str | None = None,
    chapter_id: str | None = None,
    packet_id: str | None = None,
    actor_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Iterator[LLMCallObservation]:
    """Record one provider call as an ``llm.call.completed`` / ``llm.call.failed`` event.

    The body performs the call and passes its usage to ``observation.complete``.
    An exception inside the body records a failed call (classified with the
    same rules the work-item runtime uses) and is re-raised. Events are
    flushed into ``session``; committing stays with the caller.
    """
    started = time.perf_counter()
    observation = LLMCallObservation(
        call_id=stable_id("llm-call", call_kind, model, f"{time.time():.6f}"),
        call_kind=call_kind,
        model=model,
        backend=backend,
        started_at=started,
        extra_payload=dict(payload or {}),
    )
    base_payload = {
        "call_id": observation.call_id,
        "call_kind": call_kind,
        "backend": backend,
        "model": model,
        **observation.extra_payload,
    }
    actor = actor_id or f"service.{call_kind}"
    try:
        yield observation
    except Exception as exc:
        classification = classify_failure(exc)
        emit_event(
            session,
            kind=LLM_CALL_FAILED,
            run_id=run_id,
            chapter_id=chapter_id,
            packet_id=packet_id,
            actor_kind="agent",
            actor_id=actor,
            correlation_id=correlation_id,
            payload={
                **base_payload,
                "error_class": type(exc).__name__,
                "error_code": classification.reason,
                "error_message": str(exc)[:500],
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        raise
    usage = usage_payload(observation.usage)
    if not usage["latency_ms"]:
        usage["latency_ms"] = max(1, int((time.perf_counter() - started) * 1000))
    emit_event(
        session,
        kind=LLM_CALL_COMPLETED,
        run_id=run_id,
        chapter_id=chapter_id,
        packet_id=packet_id,
        actor_kind="agent",
        actor_id=actor,
        correlation_id=correlation_id,
        payload={**base_payload, **usage},
    )


def record_llm_usage(
    session: Session,
    *,
    call_kind: str,
    model: str,
    usage: Any,
    backend: str = "openai_compatible",
    run_id: str | None = None,
    chapter_id: str | None = None,
    packet_id: str | None = None,
    actor_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Record a call that already happened elsewhere (usage in hand, no exception)."""
    with observed_llm_call(
        session,
        call_kind=call_kind,
        model=model,
        backend=backend,
        run_id=run_id,
        chapter_id=chapter_id,
        packet_id=packet_id,
        actor_id=actor_id,
        payload=payload,
    ) as observation:
        observation.complete(usage)
