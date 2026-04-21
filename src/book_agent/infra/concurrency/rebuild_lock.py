"""Two-layer rebuild lock: thread-local + Postgres advisory.

M1.3 rate-limiter for on-demand export rebuilds. The problem: when the
download route detects a missing artifact and triggers a rebuild, N
concurrent downloaders of the same (document_id, export_type) would
each call ``workflow.export_document`` and race to write the same file
on disk. The race is benign for idempotent writes but wastes provider
quota and can corrupt partially-written files.

Design: the lock is *try-acquire* (non-blocking). If another request
is already rebuilding, the losing request receives ``acquired=False``
and the route returns ``503 Retry-After`` so the client can back off
instead of queueing inside the server and consuming a worker slot.

* **Process-local layer** (``threading.Lock``): guards against a
  single uvicorn worker processing two concurrent requests for the
  same key. Cheap, sufficient for single-worker smoke.
* **Postgres layer** (``pg_try_advisory_xact_lock``): guards against
  multi-worker / multi-host deployments. Transaction-scoped — released
  on commit/rollback of the outer request txn, so no manual release
  path to get wrong. Skipped on sqlite because the smoke harness is
  single-worker and sqlite has no equivalent.
"""

from __future__ import annotations

import threading
import zlib
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

_PROCESS_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()


def _get_process_lock(key: tuple[str, str]) -> threading.Lock:
    with _REGISTRY_LOCK:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _advisory_keys(document_id: str, export_type_value: str) -> tuple[int, int]:
    # pg_try_advisory_xact_lock(int4, int4) needs two 32-bit signed ints.
    # crc32 gives us a deterministic 32-bit value from arbitrary input;
    # masking to positive int32 avoids the sign-flip surprise. Collisions
    # are possible but harmless here — worst case, two distinct keys
    # briefly serialize through the same lock.
    a = zlib.crc32(document_id.encode("utf-8")) & 0x7FFFFFFF
    b = zlib.crc32(export_type_value.encode("utf-8")) & 0x7FFFFFFF
    return a, b


@contextmanager
def try_acquire_rebuild_lock(
    session: Session,
    document_id: str,
    export_type_value: str,
) -> Iterator[bool]:
    key = (document_id, export_type_value)
    local = _get_process_lock(key)
    if not local.acquire(blocking=False):
        yield False
        return
    try:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            a, b = _advisory_keys(document_id, export_type_value)
            acquired = session.execute(
                text("SELECT pg_try_advisory_xact_lock(:a, :b)"),
                {"a": a, "b": b},
            ).scalar()
            if not acquired:
                yield False
                return
        yield True
    finally:
        local.release()
