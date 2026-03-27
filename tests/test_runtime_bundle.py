import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from book_agent.core.config import Settings
from book_agent.core.ids import stable_id
from book_agent.domain.enums import RuntimeBundleRevisionStatus
from book_agent.domain.models.ops import RuntimeBundleRevision
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.runtime_bundle import RuntimeBundleService


class RuntimeBundleServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _service(self, session):
        settings = Settings(
            database_url="sqlite+pysqlite:///:memory:",
            runtime_bundle_root=self.bundle_root,
        )
        return RuntimeBundleService(session, settings=settings)

    def test_publish_lookup_and_activate_bundle_round_trip(self) -> None:
        manifest_json = {
            "code": {"runner": "controller", "entrypoint": "book_agent"},
            "config": {"profile": "runtime-v2"},
        }

        with self.session_factory() as session:
            service = self._service(session)
            record = service.publish_bundle(
                revision_name="bundle-v1",
                manifest_json=manifest_json,
                rollout_scope_json={"mode": "dev"},
            )
            self.assertEqual(record.revision.status, RuntimeBundleRevisionStatus.PUBLISHED)
            self.assertTrue(record.manifest_path.exists())
            self.assertEqual(record.manifest_json["revision_name"], "bundle-v1")
            self.assertEqual(record.manifest_json["manifest_json"], manifest_json)

            persisted = session.get(RuntimeBundleRevision, record.revision.id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertEqual(persisted.status, RuntimeBundleRevisionStatus.PUBLISHED)

            looked_up = service.lookup_bundle(record.revision.id)
            self.assertEqual(looked_up.revision.id, record.revision.id)
            self.assertEqual(looked_up.manifest_json["manifest_json"], manifest_json)

            activated = service.activate_bundle(record.revision.id)
            session.commit()

        active_pointer = self.bundle_root / "active.json"
        self.assertTrue(active_pointer.exists())
        active_payload = json.loads(active_pointer.read_text(encoding="utf-8"))
        self.assertEqual(active_payload["active_revision_id"], record.revision.id)
        self.assertEqual(activated.revision.id, record.revision.id)

        with self.session_factory() as session:
            service = self._service(session)
            active_record = service.lookup_active_bundle()
            self.assertEqual(active_record.revision.id, record.revision.id)
            self.assertEqual(active_record.manifest_json["manifest_json"], manifest_json)

    def test_publish_bundle_uses_stable_identifier(self) -> None:
        manifest_json = {"code": {"runner": "controller"}}

        with self.session_factory() as session:
            service = self._service(session)
            record = service.publish_bundle(revision_name="bundle-v2", manifest_json=manifest_json)

        canonical_manifest = json.dumps(manifest_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected_digest = sha256(canonical_manifest.encode("utf-8")).hexdigest()
        expected_revision_id = stable_id("runtime-bundle-revision", "runtime", "bundle-v2", expected_digest)
        self.assertEqual(record.revision.id, expected_revision_id)

    def test_canary_failure_rolls_back_to_latest_stable_bundle(self) -> None:
        with self.session_factory() as session:
            service = self._service(session)

            stable = service.publish_bundle(
                revision_name="bundle-v1",
                manifest_json={"code": {"runner": "controller"}, "config": {"profile": "stable"}},
                rollout_scope_json={"mode": "dev"},
            )
            service.record_canary_verdict(
                stable.revision.id,
                verdict="passed",
                report_json={"lane": "canary", "result": "green"},
            )
            service.activate_bundle(stable.revision.id)

            candidate = service.publish_bundle(
                revision_name="bundle-v2",
                manifest_json={"code": {"runner": "controller"}, "config": {"profile": "candidate"}},
                parent_bundle_revision_id=stable.revision.id,
                rollout_scope_json={"mode": "dev"},
            )
            service.activate_bundle(candidate.revision.id)
            service.record_canary_verdict(
                candidate.revision.id,
                verdict="failed",
                report_json={"signal": "export_misrouting"},
            )
            target = service.rollback_bundle(candidate.revision.id, reason="canary_regression")
            session.commit()

        with self.session_factory() as session:
            service = self._service(session)
            rolled_back = session.get(RuntimeBundleRevision, candidate.revision.id)
            active = service.lookup_active_bundle()
            latest_stable = service.lookup_latest_stable_bundle(exclude_revision_id=candidate.revision.id)

            self.assertIsNotNone(rolled_back)
            assert rolled_back is not None
            self.assertEqual(target.revision.id, stable.revision.id)
            self.assertEqual(rolled_back.status, RuntimeBundleRevisionStatus.ROLLED_BACK)
            self.assertEqual(rolled_back.rollback_target_revision_id, stable.revision.id)
            self.assertEqual(rolled_back.freeze_reason, "canary_regression")
            self.assertEqual(rolled_back.canary_verdict, "failed")
            self.assertEqual(rolled_back.canary_report_json["signal"], "export_misrouting")
            self.assertIsNotNone(rolled_back.frozen_at)
            self.assertIsNotNone(rolled_back.rolled_back_at)
            self.assertEqual(active.revision.id, stable.revision.id)
            self.assertEqual(latest_stable.revision.id, stable.revision.id)
