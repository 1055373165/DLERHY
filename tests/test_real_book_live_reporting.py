import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.real_book_live_reporting_common import (
    CURRENT_TELEMETRY_GENERATION,
    build_telemetry_compatibility,
)
from scripts.run_real_book_live import _enrich_report_runtime_snapshot, main
from scripts.watch_real_book_live import _build_monitor_snapshot


class RealBookLiveReportingTests(unittest.TestCase):
    def test_enrich_report_runtime_snapshot_persists_ocr_and_db_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "book-agent.db"
            report_path = root / "report.json"
            ocr_status_path = root / "report.ocr.json"

            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("create table documents (id text, status text)")
                connection.execute("create table work_items (id text, status text)")
                connection.execute("create table translation_packets (id text, status text)")
                connection.execute("insert into documents values ('doc-1', 'parsed')")
                connection.execute("insert into work_items values ('wi-1', 'succeeded')")
                connection.execute("insert into work_items values ('wi-2', 'retryable_failed')")
                connection.execute("insert into translation_packets values ('pkt-1', 'built')")
                connection.execute("insert into translation_packets values ('pkt-2', 'translated')")
                connection.commit()

            ocr_status_path.write_text(
                json.dumps(
                    {
                        "state": "running",
                        "page_range": "0-31",
                        "output_snapshot": {
                            "path": "/tmp/fake-ocr-output",
                            "exists": True,
                            "results_json_exists": False,
                        },
                        "stderr_tail": "Recognizing Text: 12% | 120/1000",
                    }
                ),
                encoding="utf-8",
            )

            report = {
                "source_path": str((root / "sample.pdf").resolve()),
                "database_url": f"sqlite+pysqlite:///{db_path}",
                "ocr_status_path": str(ocr_status_path),
                "bootstrap_in_progress": True,
            }

            _enrich_report_runtime_snapshot(report, report_path=report_path)

            self.assertEqual(report["stage"], "bootstrap_ocr_running")
            self.assertEqual(report["db_counts"]["documents"], 1)
            self.assertEqual(report["work_item_status_counts"]["retryable_failed"], 1)
            self.assertEqual(report["translation_packet_status_counts"]["translated"], 1)
            self.assertEqual(report["ocr_status"]["page_range"], "0-31")
            self.assertEqual(report["ocr_progress"]["current"], 120)
            self.assertEqual(report["ocr_progress"]["total"], 1000)
            self.assertEqual(report["telemetry_generation"], CURRENT_TELEMETRY_GENERATION)
            self.assertTrue(report["telemetry_compatibility"]["compatible_with_phase3"])
            self.assertIsNone(report["recommended_recovery_action"])

    def test_main_records_bootstrap_failure_without_leaving_report_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "sample.pdf"
            source_path.write_bytes(b"%PDF-1.4 fake")
            report_path = root / "report.json"
            export_root = root / "exports"
            db_path = root / "book-agent.db"

            class _FailingService:
                def bootstrap_epub(self, _source_path):
                    raise RuntimeError("ocr bootstrap exploded")

            with patch("scripts.run_real_book_live._new_service", return_value=_FailingService()):
                exit_code = main(
                    [
                        "--source-path",
                        str(source_path),
                        "--database-url",
                        f"sqlite+pysqlite:///{db_path}",
                        "--export-root",
                        str(export_root),
                        "--report-path",
                        str(report_path),
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(payload["bootstrap_in_progress"])
            self.assertEqual(payload["error"]["stage"], "bootstrap")
            self.assertEqual(payload["error"]["message"], "ocr bootstrap exploded")
            self.assertEqual(payload["error"]["failure_taxonomy"]["family"], "ocr_failure")
            self.assertEqual(
                payload["error"]["recommended_recovery_action"],
                "fix_ocr_runtime_and_rerun_bootstrap",
            )
            self.assertEqual(payload["stage"], "finished")

    def test_enrich_report_runtime_snapshot_classifies_provider_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "book-agent.db"
            report_path = root / "report.json"

            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("create table documents (id text, status text)")
                connection.execute("create table work_items (id text, status text)")
                connection.execute("create table translation_packets (id text, status text)")
                connection.execute("insert into documents values ('doc-1', 'active')")
                connection.commit()

            report = {
                "source_path": str((root / "sample.pdf").resolve()),
                "database_url": f"sqlite+pysqlite:///{db_path}",
                "run": {
                    "status": "paused",
                    "stop_reason": "provider.insufficient_balance",
                },
                "translate": {
                    "last_failure": {
                        "error_message": 'Provider returned HTTP 402: {"error":{"message":"Insufficient Balance"}}'
                    }
                },
            }

            _enrich_report_runtime_snapshot(report, report_path=report_path)

            self.assertEqual(report["failure_taxonomy"]["family"], "provider_exhaustion")
            self.assertEqual(
                report["recommended_recovery_action"],
                "top_up_provider_balance_and_resume",
            )

    def test_watch_snapshot_marks_legacy_report_generation_and_surfaces_recovery_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report_path = root / "report.json"
            report_payload = {
                "started_at": "2026-03-18T07:41:20+00:00",
                "source_path": str((root / "sample.pdf").resolve()),
                "database_url": f"sqlite+pysqlite:///{root / 'book-agent.db'}",
                "run": {
                    "status": "paused",
                    "stop_reason": "provider.insufficient_balance",
                },
                "translate": {
                    "last_failure": {
                        "error_message": 'Provider returned HTTP 402: {"error":{"message":"Insufficient Balance"}}'
                    }
                },
            }
            report_path.write_text(json.dumps(report_payload), encoding="utf-8")

            with patch("scripts.watch_real_book_live._list_processes", return_value=[]):
                snapshot = _build_monitor_snapshot(report_payload, report_path=report_path)

            self.assertEqual(snapshot["failure_taxonomy"]["family"], "provider_exhaustion")
            self.assertEqual(
                snapshot["recommended_recovery_action"],
                "top_up_provider_balance_and_resume",
            )
            self.assertEqual(
                snapshot["telemetry_compatibility"]["generation"],
                "legacy-report-generation",
            )
            self.assertIn("stage", snapshot["telemetry_compatibility"]["missing_fields"])

    def test_build_telemetry_compatibility_accepts_current_generation_payload(self) -> None:
        payload = {
            "telemetry_generation": CURRENT_TELEMETRY_GENERATION,
            "stage": "finished",
            "database_path": "/tmp/book-agent.db",
            "db_counts": {},
            "work_item_status_counts": {},
            "translation_packet_status_counts": {},
            "ocr_status": None,
            "ocr_progress": None,
            "resume_from_run_id": None,
            "resume_from_status": None,
            "retry_from_run_id": None,
            "retry_from_status": None,
            "failure_taxonomy": None,
            "recommended_recovery_action": None,
        }

        compatibility = build_telemetry_compatibility(payload)

        self.assertTrue(compatibility["compatible_with_phase3"])
        self.assertEqual(compatibility["missing_fields"], [])


if __name__ == "__main__":
    unittest.main()
