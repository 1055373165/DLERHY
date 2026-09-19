"""Ambient run attribution for work done on behalf of a document run.

Work-item threads bind the run they serve; everything that records a model
call underneath (packet translation, review-time reruns, concept resolution,
terminology surveys) picks the run id up from here when the caller did not
pass one explicitly. That is what lets the budget guardrails and the cost
rollup see spend that happens inside the review and export stages.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current_run_id: ContextVar[str | None] = ContextVar("book_agent_current_run_id", default=None)


def current_run_id() -> str | None:
    return _current_run_id.get()


@contextmanager
def bind_run_context(run_id: str | None) -> Iterator[None]:
    token = _current_run_id.set(run_id)
    try:
        yield
    finally:
        _current_run_id.reset(token)
