import tempfile
import unittest
from pathlib import Path

from book_agent.core.config import Settings
from book_agent.domain.enums import DocumentStatus, ExportType, SourceType
from book_agent.domain.models import Document
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.export_routing import ExportRoutingError, ExportRoutingService
from book_agent.services.runtime_bundle import RuntimeBundleService


class ExportRoutingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _settings(self) -> Settings:
        return Settings(
            database_url="sqlite+pysqlite:///:memory:",
            runtime_bundle_root=self.bundle_root,
        )

    def _seed_document(self, session) -> Document:
        document = Document(
            source_type=SourceType.EPUB,
            file_fingerprint="export-routing-document",
            source_path="/tmp/export-routing.epub",
            title="Export Routing",
            author="Tester",
            src_lang="en",
            tgt_lang="zh",
            status=DocumentStatus.ACTIVE,
            parser_version=1,
            segmentation_version=1,
        )
        session.add(document)
        session.flush()
        return document

    def test_resolve_document_route_uses_active_bundle_policy(self) -> None:
        with self.session_factory() as session:
            document = self._seed_document(session)
            bundle_service = RuntimeBundleService(session, settings=self._settings())
            record = bundle_service.publish_bundle(
                revision_name="bundle-v1",
                manifest_json={
                    "code": {"entrypoint": "book_agent"},
                    "config": {"mode": "dev"},
                    "routing_policy": {
                        "export_routes": {
                            "rebuilt_pdf": {
                                "selected_route": "epub.rebuilt_pdf_via_html",
                                "allowed_routes": ["epub.rebuilt_pdf_via_html"],
                                "route_candidates": ["epub.rebuilt_pdf_via_html"],
                                "source_types": ["epub"],
                            }
                        }
                    },
                },
                rollout_scope_json={"mode": "dev"},
            )
            bundle_service.activate_bundle(record.revision.id)
            routing_service = ExportRoutingService(runtime_bundle_service=bundle_service)

            decision = routing_service.resolve_document_route(
                document=document,
                export_type=ExportType.REBUILT_PDF,
            )

            self.assertEqual(decision.selected_route, "epub.rebuilt_pdf_via_html")
            self.assertEqual(decision.runtime_bundle_revision_id, record.revision.id)
            self.assertEqual(decision.route_evidence_json["route_decision_source"], "bundle_policy")
            self.assertEqual(
                decision.route_evidence_json["expected_route_candidates"],
                ["epub.rebuilt_pdf_via_html"],
            )

    def test_resolve_document_route_raises_for_misrouted_policy(self) -> None:
        with self.session_factory() as session:
            document = self._seed_document(session)
            bundle_service = RuntimeBundleService(session, settings=self._settings())
            record = bundle_service.publish_bundle(
                revision_name="bundle-v2",
                manifest_json={
                    "code": {"entrypoint": "book_agent"},
                    "config": {"mode": "dev"},
                    "routing_policy": {
                        "export_routes": {
                            "rebuilt_pdf": {
                                "selected_route": "pdf.direct",
                                "allowed_routes": ["pdf.direct"],
                                "route_candidates": ["pdf.direct"],
                                "source_types": ["epub"],
                            }
                        }
                    },
                },
                rollout_scope_json={"mode": "dev"},
            )
            bundle_service.activate_bundle(record.revision.id)
            routing_service = ExportRoutingService(runtime_bundle_service=bundle_service)

            with self.assertRaises(ExportRoutingError) as exc_info:
                routing_service.resolve_document_route(
                    document=document,
                    export_type=ExportType.REBUILT_PDF,
                )

        exc = exc_info.exception
        self.assertEqual(exc.expected_route_candidates, ["epub.rebuilt_pdf_via_html"])
        self.assertEqual(exc.route_evidence_json["runtime_bundle_revision_id"], record.revision.id)
        self.assertEqual(exc.route_evidence_json["selected_route"], "pdf.direct")
        self.assertEqual(exc.route_evidence_json["route_decision_source"], "bundle_policy")

    def test_resolve_document_route_falls_back_to_default_when_no_bundle_is_active(self) -> None:
        with self.session_factory() as session:
            document = self._seed_document(session)
            bundle_service = RuntimeBundleService(session, settings=self._settings())
            routing_service = ExportRoutingService(runtime_bundle_service=bundle_service)

            decision = routing_service.resolve_document_route(
                document=document,
                export_type=ExportType.REBUILT_PDF,
            )

            self.assertEqual(decision.selected_route, "epub.rebuilt_pdf_via_html")
            self.assertIsNone(decision.runtime_bundle_revision_id)
            self.assertEqual(decision.route_evidence_json["route_decision_source"], "default")
