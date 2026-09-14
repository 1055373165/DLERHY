import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.ocr import OcrPdfParser, OcrPdfTextExtractor, UvSuryaOcrRunner
from book_agent.ingestion.pdf.extract import PdfFileProfiler
from book_agent.ingestion.pdf.models import PdfExtraction


class OcrRuntimeTests(unittest.TestCase):
    def test_pdf_file_profiler_uses_page_count_hint_for_scanned_pdf_without_text_pages(self) -> None:
        profile = PdfFileProfiler().profile_from_extraction(
            PdfExtraction(
                title=None,
                author=None,
                metadata={
                    "pdf_extractor": "basic",
                    "page_count_hint": 58,
                },
                pages=[],
                outline_entries=[],
            )
        )

        self.assertEqual(profile.page_count, 58)
        self.assertEqual(profile.pdf_kind, "scanned_pdf")
        self.assertTrue(profile.ocr_required)

    def test_uv_surya_ocr_runner_writes_status_snapshots_during_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "ocr-output"
            output_dir.mkdir()
            status_path = Path(temp_dir) / "ocr-status.json"
            poll_results = [None, 0]

            class _FakeProcess:
                pid = 4321

                def poll(self):
                    result = poll_results.pop(0)
                    if result == 0:
                        (output_dir / "results.json").write_text(
                            json.dumps({"scan-sample": []}),
                            encoding="utf-8",
                        )
                    return result

            def _fake_popen(_command, stdout, stderr, text, env):
                self.assertTrue(text)
                self.assertIn("UV_CACHE_DIR", env)
                stdout.write("warming up model weights\n")
                stdout.flush()
                stderr.write("loading OCR runtime\n")
                stderr.flush()
                return _FakeProcess()

            with (
                patch.dict(
                    os.environ,
                    {
                        "BOOK_AGENT_OCR_STATUS_PATH": str(status_path),
                        "BOOK_AGENT_OCR_HEARTBEAT_SECONDS": "0.1",
                    },
                    clear=False,
                ),
                patch(
                    "book_agent.domain.structure.ocr.shutil.which",
                    side_effect=["/opt/homebrew/bin/uv", "/opt/homebrew/bin/python3.13"],
                ),
                patch("book_agent.domain.structure.ocr.subprocess.Popen", side_effect=_fake_popen),
                patch("book_agent.domain.structure.ocr.time.sleep", return_value=None),
            ):
                results_path = UvSuryaOcrRunner().run(
                    file_path="scan-sample.pdf",
                    output_dir=output_dir,
                )

            self.assertEqual(results_path, output_dir / "results.json")
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status_payload["state"], "succeeded")
            self.assertEqual(status_payload["pid"], 4321)
            self.assertEqual(status_payload["returncode"], 0)
            self.assertEqual(status_payload["output_snapshot"]["results_json_path"], str(results_path.resolve()))
            self.assertIn("warming up model weights", status_payload["stdout_tail"])
            self.assertIn("loading OCR runtime", status_payload["stderr_tail"])

    def test_uv_surya_ocr_runner_sets_uv_cache_dir_when_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            env = UvSuryaOcrRunner()._runtime_env()

        self.assertIn("UV_CACHE_DIR", env)
        self.assertTrue(Path(env["UV_CACHE_DIR"]).is_dir())

    def test_uv_surya_ocr_runner_times_out_when_max_runtime_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "ocr-output"
            output_dir.mkdir()
            status_path = Path(temp_dir) / "ocr-status.json"

            class _FakeProcess:
                pid = 9876
                terminated = False

                def poll(self):
                    return None

                def terminate(self):
                    self.terminated = True

                def wait(self, timeout=None):
                    return -15

            fake_process = _FakeProcess()

            def _fake_popen(_command, stdout, stderr, text, env):
                self.assertTrue(text)
                self.assertIn("UV_CACHE_DIR", env)
                return fake_process

            utcnow_values = iter(
                [
                    datetime.fromisoformat("2026-04-04T10:00:00+00:00"),
                    datetime.fromisoformat("2026-04-04T10:00:02+00:00"),
                    datetime.fromisoformat("2026-04-04T10:00:02+00:00"),
                    datetime.fromisoformat("2026-04-04T10:00:02+00:00"),
                ]
            )

            def _fake_utcnow():
                try:
                    return next(utcnow_values)
                except StopIteration:
                    return datetime.fromisoformat("2026-04-04T10:00:02+00:00")

            with (
                patch.dict(
                    os.environ,
                    {
                        "BOOK_AGENT_OCR_STATUS_PATH": str(status_path),
                        "BOOK_AGENT_OCR_HEARTBEAT_SECONDS": "0.1",
                        "BOOK_AGENT_OCR_MAX_RUNTIME_SECONDS": "1",
                    },
                    clear=False,
                ),
                patch(
                    "book_agent.domain.structure.ocr.shutil.which",
                    side_effect=["/opt/homebrew/bin/uv", "/opt/homebrew/bin/python3.13"],
                ),
                patch("book_agent.domain.structure.ocr.subprocess.Popen", side_effect=_fake_popen),
                patch("book_agent.domain.structure.ocr._utcnow", side_effect=_fake_utcnow),
            ):
                with self.assertRaisesRegex(RuntimeError, "exceeded max runtime 1.0s"):
                    UvSuryaOcrRunner().run(
                        file_path="scan-sample.pdf",
                        output_dir=output_dir,
                    )

            self.assertTrue(fake_process.terminated)
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status_payload["state"], "timed_out")
            self.assertEqual(status_payload["pid"], 9876)
            self.assertIn("exceeded max runtime 1.0s", status_payload["stderr_tail"])

    def test_ocr_pdf_text_extractor_chunks_large_pdf_runs_and_merges_pages(self) -> None:
        run_calls: list[str | None] = []

        def _fake_run(self, *, file_path, output_dir):
            run_calls.append(self.page_range)
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            start_page, end_page = (int(part) for part in str(self.page_range).split("-"))
            payload = {
                Path(file_path).stem: [
                    {
                        "image_bbox": [0, 0, 600, 800],
                        "text_lines": [
                            {
                                "text": f"Page {page_number}",
                                "confidence": 0.99,
                                "bbox": [72, 120, 220, 148],
                            }
                        ],
                    }
                    for page_number in range(start_page + 1, end_page + 2)
                ]
            }
            results_path = output_path / "results.json"
            results_path.write_text(json.dumps(payload), encoding="utf-8")
            return results_path

        with patch.object(UvSuryaOcrRunner, "run", new=_fake_run):
            extraction = OcrPdfTextExtractor(
                runner=UvSuryaOcrRunner(),
                chunk_page_count=32,
            ).extract("scan-sample.pdf", page_count=65)

        self.assertEqual(run_calls, ["0-31", "32-63", "64-64"])
        self.assertEqual(extraction.metadata["ocr_chunk_count"], 3)
        self.assertEqual(extraction.metadata["ocr_chunk_page_count"], 32)
        self.assertEqual(len(extraction.pages), 65)
        self.assertEqual(extraction.pages[0].blocks[0].text, "Page 1")
        self.assertEqual(extraction.pages[-1].blocks[0].text, "Page 65")

    def test_ocr_pdf_text_extractor_materializes_full_page_image_blocks(self) -> None:
        class _FakeRunner:
            def run(self, *, file_path, output_dir):
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)
                results_path = output_path / "results.json"
                results_path.write_text(
                    json.dumps(
                        {
                            Path(file_path).stem: [
                                {
                                    "image_bbox": [0, 0, 768, 1089],
                                    "text_lines": [
                                        {
                                            "text": "Scanned page text",
                                            "confidence": 0.99,
                                            "bbox": [72, 120, 220, 148],
                                        }
                                    ],
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                return results_path

        extraction = OcrPdfTextExtractor(runner=_FakeRunner(), chunk_page_count=32).extract(
            "scan-sample.pdf",
            page_count=1,
        )

        self.assertEqual(len(extraction.pages), 1)
        self.assertEqual(extraction.pages[0].blocks[0].text, "Scanned page text")
        self.assertEqual(len(extraction.pages[0].image_blocks), 1)
        self.assertEqual(extraction.pages[0].image_blocks[0].image_type, "scanned_page_image")
        self.assertEqual(extraction.pages[0].image_blocks[0].width_px, 768)
        self.assertEqual(extraction.pages[0].image_blocks[0].height_px, 1089)



class OcrPdfParserRecoveryServiceTests(unittest.TestCase):
    def test_default_recovery_service_uses_settings_figure_config_without_ocr_reextraction(self) -> None:
        figure_config = object()
        with (
            patch("book_agent.domain.structure.pdf._resolve_default_figure_cluster_config", return_value=figure_config),
            patch.dict(os.environ, {"BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION": "1"}),
        ):
            parser = OcrPdfParser()

        self.assertIs(parser.recovery_service._figure_cluster_config, figure_config)
        self.assertIsNone(parser.recovery_service._ocr_reextraction_adapter)


if __name__ == "__main__":
    unittest.main()
