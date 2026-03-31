import unittest

from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.runtime_repair_registry import (
    RuntimeRepairWorkerRegistry,
    UnsupportedRuntimeRepairWorkerError,
)
from book_agent.services.runtime_repair_worker import RuntimeRepairWorker


class RuntimeRepairWorkerRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)

    def test_resolves_registered_worker_hints(self) -> None:
        registry = RuntimeRepairWorkerRegistry(session_factory=self.session_factory)

        review_worker = registry.resolve(
            worker_hint="review_deadlock_repair_agent",
            worker_contract_version=1,
        )
        export_worker = registry.resolve(
            worker_hint="export_routing_repair_agent",
            worker_contract_version=1,
        )
        default_worker = registry.resolve_for_input_bundle({})

        self.assertIsInstance(review_worker, RuntimeRepairWorker)
        self.assertIsInstance(export_worker, RuntimeRepairWorker)
        self.assertIsInstance(default_worker, RuntimeRepairWorker)

    def test_rejects_unknown_worker_hint(self) -> None:
        registry = RuntimeRepairWorkerRegistry(session_factory=self.session_factory)

        with self.assertRaises(UnsupportedRuntimeRepairWorkerError) as exc_info:
            registry.resolve(
                worker_hint="unknown_runtime_repair_agent",
                worker_contract_version=1,
            )

        self.assertIn("Unknown repair worker hint", str(exc_info.exception))
        self.assertIn("review_deadlock_repair_agent", str(exc_info.exception))

    def test_rejects_unsupported_worker_contract_version(self) -> None:
        registry = RuntimeRepairWorkerRegistry(session_factory=self.session_factory)

        with self.assertRaises(UnsupportedRuntimeRepairWorkerError) as exc_info:
            registry.resolve(
                worker_hint="review_deadlock_repair_agent",
                worker_contract_version=2,
            )

        self.assertIn("Unsupported repair worker contract version", str(exc_info.exception))
        self.assertIn("Supported versions: 1", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
