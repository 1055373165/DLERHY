import os
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from book_agent.app.main import create_app
from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.app.runtime.document_run_executor import _is_retryable_exception
from book_agent.core.config import get_settings
from book_agent.domain.enums import (
    DocumentRunType,
    ExportStatus,
    ExportType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, WorkItem
from book_agent.services.export import ExportService
from book_agent.services.export_routing import ExportRoutingError, ExportRoutingService
from book_agent.services.run_execution import ClaimedRunWorkItem
from book_agent.services.runtime_bundle import RuntimeBundleService


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""

CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Business Strategy Handbook</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="chap1" />
  </spine>
</package>
"""

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter1.xhtml">Chapter One</a></li>
      </ol>
    </nav>
  </body>
</html>
"""

CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Pricing power matters. Strategy compounds.</p>
    <blockquote>A quoted paragraph.</blockquote>
  </body>
</html>
"""


class ReqEx02ExportMisroutingSelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.database_path = Path(self.tempdir.name) / "req-ex-02.db"

        self._old_env = {
            "BOOK_AGENT_DATABASE_URL": os.environ.get("BOOK_AGENT_DATABASE_URL"),
            "BOOK_AGENT_EXPORT_ROOT": os.environ.get("BOOK_AGENT_EXPORT_ROOT"),
            "BOOK_AGENT_RUNTIME_BUNDLE_ROOT": os.environ.get("BOOK_AGENT_RUNTIME_BUNDLE_ROOT"),
            "BOOK_AGENT_UPLOAD_ROOT": os.environ.get("BOOK_AGENT_UPLOAD_ROOT"),
            "BOOK_AGENT_TRANSLATION_BACKEND": os.environ.get("BOOK_AGENT_TRANSLATION_BACKEND"),
            "BOOK_AGENT_TRANSLATION_MODEL": os.environ.get("BOOK_AGENT_TRANSLATION_MODEL"),
        }
        os.environ["BOOK_AGENT_DATABASE_URL"] = f"sqlite+pysqlite:///{self.database_path}"
        os.environ["BOOK_AGENT_EXPORT_ROOT"] = str(Path(self.tempdir.name) / "exports")
        os.environ["BOOK_AGENT_RUNTIME_BUNDLE_ROOT"] = str(Path(self.tempdir.name) / "runtime-bundles")
        os.environ["BOOK_AGENT_UPLOAD_ROOT"] = str(Path(self.tempdir.name) / "uploads")
        os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
        os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
        get_settings.cache_clear()

        self.addCleanup(self._restore_env)
        self.app = create_app()
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self._stop_executor)

    def _restore_env(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    def _stop_executor(self) -> None:
        executor = getattr(self.app.state, "document_run_executor", None)
        if executor is not None:
            executor.stop()
            self.app.state.document_run_executor = None

    def _write_epub(self) -> Path:
        epub_path = Path(self.tempdir.name) / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        return epub_path

    def test_req_ex_02_export_misrouting_self_heal_closes_loop(self) -> None:
        epub_path = self._write_epub()

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        created = self.client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": DocumentRunType.TRANSLATE_FULL.value,
                "requested_by": "req-ex-02-test",
                "status_detail_json": {
                    "runtime_v2": {
                        "allowed_patch_surfaces": ["runtime_bundle"],
                    }
                },
                "budget": {
                    "max_auto_followup_attempts": 2,
                },
            },
        )
        self.assertEqual(created.status_code, 201)
        run_id = created.json()["run_id"]

        with self.app.state.session_factory() as session:
            document = session.get(Document, document_id)
            self.assertIsNotNone(document)
            assert document is not None

            bundle_service = RuntimeBundleService(session)
            misroute_record = bundle_service.publish_bundle(
                revision_name="req-ex-02-misroute",
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
            bundle_service.activate_bundle(misroute_record.revision.id)

            routing_service = ExportRoutingService(runtime_bundle_service=bundle_service)
            with self.assertRaises(ExportRoutingError) as exc_info:
                routing_service.resolve_document_route(
                    document=document,
                    export_type=ExportType.REBUILT_PDF,
                    runtime_bundle_revision_id=misroute_record.revision.id,
                )

            export_scope_id = str(uuid4())
            work_item = WorkItem(
                run_id=run_id,
                stage=WorkItemStage.EXPORT,
                scope_type=WorkItemScopeType.EXPORT,
                scope_id=export_scope_id,
                priority=100,
                status=WorkItemStatus.RETRYABLE_FAILED,
                runtime_bundle_revision_id=misroute_record.revision.id,
                input_version_bundle_json={
                    "document_id": document_id,
                    "export_type": ExportType.REBUILT_PDF.value,
                },
                output_artifact_refs_json={},
                error_detail_json={},
            )
            session.add(work_item)
            session.flush()

            claimed = ClaimedRunWorkItem(
                run_id=run_id,
                work_item_id=work_item.id,
                stage=WorkItemStage.EXPORT.value,
                scope_type=WorkItemScopeType.EXPORT.value,
                scope_id=export_scope_id,
                attempt=1,
                priority=100,
                lease_token=f"lease-{uuid4()}",
                worker_name="req-ex-02-test",
                worker_instance_id=f"worker-{uuid4()}",
                lease_expires_at=datetime.now(timezone.utc).isoformat(),
            )
            executor = DocumentRunExecutor(
                session_factory=self.app.state.session_factory,
                export_root=self.app.state.export_root,
                translation_worker=None,
            )
            executor._recover_export_misrouting(
                session=session,
                run_id=run_id,
                claimed=claimed,
                exc=exc_info.exception,
            )
            session.commit()

        with self.app.state.session_factory() as session:
            execution = executor._run_execution_service(session)
            repair_claimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="test.repair",
                worker_instance_id="repair-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(repair_claimed)
            assert repair_claimed is not None
            session.commit()

        executor._execute_repair_work_item(run_id, repair_claimed)

        def _fake_render(_service, _html_path, pdf_path) -> None:
            Path(pdf_path).write_bytes(b"%PDF-1.4\n% recovered\n")

        with (
            patch.object(ExportService, "_enforce_gate", autospec=True, return_value=None),
            patch.object(
                ExportService,
                "_render_rebuilt_pdf_from_html",
                autospec=True,
                side_effect=_fake_render,
            ),
        ):
            export_response = self.client.post(
                f"/v1/documents/{document_id}/export",
                json={
                    "export_type": ExportType.REBUILT_PDF.value,
                    "auto_execute_followup_on_gate": True,
                    "max_auto_followup_attempts": 3,
                },
            )
        self.assertEqual(export_response.status_code, 200)
        export_payload = export_response.json()
        runtime_v2_context = export_payload["runtime_v2_context"]
        self.assertIsNotNone(runtime_v2_context)
        assert runtime_v2_context is not None
        self.assertTrue(runtime_v2_context["recovered"])
        self.assertEqual(
            runtime_v2_context["incident_id"],
            runtime_v2_context["last_export_route_recovery"]["incident_id"],
        )
        self.assertEqual(
            runtime_v2_context["proposal_id"],
            runtime_v2_context["last_export_route_recovery"]["proposal_id"],
        )
        self.assertEqual(
            runtime_v2_context["bundle_revision_id"],
            runtime_v2_context["last_export_route_recovery"]["bundle_revision_id"],
        )
        self.assertEqual(runtime_v2_context["replay_work_item_id"], work_item.id)
        self.assertEqual(runtime_v2_context["bound_work_item_ids"], [work_item.id])
        self.assertEqual(runtime_v2_context["repair_blockage_state"], "ready_to_continue")
        self.assertFalse(runtime_v2_context["repair_blocked"])
        self.assertEqual(runtime_v2_context["repair_blockage_source"], "last_export_route_recovery")
        self.assertEqual(
            runtime_v2_context["repair_blockage"]["state"],
            runtime_v2_context["last_export_route_recovery"]["repair_blockage"]["state"],
        )

        exports_dashboard = self.client.get(
            f"/v1/documents/{document_id}/exports",
            params={"export_type": ExportType.REBUILT_PDF.value, "status": ExportStatus.SUCCEEDED.value},
        )
        self.assertEqual(exports_dashboard.status_code, 200)
        dashboard_payload = exports_dashboard.json()
        self.assertGreaterEqual(dashboard_payload["record_count"], 1)
        export_id = dashboard_payload["records"][0]["export_id"]
        dashboard_record = next(record for record in dashboard_payload["records"] if record["export_id"] == export_id)
        self.assertIsNotNone(dashboard_record["runtime_v2_context"])
        dashboard_runtime_v2 = dashboard_record["runtime_v2_context"]
        assert dashboard_runtime_v2 is not None
        self.assertEqual(dashboard_runtime_v2["incident_id"], runtime_v2_context["incident_id"])
        self.assertEqual(dashboard_runtime_v2["proposal_id"], runtime_v2_context["proposal_id"])
        self.assertEqual(dashboard_runtime_v2["bundle_revision_id"], runtime_v2_context["bundle_revision_id"])
        self.assertEqual(
            dashboard_runtime_v2["repair_blockage_state"],
            runtime_v2_context["repair_blockage_state"],
        )
        self.assertEqual(
            dashboard_runtime_v2["repair_blockage_source"],
            runtime_v2_context["repair_blockage_source"],
        )

        export_detail = self.client.get(f"/v1/documents/{document_id}/exports/{export_id}")
        self.assertEqual(export_detail.status_code, 200)
        export_detail_payload = export_detail.json()
        self.assertIsNotNone(export_detail_payload["runtime_v2_context"])
        detail_runtime_v2 = export_detail_payload["runtime_v2_context"]
        assert detail_runtime_v2 is not None
        self.assertEqual(detail_runtime_v2["incident_id"], runtime_v2_context["incident_id"])
        self.assertEqual(detail_runtime_v2["proposal_id"], runtime_v2_context["proposal_id"])
        self.assertEqual(detail_runtime_v2["bundle_revision_id"], runtime_v2_context["bundle_revision_id"])
        self.assertEqual(detail_runtime_v2["replay_work_item_id"], work_item.id)
        self.assertEqual(detail_runtime_v2["repair_blockage_state"], runtime_v2_context["repair_blockage_state"])

        summary = self.client.get(f"/v1/documents/{document_id}")
        self.assertEqual(summary.status_code, 200)
        summary_payload = summary.json()
        self.assertIsNotNone(summary_payload["runtime_v2_context"])
        summary_runtime_v2 = summary_payload["runtime_v2_context"]
        assert summary_runtime_v2 is not None
        self.assertEqual(summary_runtime_v2["incident_id"], runtime_v2_context["incident_id"])
        self.assertEqual(
            summary_runtime_v2["active_runtime_bundle_revision_id"],
            runtime_v2_context["active_runtime_bundle_revision_id"],
        )
        self.assertEqual(summary_runtime_v2["replay_work_item_id"], work_item.id)
        self.assertEqual(summary_runtime_v2["repair_blockage_state"], runtime_v2_context["repair_blockage_state"])

        run_events = self.client.get(f"/v1/runs/{run_id}/events")
        self.assertEqual(run_events.status_code, 200)
        run_events_payload = run_events.json()
        replay_event = next(
            entry
            for entry in run_events_payload["entries"]
            if entry["event_type"] == "runtime_v2.export.replayed"
        )
        self.assertEqual(replay_event["work_item_id"], work_item.id)
        self.assertEqual(replay_event["payload_json"]["incident_id"], runtime_v2_context["incident_id"])
        self.assertEqual(replay_event["payload_json"]["proposal_id"], runtime_v2_context["proposal_id"])
        self.assertEqual(replay_event["payload_json"]["bundle_revision_id"], runtime_v2_context["bundle_revision_id"])
        self.assertEqual(replay_event["payload_json"]["replay_work_item_id"], work_item.id)
        self.assertEqual(
            replay_event["payload_json"]["bound_work_item_ids"],
            [work_item.id],
        )

        with self.app.state.session_factory() as session:
            run = session.get(DocumentRun, run_id)
            self.assertIsNotNone(run)
            assert run is not None
            runtime_v2 = dict((run.status_detail_json or {}).get("runtime_v2") or {})
            self.assertEqual(runtime_v2["last_export_route_recovery"]["bound_work_item_ids"], [work_item.id])
            self.assertEqual(runtime_v2["last_export_route_evidence"]["selected_route"], "pdf.direct")
            self.assertTrue(_is_retryable_exception(exc_info.exception))
