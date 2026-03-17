# ruff: noqa: E402

import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.app.main import create_app
from book_agent.domain.enums import (
    ActionActorType,
    ActionStatus,
    ActionType,
    Detector,
    IssueStatus,
    JobScopeType,
    LockLevel,
    MemoryScopeType,
    RootCauseLayer,
    SentenceStatus,
    Severity,
    SnapshotType,
    TermStatus,
    TermType,
)
from book_agent.domain.models import Chapter, Document, IssueAction, MemorySnapshot, Sentence, TermEntry
from book_agent.domain.models.review import Export, ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationPacket, TranslationRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.workers.contracts import AlignmentSuggestion, TranslationTargetSegment, TranslationWorkerOutput
from book_agent.workers.translator import TranslationTask, TranslationWorkerMetadata


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

EMPTY_CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <section id="frontmatter">
      <img src="cover.png" alt="Cover" />
    </section>
  </body>
</html>
"""

LITERAL_TAG_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Tokens starting with <think>. Keep the token literal.</p>
  </body>
</html>
"""

CODE_CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Use the example carefully.</p>
    <pre class="code-area">def run_agent():
    return "ok"

print(run_agent())</pre>
  </body>
</html>
"""

STRUCTURED_ARTIFACT_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:m="http://www.w3.org/1998/Math/MathML">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <div id="fig-1" class="browsable-container figure-container">
      <img src="images/agent-loop.png" alt="Agent loop architecture" />
      <h5>Figure 1.1 Agent loop architecture</h5>
    </div>
    <table id="tbl-1">
      <tr><th>Tier</th><th>Latency</th></tr>
      <tr><td>Basic</td><td>Slow</td></tr>
    </table>
    <m:math id="eq-1"><m:mi>x</m:mi><m:mo>=</m:mo><m:mn>1</m:mn></m:math>
    <p>https://example.com/agent-docs</p>
  </body>
</html>
"""

IMAGE_ONLY_FIGURE_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <div id="cover" class="browsable-container figure-container">
      <img src="images/cover.png" alt="Cover illustration" />
    </div>
  </body>
</html>
"""


def _content_opf_with_chapters(chapters: list[tuple[str, str]]) -> str:
    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />',
        *[
            f'<item id="chap{index}" href="{href}" media-type="application/xhtml+xml" />'
            for index, (_, href) in enumerate(chapters, start=1)
        ],
    ]
    spine_items = [
        f'<itemref idref="chap{index}" />'
        for index, _chapter in enumerate(chapters, start=1)
    ]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Business Strategy Handbook</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    {' '.join(manifest_items)}
  </manifest>
  <spine>
    {' '.join(spine_items)}
  </spine>
</package>
"""


