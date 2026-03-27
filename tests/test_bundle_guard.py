import tempfile
import unittest
from pathlib import Path

from book_agent.core.config import Settings
from book_agent.domain.enums import RuntimeBundleRevisionStatus
from book_agent.domain.models.ops import RuntimeBundleRevision
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.bundle_guard import BundleGuardService
from book_agent.services.runtime_bundle import RuntimeBundleService


class BundleGuardServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _service(self, session) -> RuntimeBundleService:
        settings = Settings(
            database_url="sqlite+pysqlite:///:memory:",
            runtime_bundle_root=self.bundle_root,
        )
        return RuntimeBundleService(session, settings=settings)

    def test_failed_dev_canary_rolls_back_to_latest_stable_bundle(self) -> None:
        with self.session_factory() as session:
            bundle_service = self._service(session)
            guard = BundleGuardService(session, bundle_service=bundle_service)

            stable = bundle_service.publish_bundle(
                revision_name="bundle-stable",
                manifest_json={"code": {"runner": "controller"}},
                rollout_scope_json={"mode": "dev"},
            )
            bundle_service.record_canary_verdict(stable.revision.id, verdict="passed", report_json={"passed": True})
            bundle_service.activate_bundle(stable.revision.id)

            candidate = bundle_service.publish_bundle(
                revision_name="bundle-candidate",
                manifest_json={"code": {"runner": "controller"}, "patch": {"id": "candidate"}},
                parent_bundle_revision_id=stable.revision.id,
                rollout_scope_json={"mode": "dev"},
            )
            bundle_service.activate_bundle(candidate.revision.id)

            evaluation = guard.evaluate_canary_and_maybe_rollback(
                revision_id=candidate.revision.id,
                report_json={"canary_verdict": "failed", "signal": "export_misrouting"},
                rollout_scope_json={"mode": "dev"},
            )
            session.commit()

        with self.session_factory() as session:
            persisted_candidate = session.get(RuntimeBundleRevision, candidate.revision.id)
            active = self._service(session).lookup_active_bundle()
            self.assertTrue(evaluation.rollback_performed)
            self.assertEqual(evaluation.effective_revision_id, stable.revision.id)
            self.assertIsNotNone(persisted_candidate)
            assert persisted_candidate is not None
            self.assertEqual(persisted_candidate.status, RuntimeBundleRevisionStatus.ROLLED_BACK)
            self.assertEqual(persisted_candidate.rollback_target_revision_id, stable.revision.id)
            self.assertEqual(persisted_candidate.freeze_reason, "export_misrouting")
            self.assertEqual(active.revision.id, stable.revision.id)

    def test_failed_production_bundle_stays_active_without_canary_lane(self) -> None:
        with self.session_factory() as session:
            bundle_service = self._service(session)
            guard = BundleGuardService(session, bundle_service=bundle_service)

            stable = bundle_service.publish_bundle(
                revision_name="bundle-stable",
                manifest_json={"code": {"runner": "controller"}},
                rollout_scope_json={"mode": "production"},
            )
            bundle_service.record_canary_verdict(stable.revision.id, verdict="passed", report_json={"passed": True})
            bundle_service.activate_bundle(stable.revision.id)

            candidate = bundle_service.publish_bundle(
                revision_name="bundle-prod-candidate",
                manifest_json={"code": {"runner": "controller"}, "patch": {"id": "prod"}},
                parent_bundle_revision_id=stable.revision.id,
                rollout_scope_json={"mode": "production"},
            )
            bundle_service.activate_bundle(candidate.revision.id)

            evaluation = guard.evaluate_canary_and_maybe_rollback(
                revision_id=candidate.revision.id,
                report_json={"canary_verdict": "failed", "signal": "prod_regression"},
                rollout_scope_json={"mode": "production"},
            )
            session.commit()

        with self.session_factory() as session:
            persisted_candidate = session.get(RuntimeBundleRevision, candidate.revision.id)
            active = self._service(session).lookup_active_bundle()
            self.assertFalse(evaluation.rollback_performed)
            self.assertEqual(evaluation.effective_revision_id, candidate.revision.id)
            self.assertIsNotNone(persisted_candidate)
            assert persisted_candidate is not None
            self.assertEqual(persisted_candidate.status, RuntimeBundleRevisionStatus.PUBLISHED)
            self.assertEqual(persisted_candidate.canary_verdict, "failed")
            self.assertIsNone(persisted_candidate.rollback_target_revision_id)
            self.assertEqual(active.revision.id, candidate.revision.id)
