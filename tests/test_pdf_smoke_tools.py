# ruff: noqa: E402

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.tools.pdf_smoke import (
    build_pdf_smoke_report,
    discover_pdf_smoke_candidates,
    evaluate_pdf_smoke_expectations,
    run_pdf_smoke_corpus,
)


PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_command(x: int, y: int, font_size: int, text: str) -> str:
    escaped = _pdf_escape(text)
    return f"BT /F1 {font_size} Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET"


def _write_pdf(path: Path, pages: list[list[str]], *, title: str, author: str) -> None:
    objects: list[str | None] = [None]

    def add_object(content: str) -> int:
        objects.append(content)
        return len(objects) - 1

    font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = add_object("")
    info_id = add_object(f"<< /Title ({_pdf_escape(title)}) /Author ({_pdf_escape(author)}) >>")

    page_ids: list[int] = []
    for commands in pages:
        content_stream = "\n".join(commands)
        content_bytes = content_stream.encode("latin1")
        content_id = add_object(
            f"<< /Length {len(content_bytes)} >>\nstream\n{content_stream}\nendstream"
        )
        page_id = add_object(
            "<< /Type /Page "
            f"/Parent {pages_id} 0 R "
            f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)

    objects[pages_id] = (
        "<< /Type /Pages "
        f"/Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_ids)} >>"
    )
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * len(objects)
    for object_id in range(1, len(objects)):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n{objects[object_id]}\nendobj\n".encode("latin1"))

    startxref = len(output)
    output.extend(f"xref\n0 {len(objects)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for object_id in range(1, len(objects)):
        output.extend(f"{offsets[object_id]:010d} 00000 n \n".encode("ascii"))

    output.extend(
        (
            f"trailer\n<< /Size {len(objects)} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\n"
            f"startxref\n{startxref}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(output))


def _write_low_risk_text_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 794, 10, "Signal vs Noise"),
            _text_command(72, 734, 22, "Chapter 1 Strategic Moats"),
            _text_command(72, 102, 12, "Pricing pow-"),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 794, 10, "Signal vs Noise"),
            _text_command(72, 714, 12, "er matters because durable advantages compound across cycles."),
            _text_command(72, 658, 12, "Teams that defend margins keep investing in product quality."),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Signal vs Noise", author="Test Author")


def _write_blank_pdf(path: Path) -> None:
    _write_pdf(path, [[]], title="Blank Sample", author="Test Author")


class PdfSmokeToolsTests(unittest.TestCase):
    def test_build_pdf_smoke_report_includes_parse_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "sample.pdf"
            _write_low_risk_text_pdf(pdf_path)

            report = build_pdf_smoke_report(pdf_path, include_parse_summary=True)

        self.assertEqual(report["profile"]["layout_risk"], "low")
        self.assertEqual(report["profile"]["extractor_kind"], "basic")
        self.assertEqual(report["bootstrap"]["status"], "succeeded")
        self.assertEqual(report["parse_summary"]["chapter_count"], 1)
        self.assertEqual(report["parse_summary"]["extractor_kind"], "basic")
        self.assertEqual(report["parse_summary"]["page_families"], {"1": "body", "2": "body"})

    def test_evaluate_pdf_smoke_expectations_reports_failures(self) -> None:
        report = {
            "profile": {"layout_risk": "low"},
            "bootstrap": {"status": "failed", "error": "layout_risk=high"},
        }

        failures = evaluate_pdf_smoke_expectations(
            report,
            [
                {"path": "profile.layout_risk", "equals": "high"},
                {"path": "bootstrap.error", "contains": "layout_risk=high"},
            ],
        )

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["path"], "profile.layout_risk")
        self.assertEqual(failures[0]["operator"], "equals")

    def test_run_pdf_smoke_corpus_writes_case_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "sample.pdf"
            output_dir = Path(tmpdir) / "reports"
            _write_low_risk_text_pdf(pdf_path)

            summary = run_pdf_smoke_corpus(
                [
                    {
                        "name": "low-risk",
                        "source_path": str(pdf_path),
                        "include_parse_summary": True,
                        "expectations": [
                            {"path": "profile.layout_risk", "equals": "low"},
                            {"path": "bootstrap.status", "equals": "succeeded"},
                            {"path": "parse_summary.page_families.1", "equals": "body"},
                        ],
                    }
                ],
                output_dir=output_dir,
            )

            report_path = Path(summary["cases"][0]["report_path"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["passed_case_count"], 1)
            self.assertEqual(summary["failed_case_count"], 0)
            self.assertTrue(report_path.exists())
            self.assertEqual(report["case_status"], "passed")
            self.assertEqual(report["profile"]["layout_risk"], "low")

    def test_run_pdf_smoke_corpus_skips_missing_optional_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "reports"

            summary = run_pdf_smoke_corpus(
                [
                    {
                        "name": "missing-optional",
                        "source_path": str(Path(tmpdir) / "missing.pdf"),
                        "optional": True,
                        "skip_reason": "local_sample_missing",
                        "expectations": [
                            {"path": "profile.layout_risk", "equals": "low"},
                        ],
                    }
                ],
                output_dir=output_dir,
            )

            report_path = Path(summary["cases"][0]["report_path"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["passed_case_count"], 0)
            self.assertEqual(summary["skipped_case_count"], 1)
            self.assertEqual(summary["failed_case_count"], 0)
            self.assertEqual(summary["cases"][0]["status"], "skipped")
            self.assertEqual(report["case_status"], "skipped")
            self.assertEqual(report["skip_reason"], "local_sample_missing")

    def test_discover_pdf_smoke_candidates_classifies_pass_and_ocr_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_low_risk_text_pdf(root / "low-risk.pdf")
            _write_blank_pdf(root / "blank.pdf")
            hidden_dir = root / ".git"
            hidden_dir.mkdir()
            _write_low_risk_text_pdf(hidden_dir / "hidden.pdf")

            summary = discover_pdf_smoke_candidates([root])

        self.assertEqual(summary["candidate_count"], 2)
        self.assertEqual(summary["candidate_kind_counts"]["pass_path"], 1)
        self.assertEqual(summary["candidate_kind_counts"]["ocr_path"], 1)
        self.assertEqual(summary["candidates"][0]["candidate_kind"], "pass_path")
        self.assertGreater(summary["candidates"][0]["candidate_score"], summary["candidates"][1]["candidate_score"])
        self.assertTrue(summary["candidates"][0]["recommended_for_manifest"])
        self.assertEqual(summary["recommended_case_count"], 1)
        self.assertEqual(summary["recommended_cases"][0]["expectations"][2]["equals"], "succeeded")
        self.assertTrue(summary["candidates"][0]["path"].endswith("low-risk.pdf"))
