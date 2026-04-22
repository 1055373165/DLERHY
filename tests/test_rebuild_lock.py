# ruff: noqa: E402
"""M1.3 rebuild-lock contract tests.

Pins the contract that :func:`try_acquire_rebuild_lock` is a non-
blocking, per-key mutex. The download route relies on exactly this
shape: the first concurrent requester gets to rebuild, every other
racer immediately receives ``acquired=False`` (which the route
translates to 503 Retry-After). If this contract breaks, the
thundering-herd defense is gone and N workers rebuild the same file
in parallel — wasting provider quota and risking corrupt writes.

We deliberately exercise the sqlite path because the smoke harness
is single-worker sqlite. The Postgres advisory-lock path is behind a
dialect branch and is not exercised here; that branch is
single-node-safe by construction (one round-trip, no client-side
state). The process-local layer is the one that needs a running-test
guarantee.
"""

import os
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.infra.concurrency.rebuild_lock import try_acquire_rebuild_lock


class _StubSession:
    """Stand-in for a SQLAlchemy Session.

    Only the attributes the lock actually probes (``bind.dialect.name``)
    are implemented. ``None`` bind triggers the sqlite/no-op branch,
    matching what the lock does when it can't detect Postgres.
    """

    bind = None


class RebuildLockContractTests(unittest.TestCase):

    def test_same_key_second_acquire_fails_while_first_held(self) -> None:
        doc_id = "doc-same-key"
        export_type = "merged_html"
        first_entered = threading.Event()
        release_first = threading.Event()
        second_result: dict[str, bool] = {}

        def first() -> None:
            with try_acquire_rebuild_lock(_StubSession(), doc_id, export_type) as acquired:
                self.assertTrue(acquired)
                first_entered.set()
                # Hold the lock until the main thread signals release.
                release_first.wait(timeout=5.0)

        def second() -> None:
            first_entered.wait(timeout=5.0)
            with try_acquire_rebuild_lock(_StubSession(), doc_id, export_type) as acquired:
                second_result["acquired"] = acquired

        t1 = threading.Thread(target=first)
        t2 = threading.Thread(target=second)
        t1.start()
        t2.start()
        t2.join(timeout=5.0)
        release_first.set()
        t1.join(timeout=5.0)

        self.assertFalse(second_result["acquired"], "contending acquire must return False")

    def test_different_keys_do_not_contend(self) -> None:
        # A rebuild on (doc-A, merged_html) must not block a rebuild on
        # (doc-B, merged_html) or on (doc-A, bilingual_html). Otherwise
        # one slow rebuild would stall the whole fleet.
        held = threading.Event()
        release = threading.Event()
        disjoint_result: dict[str, bool] = {}

        def holder() -> None:
            with try_acquire_rebuild_lock(_StubSession(), "doc-A", "merged_html") as acquired:
                self.assertTrue(acquired)
                held.set()
                release.wait(timeout=5.0)

        def disjoint() -> None:
            held.wait(timeout=5.0)
            with try_acquire_rebuild_lock(_StubSession(), "doc-B", "merged_html") as acquired:
                disjoint_result["other_doc"] = acquired
            with try_acquire_rebuild_lock(_StubSession(), "doc-A", "bilingual_html") as acquired:
                disjoint_result["other_type"] = acquired

        t_hold = threading.Thread(target=holder)
        t_disjoint = threading.Thread(target=disjoint)
        t_hold.start()
        t_disjoint.start()
        t_disjoint.join(timeout=5.0)
        release.set()
        t_hold.join(timeout=5.0)

        self.assertTrue(disjoint_result["other_doc"])
        self.assertTrue(disjoint_result["other_type"])

    def test_release_allows_subsequent_acquire(self) -> None:
        doc_id = "doc-reacquire"
        export_type = "merged_html"
        with try_acquire_rebuild_lock(_StubSession(), doc_id, export_type) as first:
            self.assertTrue(first)
        # After context exit the lock is released — a fresh acquire
        # on the same key must succeed, not see the prior owner's
        # state. This guards against a leak that would look exactly
        # like a stuck 503 in prod.
        with try_acquire_rebuild_lock(_StubSession(), doc_id, export_type) as second:
            self.assertTrue(second)

    def test_exception_inside_block_still_releases(self) -> None:
        doc_id = "doc-exc"
        export_type = "merged_html"

        class _Boom(Exception):
            pass

        with self.assertRaises(_Boom):
            with try_acquire_rebuild_lock(_StubSession(), doc_id, export_type) as acquired:
                self.assertTrue(acquired)
                raise _Boom()

        # If finally-release was skipped, this would dead-lock the
        # test. We rely on the 2s join timeout elsewhere; here a
        # straight acquire must work synchronously.
        with try_acquire_rebuild_lock(_StubSession(), doc_id, export_type) as after:
            self.assertTrue(after)


if __name__ == "__main__":
    unittest.main()