def _nav_xhtml_with_chapters(chapters: list[tuple[str, str]]) -> str:
    nav_items = "\n".join(
        f'        <li><a href="{href}">{title}</a></li>'
        for title, href in chapters
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
{nav_items}
      </ol>
    </nav>
  </body>
</html>
"""


class TermAwareWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="TermAwareWorker",
            model_name="term-aware-test",
            prompt_version="test.term-aware.v1",
            runtime_config={"mode": "test"},
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        relevant_terms = {
            term.source_term.lower(): term.target_term
            for term in task.context_packet.relevant_terms
        }
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            if "Pricing power" in sentence.source_text:
                target_term = relevant_terms.get("pricing power", "定价能力")
                text = f"{target_term}很重要。"
            else:
                text = f"译文::{sentence.source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.99,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.99,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class LowConfidenceWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="LowConfidenceWorker",
            model_name="low-confidence-test",
            prompt_version="test.low-confidence.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        low_confidence_flags = []
        for sentence in task.current_sentences:
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=f"译文::{sentence.source_text}",
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.55,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.9,
                )
            )
            if "Pricing power" in sentence.source_text:
                low_confidence_flags.append({"sentence_id": sentence.id, "reason": "ambiguous_term"})
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
            low_confidence_flags=low_confidence_flags,
        )


class FormatPollutionWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="FormatPollutionWorker",
            model_name="format-pollution-test",
            prompt_version="test.format-pollution.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            text = "<span>污染</span>" if "Pricing power" in sentence.source_text else f"译文::{sentence.source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.9,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.99,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class LiteralTagWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="LiteralTagWorker",
            model_name="literal-tag-test",
            prompt_version="test.literal-tag.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            if "<think>" in sentence.source_text:
                text = "以<think>开头的标记要保留字面形式。"
            else:
                text = f"译文::{sentence.source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.99,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class ApiWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        sqlite_path = Path(self.tempdir.name) / "book-agent.db"
        self.engine = build_engine(
            f"sqlite+pysqlite:///{sqlite_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.app = create_app()
        self.app.state.session_factory = self.session_factory
        self.app.state.export_root = str(Path(self.tempdir.name) / "exports")
        self.app.state.upload_root = str(Path(self.tempdir.name) / "uploads")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def _write_epub(self) -> Path:
        epub_path = Path(self.tempdir.name) / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        return epub_path

    def _write_epub_with_chapters(
        self,
        chapters: list[tuple[str, str, str]],
        *,
        extra_files: dict[str, str | bytes] | None = None,
    ) -> Path:
        toc_entries = [(title, href) for title, href, _content in chapters]
        fingerprint_input = "|".join(
            f"{title}:{href}:{sha256(content.encode('utf-8')).hexdigest()}"
            for title, href, content in chapters
        )
        epub_name = f"multi-chapter-{sha256(fingerprint_input.encode('utf-8')).hexdigest()[:12]}.epub"
        epub_path = Path(self.tempdir.name) / epub_name
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", _content_opf_with_chapters(toc_entries))
            archive.writestr("OEBPS/nav.xhtml", _nav_xhtml_with_chapters(toc_entries))
            for _title, href, content in chapters:
                archive.writestr(f"OEBPS/{href}", content)
            for asset_path, content in (extra_files or {}).items():
                archive.writestr(asset_path, content)
        return epub_path

    def _wait_for_run_terminal(self, run_id: str, *, timeout_seconds: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = self.client.get(f"/v1/runs/{run_id}")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            if payload["status"] in {"succeeded", "failed", "paused", "cancelled"}:
                return payload
            time.sleep(0.2)
        self.fail(f"Run {run_id} did not reach terminal state within {timeout_seconds} seconds.")

    def test_document_api_happy_path(self) -> None:
        epub_path = self._write_epub()

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        bootstrap_data = bootstrap.json()
        document_id = bootstrap_data["document_id"]
        self.assertEqual(bootstrap_data["chapter_count"], 1)
        self.assertEqual(bootstrap_data["packet_count"], 3)

        blocked_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(blocked_export.status_code, 409)

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)
        self.assertEqual(translate.json()["translated_packet_count"], 3)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["total_issue_count"], 0)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(export.status_code, 200)
        export_data = export.json()
        self.assertEqual(export_data["document_status"], "exported")
        self.assertEqual(len(export_data["chapter_results"]), 1)
        self.assertTrue(Path(export_data["chapter_results"][0]["file_path"]).exists())
        self.assertTrue(Path(export_data["chapter_results"][0]["manifest_path"]).exists())

        summary = self.client.get(f"/v1/documents/{document_id}")
        self.assertEqual(summary.status_code, 200)
        summary_data = summary.json()
        self.assertEqual(summary_data["status"], "exported")
        self.assertIsNotNone(summary_data["chapters"][0]["quality_summary"])
        self.assertTrue(summary_data["chapters"][0]["quality_summary"]["coverage_ok"])

    def test_bootstrap_upload_accepts_epub_file(self) -> None:
        epub_path = self._write_epub()

        with epub_path.open("rb") as handle:
            response = self.client.post(
                "/v1/documents/bootstrap-upload",
                files={"source_file": ("uploaded-book.epub", handle, "application/epub+zip")},
            )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["source_type"], "epub")
        self.assertEqual(payload["title"], "Business Strategy Handbook")
        uploaded_files = list(Path(self.app.state.upload_root).rglob("uploaded-book.epub"))
        self.assertEqual(len(uploaded_files), 1)

    def test_translate_full_run_executes_review_and_exports_in_background(self) -> None:
        epub_path = self._write_epub()

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        created = self.client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": "translate_full",
                "requested_by": "api-test",
                "status_detail_json": {"source": "api-test"},
            },
        )
        self.assertEqual(created.status_code, 201)
        run_id = created.json()["run_id"]

        resumed = self.client.post(
            f"/v1/runs/{run_id}/resume",
            json={"actor_id": "api-test", "note": "start run"},
        )
        self.assertEqual(resumed.status_code, 200)

        terminal = self._wait_for_run_terminal(run_id)
        self.assertEqual(terminal["status"], "succeeded")

        summary = self.client.get(f"/v1/documents/{document_id}")
        self.assertEqual(summary.status_code, 200)
        summary_payload = summary.json()
        self.assertTrue(summary_payload["merged_export_ready"])
        self.assertEqual(summary_payload["chapter_bilingual_export_count"], 1)
        chapter_id = summary_payload["chapters"][0]["chapter_id"]
        self.assertTrue(summary_payload["chapters"][0]["bilingual_export_ready"])

        merged_download = self.client.get(
            f"/v1/documents/{document_id}/exports/download",
            params={"export_type": "merged_html"},
        )
        self.assertEqual(merged_download.status_code, 200)
        self.assertIn("application/zip", merged_download.headers["content-type"])
        with zipfile.ZipFile(BytesIO(merged_download.content)) as archive:
            names = archive.namelist()
        self.assertIn(f"{document_id}-analysis-bundle/merged-document.html", names)
        self.assertIn(f"{document_id}-analysis-bundle/bilingual-{chapter_id}.html", names)

        chapter_download = self.client.get(
            f"/v1/documents/{document_id}/chapters/{chapter_id}/exports/download",
            params={"export_type": "bilingual_html"},
        )
        self.assertEqual(chapter_download.status_code, 200)
        self.assertIn("text/html", chapter_download.headers["content-type"])

        history = self.client.get("/v1/documents/history", params={"limit": 10, "offset": 0})
        self.assertEqual(history.status_code, 200)
        history_payload = history.json()
        self.assertEqual(history_payload["record_count"], 1)
        self.assertEqual(history_payload["entries"][0]["document_id"], document_id)
        self.assertTrue(history_payload["entries"][0]["merged_export_ready"])
        self.assertEqual(history_payload["entries"][0]["chapter_bilingual_export_count"], 1)

    def test_document_history_supports_search_and_filters(self) -> None:
        first_epub_path = self._write_epub_with_chapters(
            [
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
            ]
        )
        first_bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(first_epub_path)})
        self.assertEqual(first_bootstrap.status_code, 201)
        first_document_id = first_bootstrap.json()["document_id"]

        second_epub_path = self._write_epub_with_chapters(
            [
                ("Chapter Two", "chapter2.xhtml", CHAPTER_XHTML.replace("Chapter One", "Chapter Two")),
            ]
        )
        second_bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(second_epub_path)})
        self.assertEqual(second_bootstrap.status_code, 201)
        second_document_id = second_bootstrap.json()["document_id"]

        created = self.client.post(
            "/v1/runs",
            json={
                "document_id": first_document_id,
                "run_type": "translate_full",
                "requested_by": "api-test",
                "status_detail_json": {"source": "history-filter-test"},
            },
        )
        self.assertEqual(created.status_code, 201)
        run_id = created.json()["run_id"]

        resumed = self.client.post(
            f"/v1/runs/{run_id}/resume",
            json={"actor_id": "api-test", "note": "start run"},
        )
        self.assertEqual(resumed.status_code, 200)
        terminal = self._wait_for_run_terminal(run_id)
        self.assertEqual(terminal["status"], "succeeded")

        history = self.client.get("/v1/documents/history", params={"limit": 10, "offset": 0})
        self.assertEqual(history.status_code, 200)
        history_payload = history.json()
        self.assertEqual(history_payload["total_count"], 2)
        self.assertEqual(history_payload["record_count"], 2)

        query_history = self.client.get(
            "/v1/documents/history",
            params={"limit": 10, "offset": 0, "query": first_document_id[:8]},
        )
        self.assertEqual(query_history.status_code, 200)
        query_payload = query_history.json()
        self.assertEqual(query_payload["total_count"], 1)
        self.assertEqual(query_payload["entries"][0]["document_id"], first_document_id)

        exported_history = self.client.get(
            "/v1/documents/history",
            params={"limit": 10, "offset": 0, "status": "exported"},
        )
        self.assertEqual(exported_history.status_code, 200)
        exported_payload = exported_history.json()
        self.assertEqual(exported_payload["total_count"], 1)
        self.assertEqual(exported_payload["entries"][0]["document_id"], first_document_id)

        active_history = self.client.get(
            "/v1/documents/history",
            params={"limit": 10, "offset": 0, "status": "active"},
        )
        self.assertEqual(active_history.status_code, 200)
        active_payload = active_history.json()
        self.assertEqual(active_payload["total_count"], 1)
        self.assertEqual(active_payload["entries"][0]["document_id"], second_document_id)

        run_history = self.client.get(
            "/v1/documents/history",
            params={"limit": 10, "offset": 0, "latest_run_status": "succeeded"},
        )
        self.assertEqual(run_history.status_code, 200)
        run_payload = run_history.json()
        self.assertEqual(run_payload["total_count"], 1)
        self.assertEqual(run_payload["entries"][0]["document_id"], first_document_id)

        merged_ready_history = self.client.get(
            "/v1/documents/history",
            params={"limit": 10, "offset": 0, "merged_export_ready": "true"},
        )
        self.assertEqual(merged_ready_history.status_code, 200)
        merged_ready_payload = merged_ready_history.json()
        self.assertEqual(merged_ready_payload["total_count"], 1)
        self.assertEqual(merged_ready_payload["entries"][0]["document_id"], first_document_id)

        merged_pending_history = self.client.get(
            "/v1/documents/history",
            params={"limit": 10, "offset": 0, "merged_export_ready": "false"},
        )
        self.assertEqual(merged_pending_history.status_code, 200)
        merged_pending_payload = merged_pending_history.json()
        self.assertEqual(merged_pending_payload["total_count"], 1)
        self.assertEqual(merged_pending_payload["entries"][0]["document_id"], second_document_id)

    def test_retry_run_restarts_pipeline_with_previous_lineage(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        created = self.client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": "translate_full",
                "requested_by": "api-test",
                "status_detail_json": {"source": "retry-test"},
                "budget": {"max_parallel_workers": 2},
            },
        )
        self.assertEqual(created.status_code, 201)
        original_run_id = created.json()["run_id"]

        cancelled = self.client.post(
            f"/v1/runs/{original_run_id}/cancel",
            json={"actor_id": "api-test", "note": "cancel before start"},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")

        retried = self.client.post(
            f"/v1/runs/{original_run_id}/retry",
            json={"actor_id": "api-retry", "note": "retry cancelled run"},
        )
        self.assertEqual(retried.status_code, 200)
        retry_payload = retried.json()
        self.assertNotEqual(retry_payload["run_id"], original_run_id)
        self.assertEqual(retry_payload["document_id"], document_id)
        self.assertEqual(retry_payload["status"], "running")
        self.assertEqual(retry_payload["resume_from_run_id"], original_run_id)
        self.assertEqual(retry_payload["requested_by"], "api-retry")
        self.assertEqual(retry_payload["budget"]["max_parallel_workers"], 2)

        terminal = self._wait_for_run_terminal(retry_payload["run_id"])
        self.assertEqual(terminal["status"], "succeeded")
        self.assertEqual(terminal["resume_from_run_id"], original_run_id)

        history = self.client.get(
            "/v1/documents/history",
            params={"limit": 10, "offset": 0, "latest_run_status": "succeeded"},
        )
        self.assertEqual(history.status_code, 200)
        history_payload = history.json()
        self.assertEqual(history_payload["total_count"], 1)
        self.assertEqual(history_payload["entries"][0]["document_id"], document_id)
        self.assertEqual(history_payload["entries"][0]["latest_run_id"], retry_payload["run_id"])

    def test_export_download_bundles_multi_chapter_exports_as_zip(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
                ("Chapter Two", "chapter2.xhtml", CHAPTER_XHTML.replace("Chapter One", "Chapter Two")),
            ]
        )

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(export.status_code, 200)

        download = self.client.get(
            f"/v1/documents/{document_id}/exports/download",
            params={"export_type": "bilingual_html"},
        )
        self.assertEqual(download.status_code, 200)
        self.assertIn("application/zip", download.headers["content-type"])
        self.assertIn(".zip", download.headers["content-disposition"])

        with zipfile.ZipFile(BytesIO(download.content)) as archive:
            names = archive.namelist()

        self.assertEqual(len([name for name in names if name.endswith(".html")]), 2)
        self.assertTrue(all(name.startswith(f"{document_id}-bilingual_html/") for name in names))

    def test_export_succeeds_with_empty_frontmatter_chapter(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("", "welcome.xhtml", EMPTY_CHAPTER_XHTML),
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
            ]
        )

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]
        self.assertEqual(bootstrap.json()["chapter_count"], 2)

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)
        self.assertEqual(translate.json()["translated_packet_count"], 3)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        review_data = review.json()
        self.assertEqual(review_data["total_issue_count"], 0)
        self.assertEqual(len(review_data["chapter_results"]), 2)
        self.assertTrue(any(result["issue_count"] == 0 for result in review_data["chapter_results"]))

        review_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(review_export.status_code, 200)
        self.assertEqual(len(review_export.json()["chapter_results"]), 2)

        bilingual_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(bilingual_export.status_code, 200)
        export_data = bilingual_export.json()
        self.assertEqual(export_data["document_status"], "exported")
        self.assertEqual(len(export_data["chapter_results"]), 2)

        summary = self.client.get(f"/v1/documents/{document_id}")
        self.assertEqual(summary.status_code, 200)
        summary_data = summary.json()
        frontmatter = next(chapter for chapter in summary_data["chapters"] if chapter["packet_count"] == 0)
        self.assertEqual(frontmatter["sentence_count"], 0)
        self.assertEqual(frontmatter["status"], "exported")
        self.assertIsNotNone(frontmatter["quality_summary"])
        self.assertTrue(frontmatter["quality_summary"]["coverage_ok"])

    def test_review_action_can_be_executed_via_api(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        with self.session_factory() as session:
            sentence_id = session.scalars(
                select(Sentence.id).where(Sentence.source_text.like("Pricing power%"))
            ).one()
            session.add(
                TermEntry(
                    id="00000000-0000-0000-0000-000000000010",
                    document_id=document_id,
                    scope_type=MemoryScopeType.GLOBAL,
                    scope_id=None,
                    source_term="pricing power",
                    target_term="定价权",
                    term_type=TermType.CONCEPT,
                    lock_level=LockLevel.LOCKED,
                    status=TermStatus.ACTIVE,
                    evidence_sentence_id=sentence_id,
                    version=1,
                )
            )
            session.commit()

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertGreaterEqual(review.json()["total_issue_count"], 1)

        with self.session_factory() as session:
            action_id = session.query(IssueAction.id).first()[0]

        action = self.client.post(f"/v1/actions/{action_id}/execute")
        self.assertEqual(action.status_code, 200)
        action_data = action.json()
        self.assertEqual(action_data["status"], "completed")
        self.assertGreaterEqual(action_data["invalidation_count"], 1)

    def test_execute_action_with_followup_reruns_and_resolves_issue(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        with self.session_factory() as session:
            sentence_id = session.scalars(
                select(Sentence.id).where(Sentence.source_text.like("Pricing power%"))
            ).one()
            session.add(
                TermEntry(
                    id="00000000-0000-0000-0000-000000000011",
                    document_id=document_id,
                    scope_type=MemoryScopeType.GLOBAL,
                    scope_id=None,
                    source_term="pricing power",
                    target_term="定价权",
                    term_type=TermType.CONCEPT,
                    lock_level=LockLevel.LOCKED,
                    status=TermStatus.ACTIVE,
                    evidence_sentence_id=sentence_id,
                    version=1,
                )
            )
            session.commit()

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        with self.session_factory() as session:
            action_id = session.query(IssueAction.id).first()[0]

        self.app.state.translation_worker = TermAwareWorker()
        action = self.client.post(f"/v1/actions/{action_id}/execute?run_followup=true")
        self.assertEqual(action.status_code, 200)
        action_data = action.json()
        self.assertTrue(action_data["followup_executed"])
        self.assertTrue(action_data["rebuild_applied"])
        self.assertTrue(action_data["issue_resolved"])
        self.assertGreaterEqual(len(action_data["rerun_translation_run_ids"]), 1)
        self.assertGreaterEqual(len(action_data["rebuilt_packet_ids"]), 1)
        self.assertGreaterEqual(len(action_data["rebuilt_snapshot_ids"]), 1)
        self.assertEqual(action_data["recheck_issue_count"], 0)

        with self.session_factory() as session:
            rebuilt_packets = session.scalars(select(TranslationPacket)).all()
            self.assertTrue(
                any(
                    packet.termbase_version and packet.termbase_version > 1
                    and any(term.get("source_term") == "pricing power" for term in packet.packet_json.get("relevant_terms", []))
                    for packet in rebuilt_packets
                )
            )
            termbase_snapshots = session.scalars(
                select(MemorySnapshot).where(MemorySnapshot.snapshot_type == SnapshotType.TERMBASE)
            ).all()
            self.assertGreaterEqual(len(termbase_snapshots), 2)
        summary = self.client.get(f"/v1/documents/{document_id}")
        self.assertEqual(summary.status_code, 200)
        summary_data = summary.json()
        self.assertEqual(summary_data["open_issue_count"], 0)
        self.assertEqual(summary_data["chapters"][0]["quality_summary"]["resolved_issue_count"], 1)
        self.assertIn("termbase", [item["snapshot_type"] for item in action_data["rebuilt_snapshots"]])
        self.assertGreater(action_data["termbase_version"], 1)

    def test_execute_action_with_followup_rebuilds_chapter_brief(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        with self.session_factory() as session:
            chapter_id = session.scalars(select(TranslationPacket.chapter_id)).first()
            packet_id = session.scalars(select(TranslationPacket.id)).first()
            sentence_id = session.scalars(
                select(Sentence.id).where(Sentence.source_text.like("Pricing power%"))
            ).one()
            issue = ReviewIssue(
                id="00000000-0000-0000-0000-000000000101",
                document_id=document_id,
                chapter_id=chapter_id,
                sentence_id=sentence_id,
                packet_id=packet_id,
                issue_type="CONTEXT_FAILURE",
                root_cause_layer=RootCauseLayer.MEMORY,
                severity=Severity.MEDIUM,
                blocking=False,
                detector=Detector.HUMAN,
                confidence=1.0,
                evidence_json={"reason": "chapter_brief_stale"},
                status=IssueStatus.OPEN,
            )
            action = IssueAction(
                id="00000000-0000-0000-0000-000000000102",
                issue_id=issue.id,
                action_type=ActionType.REBUILD_CHAPTER_BRIEF,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter_id,
                status=ActionStatus.PLANNED,
                reason_json={"issue_type": "CONTEXT_FAILURE"},
                created_by=ActionActorType.SYSTEM,
            )
            session.add(issue)
            session.flush()
            session.add(action)
            session.commit()

        action = self.client.post("/v1/actions/00000000-0000-0000-0000-000000000102/execute?run_followup=true")
        self.assertEqual(action.status_code, 200)
        action_data = action.json()
        self.assertTrue(action_data["followup_executed"])
        self.assertTrue(action_data["rebuild_applied"])
        self.assertTrue(action_data["issue_resolved"])
        self.assertGreater(action_data["chapter_brief_version"], 1)
        self.assertIn("chapter_brief", [item["snapshot_type"] for item in action_data["rebuilt_snapshots"]])

        with self.session_factory() as session:
            snapshots = session.scalars(
                select(MemorySnapshot).where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                )
            ).all()
            self.assertGreaterEqual(len(snapshots), 2)
            self.assertTrue(any(snapshot.status.value == "superseded" for snapshot in snapshots))
            self.assertTrue(any(snapshot.status.value == "active" and snapshot.version > 1 for snapshot in snapshots))
            packets = session.scalars(
                select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id)
            ).all()
            self.assertTrue(all(packet.chapter_brief_version and packet.chapter_brief_version > 1 for packet in packets))

    def test_review_ignores_missing_title_context_failure_for_zero_sentence_frontmatter(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("Welcome", "welcome.xhtml", EMPTY_CHAPTER_XHTML),
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
            ]
        )
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        with self.session_factory() as session:
            frontmatter = next(
                chapter
                for chapter in bootstrap.json()["chapters"]
                if chapter["packet_count"] == 0
            )
            chapter_brief = session.scalars(
                select(MemorySnapshot).where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == frontmatter["chapter_id"],
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                )
            ).one()
            content_json = dict(chapter_brief.content_json)
            content_json["open_questions"] = ["missing_chapter_title"]
            chapter_brief.content_json = content_json
            session.merge(chapter_brief)
            session.commit()

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["total_issue_count"], 0)

        with self.session_factory() as session:
            issue = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.chapter_id == frontmatter["chapter_id"],
                    ReviewIssue.issue_type == "CONTEXT_FAILURE",
                )
            ).first()
            self.assertIsNone(issue)

        review_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(review_export.status_code, 200)

        bilingual_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(bilingual_export.status_code, 200)

    def test_low_confidence_is_reported_in_review_summary(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        self.app.state.translation_worker = LowConfidenceWorker()
        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)
        self.assertGreaterEqual(len(translate.json()["review_required_sentence_ids"]), 1)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        chapter_result = review.json()["chapter_results"][0]
        self.assertEqual(chapter_result["low_confidence_count"], 1)
        self.assertGreaterEqual(chapter_result["issue_count"], 1)
        self.assertEqual(chapter_result["blocking_issue_count"], 0)

        bilingual_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(bilingual_export.status_code, 200)

    def test_image_only_cover_chapter_does_not_block_review_or_export(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("Cover", "cover.xhtml", IMAGE_ONLY_FIGURE_XHTML),
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
            ],
            extra_files={"OEBPS/images/cover.png": b"fake-cover"},
        )
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        with self.session_factory() as session:
            cover_chapter = session.scalars(
                select(Chapter)
                .where(Chapter.document_id == document_id)
                .order_by(Chapter.ordinal)
            ).first()
            self.assertIsNotNone(cover_chapter)
            cover_sentence = session.scalars(
                select(Sentence).where(Sentence.chapter_id == cover_chapter.id)
            ).one()
            cover_sentence.translatable = True
            cover_sentence.nontranslatable_reason = None
            cover_sentence.sentence_status = SentenceStatus.PENDING
            chapter_brief = session.scalars(
                select(MemorySnapshot).where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == cover_chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                )
            ).one()
            content_json = dict(chapter_brief.content_json)
            content_json["open_questions"] = ["missing_chapter_title"]
            chapter_brief.content_json = content_json
            session.merge(cover_sentence)
            session.merge(chapter_brief)
            session.commit()

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        with self.session_factory() as session:
            cover_chapter = session.scalars(
                select(Chapter)
                .where(Chapter.document_id == document_id)
                .order_by(Chapter.ordinal)
            ).first()
            assert cover_chapter is not None
            cover_packet = session.scalars(
                select(TranslationPacket)
                .where(TranslationPacket.chapter_id == cover_chapter.id)
            ).first()
            self.assertIsNotNone(cover_packet)
            assert cover_packet is not None
            packet_json = dict(cover_packet.packet_json)
            packet_json["open_questions"] = ["missing_chapter_title"]
            cover_packet.packet_json = packet_json
            session.merge(cover_packet)
            session.commit()

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["total_issue_count"], 0)

        bilingual_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(bilingual_export.status_code, 200)

    def test_final_export_auto_followup_can_rebuild_packet_context_failure(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["total_issue_count"], 0)

        with self.session_factory() as session:
            packet = session.scalars(
                select(TranslationPacket).where(TranslationPacket.chapter_id.is_not(None))
            ).first()
            self.assertIsNotNone(packet)
            assert packet is not None
            packet_json = dict(packet.packet_json)
            packet_json["open_questions"] = ["speaker_reference_ambiguous"]
            packet.packet_json = packet_json
            session.merge(packet)
            session.commit()

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertGreaterEqual(review.json()["total_issue_count"], 1)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html", "auto_execute_followup_on_gate": True},
        )
        self.assertEqual(export.status_code, 200)
        payload = export.json()
        self.assertTrue(payload["auto_followup_requested"])
        self.assertTrue(payload["auto_followup_applied"])
        self.assertEqual(payload["auto_followup_executions"][0]["action_type"], "REBUILD_PACKET_THEN_RERUN")
        self.assertTrue(payload["auto_followup_executions"][0]["issue_resolved"])
        self.assertTrue(Path(payload["chapter_results"][0]["file_path"]).exists())

        with self.session_factory() as session:
            open_issue_count = session.scalar(
                select(func.count(ReviewIssue.id)).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.issue_type == "CONTEXT_FAILURE",
                    ReviewIssue.root_cause_layer == RootCauseLayer.PACKET,
                    ReviewIssue.status == IssueStatus.OPEN,
                )
            )
            self.assertEqual(open_issue_count, 0)

    def test_format_pollution_is_reported_in_review_summary(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        self.app.state.translation_worker = FormatPollutionWorker()
        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        chapter_result = review.json()["chapter_results"][0]
        self.assertEqual(chapter_result["format_pollution_count"], 1)
        self.assertFalse(chapter_result["format_ok"])

    def test_literal_source_tag_is_not_reported_as_format_pollution(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("Chapter One", "chapter1.xhtml", LITERAL_TAG_XHTML),
            ]
        )
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        self.app.state.translation_worker = LiteralTagWorker()
        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        chapter_result = review.json()["chapter_results"][0]
        self.assertEqual(chapter_result["format_pollution_count"], 0)
        self.assertTrue(chapter_result["format_ok"])

        with self.session_factory() as session:
            issue = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.issue_type == "FORMAT_POLLUTION",
                )
            ).first()
            self.assertIsNone(issue)

    def test_merged_html_export_renders_prose_and_code_with_different_modes(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("Chapter One", "chapter1.xhtml", CODE_CHAPTER_XHTML),
            ]
        )
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "merged_html"},
        )
        self.assertEqual(export.status_code, 200)
        export_data = export.json()
        self.assertEqual(export_data["document_status"], "exported")
        self.assertIsNotNone(export_data["file_path"])
        merged_html = Path(export_data["file_path"]).read_text(encoding="utf-8")
        self.assertIn("Reading Map", merged_html)
        self.assertIn("Back to top", merged_html)
        self.assertIn("href='#chapter-", merged_html)
        self.assertIn("ZH::Use the example carefully.", merged_html)
        self.assertIn("代码保持原样", merged_html)
        self.assertIn("def run_agent():\n    return &quot;ok&quot;\n\nprint(run_agent())", merged_html)
        self.assertEqual(export_data["chapter_results"], [])

    def test_merged_html_export_skips_empty_untitled_frontmatter_chapter(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("", "welcome.xhtml", EMPTY_CHAPTER_XHTML),
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
            ]
        )
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        bootstrap_data = bootstrap.json()
        document_id = bootstrap_data["document_id"]
        frontmatter = next(chapter for chapter in bootstrap_data["chapters"] if chapter["packet_count"] == 0)

        with self.session_factory() as session:
            frontmatter_chapter = session.get(Chapter, frontmatter["chapter_id"])
            assert frontmatter_chapter is not None
            frontmatter_chapter.title_src = None
            frontmatter_chapter.title_tgt = None
            session.merge(frontmatter_chapter)
            session.commit()

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)
        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "merged_html"},
        )
        self.assertEqual(export.status_code, 200)
        merged_html = Path(export.json()["file_path"]).read_text(encoding="utf-8")
        self.assertNotIn(frontmatter["chapter_id"], merged_html)
        self.assertIn("Chapter One", merged_html)
        self.assertIn("Chapter 1</div><h2>ZH::Chapter One</h2>", merged_html)
        self.assertNotIn("Chapter 2</div><h2>ZH::Chapter One</h2>", merged_html)

    def test_merged_html_export_deduplicates_exact_duplicate_epub_chapters(self) -> None:
        duplicate_welcome = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="welcome">Welcome</h1>
    <p>Thank you for reading.</p>
  </body>
</html>
"""
        epub_path = self._write_epub_with_chapters(
            [
                ("Welcome", "welcome-a.xhtml", duplicate_welcome),
                ("Welcome", "welcome-b.xhtml", duplicate_welcome),
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
            ]
        )
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)
        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "merged_html"},
        )
        self.assertEqual(export.status_code, 200)
        export_data = export.json()
        merged_html = Path(export_data["file_path"]).read_text(encoding="utf-8")
        manifest = json.loads(Path(export_data["manifest_path"]).read_text(encoding="utf-8"))

        self.assertEqual(merged_html.count(">ZH::Welcome</h2>"), 1)
        self.assertEqual(merged_html.count("class='source-title'>Welcome</div>"), 1)
        self.assertEqual(manifest["chapter_count"], 2)
        self.assertEqual(manifest["chapters"][0]["ordinal"], 1)
        self.assertEqual(manifest["chapters"][1]["ordinal"], 2)

    def test_merged_html_export_hides_filename_like_author_metadata(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
            ]
        )
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        with self.session_factory() as session:
            document = session.get(Document, document_id)
            assert document is not None
            document.author = "welcome.html"
            session.merge(document)
            session.commit()

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)
        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "merged_html"},
        )
        self.assertEqual(export.status_code, 200)
        export_data = export.json()
        merged_html = Path(export_data["file_path"]).read_text(encoding="utf-8")
        manifest = json.loads(Path(export_data["manifest_path"]).read_text(encoding="utf-8"))

        self.assertNotIn("welcome.html", merged_html)
        self.assertIsNone(manifest["author"])

    def test_merged_html_export_renders_structured_artifacts_with_special_modes(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("Chapter One", "chapter1.xhtml", STRUCTURED_ARTIFACT_XHTML),
            ],
            extra_files={"OEBPS/images/agent-loop.png": b"fake-png-binary"},
        )
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)
        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "merged_html"},
        )
        self.assertEqual(export.status_code, 200)
        merged_html_path = Path(export.json()["file_path"])
        merged_html = merged_html_path.read_text(encoding="utf-8")
        asset_relative_path = "assets/OEBPS/images/agent-loop.png"
        self.assertTrue((merged_html_path.parent / asset_relative_path).exists())
        self.assertIn("图片锚点保留", merged_html)
        self.assertIn("<img class='artifact-image'", merged_html)
        self.assertIn(asset_relative_path, merged_html)
        self.assertIn("Figure 1.1 Agent loop architecture", merged_html)
        self.assertIn("公式保持原样", merged_html)
        self.assertIn("x=1", merged_html)
        self.assertIn("保留原始结构，优先保证可复制与结构保真", merged_html)
        self.assertIn("Tier | Latency", merged_html)
        self.assertIn("Basic | Slow", merged_html)
        self.assertIn("参考标识保留", merged_html)
        self.assertIn("https://example.com/agent-docs", merged_html)

    def test_export_download_includes_epub_asset_sidecars(self) -> None:
        epub_path = self._write_epub_with_chapters(
            [
                ("Chapter One", "chapter1.xhtml", STRUCTURED_ARTIFACT_XHTML),
            ],
            extra_files={"OEBPS/images/agent-loop.png": b"fake-png-binary"},
        )
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)
        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        summary = self.client.get(f"/v1/documents/{document_id}")
        self.assertEqual(summary.status_code, 200)
        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "merged_html"},
        )
        self.assertEqual(export.status_code, 200)

        download = self.client.get(
            f"/v1/documents/{document_id}/exports/download",
            params={"export_type": "merged_html"},
        )
        self.assertEqual(download.status_code, 200)
        self.assertIn("application/zip", download.headers["content-type"])

        with zipfile.ZipFile(BytesIO(download.content)) as archive:
            names = archive.namelist()

        self.assertIn(f"{document_id}-analysis-bundle/merged-document.html", names)
        self.assertIn(f"{document_id}-analysis-bundle/assets/OEBPS/images/agent-loop.png", names)

    def test_execute_action_with_followup_realigns_missing_edges(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        with self.session_factory() as session:
            document_packets = session.scalars(select(TranslationPacket)).all()
            packet_id = next(
                packet.id
                for packet in document_packets
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )
            latest_run_id = session.scalars(
                select(TranslationRun.id)
                .where(TranslationRun.packet_id == packet_id)
                .order_by(TranslationRun.attempt.desc())
            ).first()
            target_segment_id = session.scalars(
                select(TargetSegment.id)
                .where(TargetSegment.translation_run_id == latest_run_id)
                .order_by(TargetSegment.ordinal)
            ).first()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.target_segment_id == target_segment_id))
            session.commit()

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertGreaterEqual(review.json()["total_issue_count"], 1)

        with self.session_factory() as session:
            action_id = session.scalars(
                select(IssueAction.id)
                .join(ReviewIssue, IssueAction.issue_id == ReviewIssue.id)
                .where(ReviewIssue.issue_type == "ALIGNMENT_FAILURE")
                .order_by(IssueAction.created_at.desc())
            ).first()

        action = self.client.post(f"/v1/actions/{action_id}/execute?run_followup=true")
        self.assertEqual(action.status_code, 200)
        action_data = action.json()
        self.assertTrue(action_data["followup_executed"])
        self.assertFalse(action_data["rebuild_applied"])
        self.assertEqual(action_data["invalidation_count"], 0)
        self.assertEqual(action_data["rerun_translation_run_ids"], [])
        self.assertEqual(action_data["rerun_packet_ids"], [packet_id])
        self.assertTrue(action_data["issue_resolved"])
        self.assertEqual(action_data["recheck_issue_count"], 0)

        with self.session_factory() as session:
            restored_edge_count = session.scalar(
                select(func.count(AlignmentEdge.id)).where(AlignmentEdge.target_segment_id == target_segment_id)
            )
            self.assertGreaterEqual(restored_edge_count or 0, 1)
            issue_status = session.scalars(
                select(ReviewIssue.status).where(ReviewIssue.issue_type == "ALIGNMENT_FAILURE")
            ).first()
            self.assertEqual(issue_status, IssueStatus.RESOLVED)

    def test_review_package_includes_quality_and_repair_evidence(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        with self.session_factory() as session:
            sentence_id = session.scalars(
                select(Sentence.id).where(Sentence.source_text.like("Pricing power%"))
            ).one()
            session.add(
                TermEntry(
                    id="00000000-0000-0000-0000-000000000012",
                    document_id=document_id,
                    scope_type=MemoryScopeType.GLOBAL,
                    scope_id=None,
                    source_term="pricing power",
                    target_term="定价权",
                    term_type=TermType.CONCEPT,
                    lock_level=LockLevel.LOCKED,
                    status=TermStatus.ACTIVE,
                    evidence_sentence_id=sentence_id,
                    version=1,
                )
            )
            session.commit()

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        with self.session_factory() as session:
            action_id = session.query(IssueAction.id).first()[0]

        self.app.state.translation_worker = TermAwareWorker()
        action = self.client.post(f"/v1/actions/{action_id}/execute?run_followup=true")
        self.assertEqual(action.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(export.status_code, 200)
        export_path = Path(export.json()["chapter_results"][0]["file_path"])
        review_package = json.loads(export_path.read_text(encoding="utf-8"))

        self.assertIn("quality_summary", review_package)
        self.assertTrue(review_package["quality_summary"]["coverage_ok"])
        self.assertIn("version_evidence", review_package)
        self.assertGreaterEqual(len(review_package["version_evidence"]["active_snapshots"]), 2)
        self.assertGreaterEqual(len(review_package["version_evidence"]["packet_context_versions"]), 1)
        self.assertIn("recent_repair_events", review_package)
        self.assertTrue(
            any(event["action"] == "snapshot.rebuilt" for event in review_package["recent_repair_events"])
        )
        self.assertTrue(
            any(event["action"] == "packet.rebuilt" for event in review_package["recent_repair_events"])
        )
        self.assertIn("export_time_misalignment_evidence", review_package)
        self.assertFalse(review_package["export_time_misalignment_evidence"]["has_anomalies"])

    def test_bilingual_export_includes_manifest_evidence(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(export.status_code, 200)
        chapter_export = export.json()["chapter_results"][0]
        self.assertIsNotNone(chapter_export["manifest_path"])
        manifest_path = Path(chapter_export["manifest_path"])
        self.assertTrue(manifest_path.exists())

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["export_type"], "bilingual_html")
        self.assertEqual(manifest["html_path"], chapter_export["file_path"])
        self.assertIn("quality_summary", manifest)
        self.assertTrue(manifest["quality_summary"]["coverage_ok"])
        self.assertIn("version_evidence", manifest)
        self.assertGreaterEqual(len(manifest["version_evidence"]["packet_context_versions"]), 1)
        self.assertIn("export_time_misalignment_evidence", manifest)
        self.assertFalse(manifest["export_time_misalignment_evidence"]["has_anomalies"])
        self.assertIn("pdf_preserve_evidence", manifest)
        self.assertIsNone(manifest["pdf_preserve_evidence"])
        self.assertIn("row_summary", manifest)
        self.assertEqual(manifest["row_summary"]["sentence_row_count"], 4)
        self.assertIn("issue_summary", manifest)
        self.assertEqual(manifest["issue_summary"]["open_issue_count"], 0)

    def test_document_exports_dashboard_lists_export_records(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.client.post(f"/v1/documents/{document_id}/review")
        review_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        bilingual_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(review_export.status_code, 200)
        self.assertEqual(bilingual_export.status_code, 200)

        dashboard = self.client.get(f"/v1/documents/{document_id}/exports")
        self.assertEqual(dashboard.status_code, 200)
        payload = dashboard.json()
        self.assertEqual(payload["document_id"], document_id)
        self.assertEqual(payload["export_count"], 2)
        self.assertEqual(payload["successful_export_count"], 2)
        self.assertEqual(payload["filtered_export_count"], 2)
        self.assertEqual(payload["record_count"], 2)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(payload["limit"], 50)
        self.assertFalse(payload["has_more"])
        self.assertIsNone(payload["applied_export_type_filter"])
        self.assertIsNone(payload["applied_status_filter"])
        self.assertEqual(payload["export_counts_by_type"]["review_package"], 1)
        self.assertEqual(payload["export_counts_by_type"]["bilingual_html"], 1)
        self.assertEqual(payload["latest_export_ids_by_type"]["review_package"], review_export.json()["chapter_results"][0]["export_id"])
        self.assertEqual(payload["latest_export_ids_by_type"]["bilingual_html"], bilingual_export.json()["chapter_results"][0]["export_id"])
        self.assertEqual(len(payload["records"]), 2)
        record_by_type = {record["export_type"]: record for record in payload["records"]}
        self.assertEqual(record_by_type["bilingual_html"]["manifest_path"], bilingual_export.json()["chapter_results"][0]["manifest_path"])
        self.assertEqual(record_by_type["review_package"]["chapter_id"], bootstrap.json()["chapters"][0]["chapter_id"])
        self.assertEqual(record_by_type["bilingual_html"]["export_auto_followup_summary"]["executed_event_count"], 0)
        self.assertEqual(payload["translation_usage_summary"]["run_count"], 3)
        self.assertEqual(payload["translation_usage_summary"]["succeeded_run_count"], 3)
        self.assertEqual(len(payload["translation_usage_breakdown"]), 1)
        self.assertEqual(payload["translation_usage_breakdown"][0]["model_name"], "echo-worker")
        self.assertEqual(payload["translation_usage_breakdown"][0]["worker_name"], "EchoTranslationWorker")
        self.assertEqual(len(payload["translation_usage_timeline"]), 1)
        self.assertEqual(payload["translation_usage_timeline"][0]["bucket_granularity"], "day")
        self.assertEqual(payload["translation_usage_timeline"][0]["run_count"], 3)
        self.assertEqual(payload["translation_usage_highlights"]["top_cost_entry"]["model_name"], "echo-worker")
        self.assertEqual(payload["translation_usage_highlights"]["top_latency_entry"]["model_name"], "echo-worker")
        self.assertEqual(payload["translation_usage_highlights"]["top_volume_entry"]["model_name"], "echo-worker")
        self.assertEqual(payload["issue_hotspots"], [])
        self.assertEqual(payload["issue_chapter_pressure"], [])
        self.assertIsNone(payload["issue_chapter_highlights"]["top_open_chapter"])
        self.assertIsNone(payload["issue_chapter_highlights"]["top_blocking_chapter"])
        self.assertIsNone(payload["issue_chapter_highlights"]["top_resolved_chapter"])
        self.assertEqual(payload["issue_chapter_breakdown"], [])
        self.assertEqual(payload["issue_chapter_heatmap"], [])
        self.assertEqual(payload["issue_chapter_queue"], [])
        self.assertEqual(payload["issue_activity_timeline"], [])
        self.assertEqual(payload["issue_activity_breakdown"], [])
        self.assertIsNone(payload["issue_activity_highlights"]["top_regressing_entry"])
        self.assertIsNone(payload["issue_activity_highlights"]["top_resolving_entry"])
        self.assertIsNone(payload["issue_activity_highlights"]["top_blocking_entry"])
        self.assertEqual(record_by_type["bilingual_html"]["translation_usage_summary"]["run_count"], 3)
        self.assertEqual(len(record_by_type["bilingual_html"]["translation_usage_breakdown"]), 1)
        self.assertEqual(
            record_by_type["bilingual_html"]["translation_usage_breakdown"][0]["model_name"],
            "echo-worker",
        )
        self.assertEqual(len(record_by_type["bilingual_html"]["translation_usage_timeline"]), 1)
        self.assertEqual(
            record_by_type["bilingual_html"]["translation_usage_timeline"][0]["run_count"],
            3,
        )
        self.assertEqual(
            record_by_type["bilingual_html"]["translation_usage_highlights"]["top_cost_entry"]["model_name"],
            "echo-worker",
        )

    def test_document_exports_dashboard_supports_filtering_and_pagination(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.client.post(f"/v1/documents/{document_id}/review")
        review_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        bilingual_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(review_export.status_code, 200)
        self.assertEqual(bilingual_export.status_code, 200)

        first_page = self.client.get(
            f"/v1/documents/{document_id}/exports",
            params={"limit": 1, "offset": 0},
        )
        self.assertEqual(first_page.status_code, 200)
        first_payload = first_page.json()
        self.assertEqual(first_payload["export_count"], 2)
        self.assertEqual(first_payload["successful_export_count"], 2)
        self.assertEqual(first_payload["filtered_export_count"], 2)
        self.assertEqual(first_payload["record_count"], 1)
        self.assertEqual(first_payload["offset"], 0)
        self.assertEqual(first_payload["limit"], 1)
        self.assertTrue(first_payload["has_more"])
        self.assertIsNone(first_payload["applied_export_type_filter"])
        self.assertIsNone(first_payload["applied_status_filter"])
        self.assertEqual(len(first_payload["records"]), 1)

        filtered_page = self.client.get(
            f"/v1/documents/{document_id}/exports",
            params={"export_type": "review_package", "status": "succeeded", "limit": 1, "offset": 0},
        )
        self.assertEqual(filtered_page.status_code, 200)
        filtered_payload = filtered_page.json()
        self.assertEqual(filtered_payload["export_count"], 2)
        self.assertEqual(filtered_payload["successful_export_count"], 2)
        self.assertEqual(filtered_payload["filtered_export_count"], 1)
        self.assertEqual(filtered_payload["record_count"], 1)
        self.assertEqual(filtered_payload["offset"], 0)
        self.assertEqual(filtered_payload["limit"], 1)
        self.assertFalse(filtered_payload["has_more"])
        self.assertEqual(filtered_payload["applied_export_type_filter"], "review_package")
        self.assertEqual(filtered_payload["applied_status_filter"], "succeeded")
        self.assertEqual(len(filtered_payload["records"]), 1)
        self.assertEqual(
            filtered_payload["records"][0]["export_id"],
            review_export.json()["chapter_results"][0]["export_id"],
        )
        self.assertEqual(filtered_payload["translation_usage_summary"]["run_count"], 3)
        self.assertEqual(filtered_payload["records"][0]["translation_usage_summary"]["run_count"], 3)
        self.assertEqual(filtered_payload["translation_usage_breakdown"][0]["model_name"], "echo-worker")
        self.assertEqual(filtered_payload["translation_usage_timeline"][0]["run_count"], 3)
        self.assertEqual(filtered_payload["translation_usage_highlights"]["top_volume_entry"]["model_name"], "echo-worker")
        self.assertEqual(filtered_payload["issue_hotspots"], [])
        self.assertEqual(filtered_payload["issue_chapter_pressure"], [])
        self.assertIsNone(filtered_payload["issue_chapter_highlights"]["top_open_chapter"])
        self.assertIsNone(filtered_payload["issue_chapter_highlights"]["top_blocking_chapter"])
        self.assertIsNone(filtered_payload["issue_chapter_highlights"]["top_resolved_chapter"])
        self.assertEqual(filtered_payload["issue_chapter_breakdown"], [])
        self.assertEqual(filtered_payload["issue_chapter_heatmap"], [])
        self.assertEqual(filtered_payload["issue_chapter_queue"], [])
        self.assertEqual(filtered_payload["issue_activity_timeline"], [])
        self.assertEqual(filtered_payload["issue_activity_breakdown"], [])
        self.assertIsNone(filtered_payload["issue_activity_highlights"]["top_regressing_entry"])
        self.assertIsNone(filtered_payload["issue_activity_highlights"]["top_resolving_entry"])
        self.assertIsNone(filtered_payload["issue_activity_highlights"]["top_blocking_entry"])

        second_page = self.client.get(
            f"/v1/documents/{document_id}/exports",
            params={"limit": 1, "offset": 1},
        )
        self.assertEqual(second_page.status_code, 200)
        second_payload = second_page.json()
        self.assertEqual(second_payload["export_count"], 2)
        self.assertEqual(second_payload["filtered_export_count"], 2)
        self.assertEqual(second_payload["record_count"], 1)
        self.assertEqual(second_payload["offset"], 1)
        self.assertEqual(second_payload["limit"], 1)
        self.assertFalse(second_payload["has_more"])
        self.assertEqual(len(second_payload["records"]), 1)
        self.assertNotEqual(second_payload["records"][0]["export_id"], first_payload["records"][0]["export_id"])

    def test_document_chapter_worklist_returns_filtered_queue(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]
        chapter_id = bootstrap.json()["chapters"][0]["chapter_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)
        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        with self.session_factory() as session:
            sentence_id = session.scalars(
                select(Sentence.id).where(
                    Sentence.document_id == document_id,
                    Sentence.source_text.like("Pricing power%"),
                )
            ).one()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id == sentence_id))
            session.commit()

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(export.status_code, 409)

        with self.session_factory() as session:
            issue = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.issue_type == "ALIGNMENT_FAILURE",
                    ReviewIssue.root_cause_layer == RootCauseLayer.EXPORT,
                )
            ).one()
            issue.created_at = datetime.now(timezone.utc) - timedelta(hours=5)
            session.commit()

        worklist = self.client.get(
            f"/v1/documents/{document_id}/chapters/worklist",
            params={
                "queue_priority": "immediate",
                "owner_ready": "true",
                "needs_immediate_attention": "true",
                "sla_status": "breached",
                "limit": 1,
                "offset": 0,
            },
        )
        self.assertEqual(worklist.status_code, 200)
        payload = worklist.json()
        self.assertEqual(payload["document_id"], document_id)
        self.assertEqual(payload["worklist_count"], 1)
        self.assertEqual(payload["filtered_worklist_count"], 1)
        self.assertEqual(payload["entry_count"], 1)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(payload["limit"], 1)
        self.assertFalse(payload["has_more"])
        self.assertEqual(payload["applied_queue_priority_filter"], "immediate")
        self.assertEqual(payload["applied_sla_status_filter"], "breached")
        self.assertTrue(payload["applied_owner_ready_filter"])
        self.assertTrue(payload["applied_needs_immediate_attention_filter"])
        self.assertEqual(payload["queue_priority_counts"]["immediate"], 1)
        self.assertEqual(payload["sla_status_counts"]["breached"], 1)
        self.assertEqual(payload["immediate_attention_count"], 1)
        self.assertEqual(payload["owner_ready_count"], 1)
        self.assertEqual(payload["assigned_count"], 0)
        self.assertEqual(payload["owner_workload_summary"], [])
        self.assertIsNone(payload["owner_workload_highlights"]["top_loaded_owner"])
        self.assertIsNone(payload["owner_workload_highlights"]["top_breached_owner"])
        self.assertIsNone(payload["owner_workload_highlights"]["top_blocking_owner"])
        self.assertIsNone(payload["owner_workload_highlights"]["top_immediate_owner"])
        self.assertIsNotNone(payload["highlights"]["top_breached_entry"])
        self.assertIsNone(payload["highlights"]["top_due_soon_entry"])
        self.assertIsNotNone(payload["highlights"]["top_oldest_entry"])
        self.assertIsNotNone(payload["highlights"]["top_immediate_entry"])
        self.assertEqual(
            payload["highlights"]["top_breached_entry"]["dominant_issue_type"],
            "ALIGNMENT_FAILURE",
        )
        self.assertEqual(len(payload["entries"]), 1)
        entry = payload["entries"][0]
        self.assertEqual(entry["queue_rank"], 1)
        self.assertEqual(entry["queue_priority"], "immediate")
        self.assertEqual(entry["sla_status"], "breached")
        self.assertFalse(entry["is_assigned"])
        self.assertIsNone(entry["assigned_owner_name"])
        self.assertTrue(entry["needs_immediate_attention"])
        self.assertTrue(entry["owner_ready"])
        self.assertEqual(entry["dominant_issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(entry["dominant_root_cause_layer"], "export")

        assign = self.client.put(
            f"/v1/documents/{document_id}/chapters/{chapter_id}/worklist/assignment",
            json={
                "owner_name": "ops-alice",
                "assigned_by": "ops-lead",
                "note": "Take export alignment fix",
            },
        )
        self.assertEqual(assign.status_code, 200)
        assignment_payload = assign.json()
        self.assertEqual(assignment_payload["owner_name"], "ops-alice")
        self.assertEqual(assignment_payload["assigned_by"], "ops-lead")
        self.assertEqual(assignment_payload["chapter_id"], chapter_id)

        detail = self.client.get(f"/v1/documents/{document_id}/chapters/{chapter_id}/worklist")
        self.assertEqual(detail.status_code, 200)
        detail_payload = detail.json()
        self.assertEqual(detail_payload["document_id"], document_id)
        self.assertEqual(detail_payload["chapter_id"], chapter_id)
        self.assertEqual(detail_payload["ordinal"], 1)
        self.assertEqual(detail_payload["title_src"], "Chapter One")
        self.assertEqual(detail_payload["packet_count"], 3)
        self.assertEqual(detail_payload["translated_packet_count"], 3)
        self.assertEqual(detail_payload["current_issue_count"], 1)
        self.assertEqual(detail_payload["current_open_issue_count"], 1)
        self.assertEqual(detail_payload["current_triaged_issue_count"], 0)
        self.assertEqual(detail_payload["current_active_blocking_issue_count"], 1)
        self.assertEqual(detail_payload["assignment"]["owner_name"], "ops-alice")
        self.assertEqual(detail_payload["assignment"]["assigned_by"], "ops-lead")
        self.assertEqual(len(detail_payload["assignment_history"]), 1)
        self.assertEqual(detail_payload["assignment_history"][0]["event_type"], "set")
        self.assertEqual(detail_payload["assignment_history"][0]["owner_name"], "ops-alice")
        self.assertEqual(detail_payload["assignment_history"][0]["performed_by"], "ops-lead")
        self.assertEqual(detail_payload["queue_entry"]["queue_priority"], "immediate")
        self.assertEqual(detail_payload["queue_entry"]["sla_status"], "breached")
        self.assertTrue(detail_payload["queue_entry"]["is_assigned"])
        self.assertEqual(detail_payload["queue_entry"]["assigned_owner_name"], "ops-alice")
        self.assertEqual(len(detail_payload["issue_family_breakdown"]), 1)
        self.assertEqual(detail_payload["issue_family_breakdown"][0]["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(detail_payload["issue_family_breakdown"][0]["root_cause_layer"], "export")
        self.assertEqual(detail_payload["recent_issues"][0]["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(detail_payload["recent_issues"][0]["status"], "open")
        self.assertEqual(detail_payload["recent_actions"][0]["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(detail_payload["recent_actions"][0]["action_type"], "REALIGN_ONLY")

        assigned_worklist = self.client.get(
            f"/v1/documents/{document_id}/chapters/worklist",
            params={"assigned": "true", "assigned_owner_name": "ops-alice"},
        )
        self.assertEqual(assigned_worklist.status_code, 200)
        assigned_payload = assigned_worklist.json()
        self.assertEqual(assigned_payload["assigned_count"], 1)
        self.assertTrue(assigned_payload["applied_assigned_filter"])
        self.assertEqual(assigned_payload["applied_assigned_owner_filter"], "ops-alice")
        self.assertEqual(len(assigned_payload["entries"]), 1)
        self.assertTrue(assigned_payload["entries"][0]["is_assigned"])
        self.assertEqual(assigned_payload["entries"][0]["assigned_owner_name"], "ops-alice")
        self.assertEqual(len(assigned_payload["owner_workload_summary"]), 1)
        owner_summary = assigned_payload["owner_workload_summary"][0]
        self.assertEqual(owner_summary["owner_name"], "ops-alice")
        self.assertEqual(owner_summary["assigned_chapter_count"], 1)
        self.assertEqual(owner_summary["immediate_count"], 1)
        self.assertEqual(owner_summary["breached_count"], 1)
        self.assertEqual(owner_summary["owner_ready_count"], 1)
        self.assertEqual(owner_summary["total_open_issue_count"], 1)
        self.assertEqual(owner_summary["total_active_blocking_issue_count"], 1)
        owner_highlights = assigned_payload["owner_workload_highlights"]
        self.assertEqual(owner_highlights["top_loaded_owner"]["owner_name"], "ops-alice")
        self.assertEqual(owner_highlights["top_breached_owner"]["owner_name"], "ops-alice")
        self.assertEqual(owner_highlights["top_blocking_owner"]["owner_name"], "ops-alice")
        self.assertEqual(owner_highlights["top_immediate_owner"]["owner_name"], "ops-alice")

        clear = self.client.post(
            f"/v1/documents/{document_id}/chapters/{chapter_id}/worklist/assignment/clear",
            json={"cleared_by": "ops-lead", "note": "Requeue for pool"},
        )
        self.assertEqual(clear.status_code, 200)
        clear_payload = clear.json()
        self.assertTrue(clear_payload["cleared"])
        self.assertEqual(clear_payload["cleared_by"], "ops-lead")

        detail_after_clear = self.client.get(f"/v1/documents/{document_id}/chapters/{chapter_id}/worklist")
        self.assertEqual(detail_after_clear.status_code, 200)
        detail_after_clear_payload = detail_after_clear.json()
        self.assertIsNone(detail_after_clear_payload["assignment"])
        self.assertFalse(detail_after_clear_payload["queue_entry"]["is_assigned"])
        self.assertEqual(len(detail_after_clear_payload["assignment_history"]), 2)
        self.assertEqual(detail_after_clear_payload["assignment_history"][0]["event_type"], "cleared")
        self.assertEqual(detail_after_clear_payload["assignment_history"][0]["performed_by"], "ops-lead")
        self.assertEqual(detail_after_clear_payload["assignment_history"][1]["event_type"], "set")
        worklist_after_clear = self.client.get(f"/v1/documents/{document_id}/chapters/worklist")
        self.assertEqual(worklist_after_clear.status_code, 200)
        worklist_after_clear_payload = worklist_after_clear.json()
        self.assertEqual(worklist_after_clear_payload["owner_workload_summary"], [])
        self.assertIsNone(worklist_after_clear_payload["owner_workload_highlights"]["top_loaded_owner"])

    def test_document_export_detail_returns_persisted_usage_and_evidence(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.client.post(f"/v1/documents/{document_id}/review")
        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(export.status_code, 200)
        export_id = export.json()["chapter_results"][0]["export_id"]

        detail = self.client.get(f"/v1/documents/{document_id}/exports/{export_id}")
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(payload["document_id"], document_id)
        self.assertEqual(payload["export_id"], export_id)
        self.assertEqual(payload["export_type"], "bilingual_html")
        self.assertEqual(payload["sentence_count"], 4)
        self.assertEqual(payload["target_segment_count"], 4)
        self.assertEqual(payload["translation_usage_summary"]["run_count"], 3)
        self.assertEqual(payload["translation_usage_summary"]["succeeded_run_count"], 3)
        self.assertEqual(payload["translation_usage_summary"]["total_cost_usd"], 0.0)
        self.assertEqual(len(payload["translation_usage_breakdown"]), 1)
        self.assertEqual(payload["translation_usage_breakdown"][0]["model_name"], "echo-worker")
        self.assertEqual(payload["translation_usage_breakdown"][0]["worker_name"], "EchoTranslationWorker")
        self.assertEqual(len(payload["translation_usage_timeline"]), 1)
        self.assertEqual(payload["translation_usage_timeline"][0]["run_count"], 3)
        self.assertEqual(payload["translation_usage_highlights"]["top_cost_entry"]["model_name"], "echo-worker")
        self.assertEqual(
            payload["translation_usage_highlights"]["top_latency_entry"]["model_name"],
            "echo-worker",
        )
        self.assertEqual(
            payload["translation_usage_highlights"]["top_volume_entry"]["model_name"],
            "echo-worker",
        )
        self.assertEqual(payload["issue_status_summary"]["issue_count"], 0)
        self.assertEqual(payload["issue_status_summary"]["open_issue_count"], 0)
        self.assertEqual(payload["export_time_misalignment_counts"]["missing_target_sentence_count"], 0)
        self.assertEqual(payload["version_evidence_summary"]["active_snapshot_versions"]["chapter_brief"], 1)
        self.assertEqual(payload["manifest_path"], export.json()["chapter_results"][0]["manifest_path"])

    def test_final_export_blocks_post_review_misalignment_but_review_package_keeps_evidence(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["total_issue_count"], 0)

        with self.session_factory() as session:
            sentence_id = session.scalars(
                select(Sentence.id).where(Sentence.document_id == document_id, Sentence.source_text.like("Pricing power%"))
            ).one()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id == sentence_id))
            session.commit()

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(export.status_code, 409)
        detail = export.json()["detail"]
        self.assertIn("export-time misalignment anomalies", detail["message"])
        self.assertEqual(detail["chapter_id"] is not None, True)
        self.assertGreaterEqual(len(detail["issue_ids"]), 1)
        self.assertGreaterEqual(len(detail["action_ids"]), 1)
        self.assertGreaterEqual(len(detail["followup_actions"]), 1)
        self.assertTrue(detail["followup_actions"][0]["suggested_run_followup"])

        with self.session_factory() as session:
            export_issue = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.root_cause_layer == RootCauseLayer.EXPORT,
                    ReviewIssue.issue_type == "ALIGNMENT_FAILURE",
                )
            ).first()
            self.assertIsNotNone(export_issue)
            assert export_issue is not None
            self.assertEqual(export_issue.packet_id is not None, True)
            self.assertIn(sentence_id, export_issue.evidence_json["missing_target_sentence_ids"])

            export_action = session.scalars(
                select(IssueAction).where(IssueAction.issue_id == export_issue.id)
            ).first()
            self.assertIsNotNone(export_action)
            assert export_action is not None
            self.assertEqual(export_action.action_type, ActionType.REALIGN_ONLY)
            self.assertIn(export_issue.id, detail["issue_ids"])
            self.assertIn(export_action.id, detail["action_ids"])

        review_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(review_export.status_code, 200)
        review_package_path = Path(review_export.json()["chapter_results"][0]["file_path"])
        review_package = json.loads(review_package_path.read_text(encoding="utf-8"))

        self.assertTrue(review_package["export_time_misalignment_evidence"]["has_anomalies"])
        self.assertIn(sentence_id, review_package["export_time_misalignment_evidence"]["missing_target_sentence_ids"])

        dashboard = self.client.get(f"/v1/documents/{document_id}/exports")
        self.assertEqual(dashboard.status_code, 200)
        dashboard_payload = dashboard.json()
        alignment_hotspot = next(
            hotspot
            for hotspot in dashboard_payload["issue_hotspots"]
            if hotspot["issue_type"] == "ALIGNMENT_FAILURE" and hotspot["root_cause_layer"] == "export"
        )
        self.assertEqual(alignment_hotspot["issue_count"], 1)
        self.assertEqual(alignment_hotspot["open_issue_count"], 1)
        self.assertEqual(alignment_hotspot["resolved_issue_count"], 0)
        self.assertEqual(alignment_hotspot["blocking_issue_count"], 1)
        self.assertEqual(alignment_hotspot["chapter_count"], 1)
        self.assertIsNotNone(alignment_hotspot["latest_seen_at"])
        chapter_pressure = dashboard_payload["issue_chapter_pressure"]
        self.assertEqual(len(chapter_pressure), 1)
        self.assertEqual(chapter_pressure[0]["chapter_id"], detail["chapter_id"])
        self.assertEqual(chapter_pressure[0]["ordinal"], 1)
        self.assertEqual(chapter_pressure[0]["title_src"], "Chapter One")
        self.assertTrue(chapter_pressure[0]["chapter_status"])
        self.assertEqual(chapter_pressure[0]["issue_count"], 1)
        self.assertEqual(chapter_pressure[0]["open_issue_count"], 1)
        self.assertEqual(chapter_pressure[0]["resolved_issue_count"], 0)
        self.assertEqual(chapter_pressure[0]["blocking_issue_count"], 1)
        self.assertIsNotNone(chapter_pressure[0]["latest_issue_at"])
        chapter_highlights = dashboard_payload["issue_chapter_highlights"]
        self.assertEqual(chapter_highlights["top_open_chapter"]["chapter_id"], detail["chapter_id"])
        self.assertEqual(chapter_highlights["top_blocking_chapter"]["chapter_id"], detail["chapter_id"])
        self.assertIsNone(chapter_highlights["top_resolved_chapter"])
        self.assertEqual(len(dashboard_payload["issue_chapter_breakdown"]), 1)
        chapter_breakdown = dashboard_payload["issue_chapter_breakdown"][0]
        self.assertEqual(chapter_breakdown["chapter_id"], detail["chapter_id"])
        self.assertEqual(chapter_breakdown["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(chapter_breakdown["root_cause_layer"], "export")
        self.assertEqual(chapter_breakdown["issue_count"], 1)
        self.assertEqual(chapter_breakdown["open_issue_count"], 1)
        self.assertEqual(chapter_breakdown["active_blocking_issue_count"], 1)
        self.assertEqual(len(dashboard_payload["issue_chapter_heatmap"]), 1)
        chapter_heatmap = dashboard_payload["issue_chapter_heatmap"][0]
        self.assertEqual(chapter_heatmap["chapter_id"], detail["chapter_id"])
        self.assertEqual(chapter_heatmap["issue_family_count"], 1)
        self.assertEqual(chapter_heatmap["dominant_issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(chapter_heatmap["dominant_root_cause_layer"], "export")
        self.assertEqual(chapter_heatmap["active_blocking_issue_count"], 1)
        self.assertEqual(chapter_heatmap["heat_score"], 7)
        self.assertEqual(chapter_heatmap["heat_level"], "high")
        self.assertEqual(len(dashboard_payload["issue_chapter_queue"]), 1)
        chapter_queue = dashboard_payload["issue_chapter_queue"][0]
        self.assertEqual(chapter_queue["chapter_id"], detail["chapter_id"])
        self.assertEqual(chapter_queue["queue_rank"], 1)
        self.assertEqual(chapter_queue["queue_priority"], "immediate")
        self.assertEqual(chapter_queue["queue_driver"], "active_blocking")
        self.assertTrue(chapter_queue["needs_immediate_attention"])
        self.assertIsNotNone(chapter_queue["oldest_active_issue_at"])
        self.assertGreaterEqual(chapter_queue["age_hours"], 0)
        self.assertEqual(chapter_queue["age_bucket"], "fresh")
        self.assertEqual(chapter_queue["sla_target_hours"], 4)
        self.assertEqual(chapter_queue["sla_status"], "on_track")
        self.assertTrue(chapter_queue["owner_ready"])
        self.assertEqual(chapter_queue["owner_ready_reason"], "clear_dominant_issue_family")
        self.assertIsNotNone(chapter_queue["latest_activity_bucket_start"])
        self.assertEqual(chapter_queue["latest_created_issue_count"], 1)
        self.assertEqual(chapter_queue["latest_resolved_issue_count"], 0)
        self.assertEqual(chapter_queue["latest_net_issue_delta"], 1)
        self.assertEqual(chapter_queue["regression_hint"], "regressing")
        self.assertFalse(chapter_queue["flapping_hint"])
        self.assertEqual(chapter_queue["dominant_issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(chapter_queue["dominant_root_cause_layer"], "export")
        self.assertEqual(len(dashboard_payload["issue_activity_timeline"]), 1)
        issue_timeline_entry = dashboard_payload["issue_activity_timeline"][0]
        self.assertEqual(issue_timeline_entry["bucket_granularity"], "day")
        self.assertEqual(issue_timeline_entry["created_issue_count"], 1)
        self.assertEqual(issue_timeline_entry["resolved_issue_count"], 0)
        self.assertEqual(issue_timeline_entry["wontfix_issue_count"], 0)
        self.assertEqual(issue_timeline_entry["blocking_created_issue_count"], 1)
        self.assertEqual(issue_timeline_entry["net_issue_delta"], 1)
        self.assertEqual(issue_timeline_entry["estimated_open_issue_count"], 1)
        self.assertEqual(len(dashboard_payload["issue_activity_breakdown"]), 1)
        breakdown_entry = dashboard_payload["issue_activity_breakdown"][0]
        self.assertEqual(breakdown_entry["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(breakdown_entry["root_cause_layer"], "export")
        self.assertEqual(breakdown_entry["issue_count"], 1)
        self.assertEqual(breakdown_entry["open_issue_count"], 1)
        self.assertEqual(breakdown_entry["blocking_issue_count"], 1)
        self.assertIsNotNone(breakdown_entry["latest_seen_at"])
        self.assertEqual(len(breakdown_entry["timeline"]), 1)
        self.assertEqual(breakdown_entry["timeline"][0]["created_issue_count"], 1)
        self.assertEqual(breakdown_entry["timeline"][0]["resolved_issue_count"], 0)
        self.assertEqual(breakdown_entry["timeline"][0]["net_issue_delta"], 1)
        self.assertEqual(breakdown_entry["timeline"][0]["estimated_open_issue_count"], 1)
        highlights = dashboard_payload["issue_activity_highlights"]
        self.assertEqual(highlights["top_regressing_entry"]["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(highlights["top_regressing_entry"]["root_cause_layer"], "export")
        self.assertIsNone(highlights["top_resolving_entry"])
        self.assertEqual(highlights["top_blocking_entry"]["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(highlights["top_blocking_entry"]["root_cause_layer"], "export")

    def test_final_export_can_auto_followup_and_succeed(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["total_issue_count"], 0)

        with self.session_factory() as session:
            sentence_id = session.scalars(
                select(Sentence.id).where(Sentence.document_id == document_id, Sentence.source_text.like("Pricing power%"))
            ).one()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id == sentence_id))
            session.commit()

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html", "auto_execute_followup_on_gate": True},
        )
        self.assertEqual(export.status_code, 200)
        export_data = export.json()
        self.assertTrue(export_data["auto_followup_requested"])
        self.assertTrue(export_data["auto_followup_applied"])
        self.assertEqual(export_data["auto_followup_attempt_count"], 1)
        self.assertEqual(export_data["auto_followup_attempt_limit"], 3)
        self.assertGreaterEqual(len(export_data["auto_followup_executions"]), 1)
        self.assertTrue(export_data["auto_followup_executions"][0]["followup_executed"])
        self.assertEqual(export_data["auto_followup_executions"][0]["action_type"], "REALIGN_ONLY")
        self.assertTrue(export_data["auto_followup_executions"][0]["issue_resolved"])
        self.assertTrue(Path(export_data["chapter_results"][0]["file_path"]).exists())
        manifest_path = Path(export_data["chapter_results"][0]["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["export_auto_followup_evidence"]["executed_event_count"], 1)
        self.assertEqual(manifest["export_auto_followup_evidence"]["stop_event_count"], 0)
        self.assertEqual(
            manifest["export_auto_followup_evidence"]["events"][0]["action"],
            "export.auto_followup.executed",
        )

        with self.session_factory() as session:
            restored_edge_count = session.scalar(
                select(func.count(AlignmentEdge.id)).where(AlignmentEdge.sentence_id == sentence_id)
            )
            self.assertGreaterEqual(restored_edge_count or 0, 1)
            export_issue_status = session.scalars(
                select(ReviewIssue.status).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.root_cause_layer == RootCauseLayer.EXPORT,
                    ReviewIssue.issue_type == "ALIGNMENT_FAILURE",
                )
            ).first()
            self.assertEqual(export_issue_status, IssueStatus.RESOLVED)
            export_record = session.get(Export, export_data["chapter_results"][0]["export_id"])
            self.assertIsNotNone(export_record)
            assert export_record is not None
            auto_followup_summary = export_record.input_version_bundle_json["export_auto_followup_summary"]
            self.assertEqual(auto_followup_summary["executed_event_count"], 1)
            self.assertEqual(auto_followup_summary["stop_event_count"], 0)
            self.assertIsNotNone(auto_followup_summary["latest_event_at"])
            self.assertIsNone(auto_followup_summary["last_stop_reason"])

        dashboard = self.client.get(f"/v1/documents/{document_id}/exports")
        self.assertEqual(dashboard.status_code, 200)
        payload = dashboard.json()
        self.assertEqual(payload["total_auto_followup_executed_count"], 1)
        bilingual_record = next(record for record in payload["records"] if record["export_type"] == "bilingual_html")
        self.assertEqual(bilingual_record["export_auto_followup_summary"]["executed_event_count"], 1)
        self.assertEqual(bilingual_record["export_auto_followup_summary"]["stop_event_count"], 0)
        self.assertEqual(len(payload["issue_activity_timeline"]), 1)
        issue_timeline_entry = payload["issue_activity_timeline"][0]
        self.assertEqual(issue_timeline_entry["created_issue_count"], 1)
        self.assertEqual(issue_timeline_entry["resolved_issue_count"], 1)
        self.assertEqual(issue_timeline_entry["wontfix_issue_count"], 0)
        self.assertEqual(issue_timeline_entry["blocking_created_issue_count"], 1)
        self.assertEqual(issue_timeline_entry["net_issue_delta"], 0)
        self.assertEqual(issue_timeline_entry["estimated_open_issue_count"], 0)
        self.assertEqual(len(payload["issue_activity_breakdown"]), 1)
        breakdown_entry = payload["issue_activity_breakdown"][0]
        self.assertEqual(breakdown_entry["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(breakdown_entry["root_cause_layer"], "export")
        self.assertEqual(breakdown_entry["issue_count"], 1)
        self.assertEqual(breakdown_entry["open_issue_count"], 0)
        self.assertEqual(breakdown_entry["blocking_issue_count"], 1)
        self.assertEqual(len(breakdown_entry["timeline"]), 1)
        self.assertEqual(breakdown_entry["timeline"][0]["created_issue_count"], 1)
        self.assertEqual(breakdown_entry["timeline"][0]["resolved_issue_count"], 1)
        self.assertEqual(breakdown_entry["timeline"][0]["net_issue_delta"], 0)
        self.assertEqual(breakdown_entry["timeline"][0]["estimated_open_issue_count"], 0)
        chapter_highlights = payload["issue_chapter_highlights"]
        self.assertIsNone(chapter_highlights["top_open_chapter"])
        self.assertEqual(chapter_highlights["top_blocking_chapter"]["chapter_id"], export_data["chapter_results"][0]["chapter_id"])
        self.assertEqual(chapter_highlights["top_resolved_chapter"]["chapter_id"], export_data["chapter_results"][0]["chapter_id"])
        self.assertEqual(len(payload["issue_chapter_breakdown"]), 1)
        chapter_breakdown = payload["issue_chapter_breakdown"][0]
        self.assertEqual(chapter_breakdown["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(chapter_breakdown["root_cause_layer"], "export")
        self.assertEqual(chapter_breakdown["open_issue_count"], 0)
        self.assertEqual(chapter_breakdown["resolved_issue_count"], 1)
        self.assertEqual(chapter_breakdown["blocking_issue_count"], 1)
        self.assertEqual(chapter_breakdown["active_blocking_issue_count"], 0)
        self.assertEqual(len(payload["issue_chapter_heatmap"]), 1)
        chapter_heatmap = payload["issue_chapter_heatmap"][0]
        self.assertEqual(chapter_heatmap["chapter_id"], export_data["chapter_results"][0]["chapter_id"])
        self.assertEqual(chapter_heatmap["issue_family_count"], 1)
        self.assertEqual(chapter_heatmap["dominant_issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(chapter_heatmap["dominant_root_cause_layer"], "export")
        self.assertEqual(chapter_heatmap["blocking_issue_count"], 1)
        self.assertEqual(chapter_heatmap["active_blocking_issue_count"], 0)
        self.assertEqual(chapter_heatmap["heat_score"], 0)
        self.assertEqual(chapter_heatmap["heat_level"], "none")
        self.assertEqual(payload["issue_chapter_queue"], [])
        highlights = payload["issue_activity_highlights"]
        self.assertIsNone(highlights["top_regressing_entry"])
        self.assertEqual(highlights["top_resolving_entry"]["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(highlights["top_resolving_entry"]["root_cause_layer"], "export")
        self.assertEqual(highlights["top_blocking_entry"]["issue_type"], "ALIGNMENT_FAILURE")
        self.assertEqual(highlights["top_blocking_entry"]["root_cause_layer"], "export")

    def test_final_export_auto_followup_respects_attempt_limit(self) -> None:
        epub_path = self._write_epub()
        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["total_issue_count"], 0)

        with self.session_factory() as session:
            sentence_ids = session.scalars(
                select(Sentence.id).where(
                    Sentence.document_id == document_id,
                    Sentence.source_text.in_(["Chapter One", "Pricing power matters."]),
                )
            ).all()
            self.assertEqual(len(sentence_ids), 2)
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id.in_(sentence_ids)))
            session.commit()

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={
                "export_type": "bilingual_html",
                "auto_execute_followup_on_gate": True,
                "max_auto_followup_attempts": 1,
            },
        )
        self.assertEqual(export.status_code, 409)
        detail = export.json()["detail"]
        self.assertTrue(detail["auto_followup_requested"])
        self.assertEqual(detail["auto_followup_attempt_count"], 1)
        self.assertEqual(detail["auto_followup_attempt_limit"], 1)
        self.assertEqual(detail["auto_followup_stop_reason"], "max_attempts_reached")
        self.assertEqual(len(detail["auto_followup_executions"]), 1)
        self.assertEqual(detail["auto_followup_executions"][0]["action_type"], "REALIGN_ONLY")
        self.assertGreaterEqual(len(detail["followup_actions"]), 1)

        review_export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(review_export.status_code, 200)
        review_package_path = Path(review_export.json()["chapter_results"][0]["file_path"])
        review_package = json.loads(review_package_path.read_text(encoding="utf-8"))
        self.assertEqual(review_package["export_auto_followup_evidence"]["executed_event_count"], 1)
        self.assertEqual(review_package["export_auto_followup_evidence"]["stop_event_count"], 1)
        self.assertTrue(
            any(
                event["action"] == "export.auto_followup.stopped"
                and event["payload"]["stop_reason"] == "max_attempts_reached"
                for event in review_package["export_auto_followup_evidence"]["events"]
            )
        )

        with self.session_factory() as session:
            review_export_record = session.get(Export, review_export.json()["chapter_results"][0]["export_id"])
            self.assertIsNotNone(review_export_record)
            assert review_export_record is not None
            auto_followup_summary = review_export_record.input_version_bundle_json["export_auto_followup_summary"]
            self.assertEqual(auto_followup_summary["executed_event_count"], 1)
            self.assertEqual(auto_followup_summary["stop_event_count"], 1)
            self.assertEqual(auto_followup_summary["last_stop_reason"], "max_attempts_reached")


if __name__ == "__main__":
    unittest.main()
