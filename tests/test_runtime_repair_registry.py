import unittest

from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.runtime_repair_registry import (
    RuntimeRepairWorkerRegistry,
    UnsupportedRuntimeRepairWorkerError,
)
from book_agent.services.runtime_repair_agent_adapter import (
    ExportRoutingRepairAgentAdapter,
    PacketRuntimeDefectRepairAgentAdapter,
    ReviewDeadlockRepairAgentAdapter,
    RuntimeRepairAgentAdapter,
)


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
        packet_worker = registry.resolve(
            worker_hint="packet_runtime_defect_repair_agent",
            worker_contract_version=1,
        )
        default_worker = registry.resolve_for_input_bundle({})

        self.assertIsInstance(review_worker, ReviewDeadlockRepairAgentAdapter)
        self.assertIsInstance(export_worker, ExportRoutingRepairAgentAdapter)
        self.assertIsInstance(packet_worker, PacketRuntimeDefectRepairAgentAdapter)
        self.assertIsInstance(default_worker, RuntimeRepairAgentAdapter)
        self.assertNotEqual(type(review_worker), type(export_worker))
        self.assertEqual(review_worker.descriptor().execution_mode, "in_process")
        self.assertEqual(export_worker.descriptor().worker_hint, "export_routing_repair_agent")
        self.assertEqual(packet_worker.descriptor().worker_hint, "packet_runtime_defect_repair_agent")

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
