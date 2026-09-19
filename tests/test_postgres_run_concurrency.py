"""Concurrent writers of one run must not overwrite each other's updates.

Work-item threads, the run loop and API transitions all read-modify-write
``document_runs.status_detail_json``. Without a row lock the last writer wins
and usage counters lose increments. Needs real PostgreSQL row locking.

Opt-in like the other PostgreSQL tests: ``BOOK_AGENT_RUN_PG_TESTS=1``.
"""

import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from book_agent.core.config import get_settings
from book_agent.domain.enums import DocumentRunType, DocumentStatus, SourceType
from book_agent.domain.models import Document
from book_agent.infra.db.session import build_session_factory, session_scope
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED
from book_agent.infra.repositories.events import emit_event
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunControlService
from book_agent.services.run_execution import RunExecutionService

ROOT = Path(__file__).resolve().parents[1]
WORKERS = 12


@unittest.skipUnless(
    os.getenv("BOOK_AGENT_RUN_PG_TESTS") == "1",
    "Set BOOK_AGENT_RUN_PG_TESTS=1 to run PostgreSQL integration tests.",
)
class PostgresRunConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        server_url = make_url(get_settings().database_url)
        if not server_url.drivername.startswith("postgresql"):
            self.skipTest("Run concurrency test requires a PostgreSQL database URL.")
        database_name = f"book_agent_concurrency_{uuid4().hex[:12]}"
        admin_engine = create_engine(server_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        self.addCleanup(admin_engine.dispose)
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))

        def _drop_database() -> None:
            with admin_engine.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))

        self.addCleanup(_drop_database)
        database_url = server_url.set(database=database_name)
        env = dict(os.environ, BOOK_AGENT_DATABASE_URL=database_url.render_as_string(hide_password=False))
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(migration.returncode, 0, migration.stderr[-4000:])
        self.engine = create_engine(database_url, pool_size=WORKERS + 2)
        self.addCleanup(self.engine.dispose)
        self.session_factory = build_session_factory(engine=self.engine)

    def test_concurrent_work_item_completions_keep_every_usage_increment(self) -> None:
        with session_scope(self.session_factory) as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"concurrency-{uuid4()}",
                status=DocumentStatus.ACTIVE,
            )
            session.add(document)
            session.flush()
            control = RunControlService(RunControlRepository(session))
            run = control.create_run(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_TARGETED,
                requested_by="concurrency-test",
            )
            control.resume_run(run.run_id, actor_id="concurrency-test", note="start")
            run_id = run.run_id
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[str(uuid4()) for _ in range(WORKERS)])

        lease_tokens: list[str] = []
        for index in range(WORKERS):
            with session_scope(self.session_factory) as session:
                claimed = RunExecutionService(RunControlRepository(session)).claim_next_translate_work_item(
                    run_id=run_id,
                    worker_name="concurrency-test",
                    worker_instance_id=f"worker-{index}",
                    lease_seconds=300,
                )
                assert claimed is not None
                lease_tokens.append(claimed.lease_token)

        barrier = threading.Barrier(WORKERS)
        errors: list[BaseException] = []

        def _complete(lease_token: str) -> None:
            try:
                with session_scope(self.session_factory) as session:
                    execution = RunExecutionService(RunControlRepository(session))
                    barrier.wait()
                    emit_event(
                        session,
                        kind=LLM_CALL_COMPLETED,
                        run_id=run_id,
                        actor_kind="agent",
                        actor_id="test.worker",
                        payload={"call_kind": "translate", "token_in": 1, "token_out": 1, "total_tokens": 2, "cost_usd": 0.0, "latency_ms": 1},
                    )
                    execution.complete_translate_success(
                        lease_token=lease_token,
                        packet_id=str(uuid4()),
                        translation_run_id=str(uuid4()),
                        token_in=1,
                        token_out=1,
                        cost_usd=0.0,
                        latency_ms=1,
                    )
            except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=_complete, args=(token,)) for token in lease_tokens]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [])
        with session_scope(self.session_factory) as session:
            summary = RunControlService(RunControlRepository(session)).get_run_summary(run_id)
        self.assertEqual(summary.status_detail_json["usage_summary"]["token_in"], WORKERS)


if __name__ == "__main__":
    unittest.main()
