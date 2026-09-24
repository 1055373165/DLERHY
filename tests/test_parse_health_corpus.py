"""The real-book corpus: every book in tests/corpus/manifest.json parses the way it is recorded there.

Each book is imported with the app's own bootstrap into a throwaway SQLite database (which also runs
the parse health checks), then compared with its manifest entry: the number of sections, their
titles, and an upper bound for every parse health check. A parser change that brings back an old
problem (contents lines as chapters, "¡" bullets, broken words, figure crops in the header...)
raises a count over its bound and fails here.

Opt-in like the PostgreSQL tests, because the books are large and live outside the repository:

    BOOK_AGENT_RUN_CORPUS_TESTS=1 uv run python -m pytest tests/test_parse_health_corpus.py -q -p no:cacheprovider

A book whose file is missing is skipped, not failed. Parsing the RAG book takes about two minutes.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

MANIFEST = Path(__file__).resolve().parent / "corpus" / "manifest.json"
RUN_CORPUS = os.getenv("BOOK_AGENT_RUN_CORPUS_TESTS") == "1"


def load_manifest(path: Path = MANIFEST) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8"))["books"])


def _title(value: str) -> str:
    return " ".join((value or "").split())


def corpus_failures(book: dict[str, Any], *, source_type: str, chapter_titles: list[str], checks: list[dict[str, Any]]) -> list[str]:
    """How a parsed book differs from its manifest entry; empty when it matches."""
    failures: list[str] = []
    if source_type != book["source_type"]:
        failures.append(f"source type {source_type}, expected {book['source_type']}")
    titles = [_title(title) for title in chapter_titles]
    if len(titles) != book["section_count"]:
        failures.append(f"{len(titles)} sections, expected {book['section_count']}")
    expected_titles = [_title(title) for title in book.get("chapter_titles", [])]
    if expected_titles and titles != expected_titles:
        missing = [title for title in expected_titles if title not in titles]
        extra = [title for title in titles if title not in expected_titles]
        failures.append(f"section titles differ: missing {missing[:5]}, unexpected {extra[:5]}")
    for title in book.get("must_exist_titles", []):
        if _title(title) not in titles:
            failures.append(f"missing section {title!r}")
    counts = {check["name"]: int(check["count"]) for check in checks}
    for name, bound in book["max_counts"].items():
        if name not in counts:
            failures.append(f"check {name} did not run")
        elif counts[name] > bound:
            samples = next((check.get("samples") or [] for check in checks if check["name"] == name), [])
            where = "; ".join(f"p{sample.get('page')}: {sample.get('text', '')[:60]}" for sample in samples[:3])
            failures.append(f"{name}: {counts[name]} > {bound} ({where})")
    for name in counts.keys() - book["max_counts"].keys():
        failures.append(f"check {name} has no bound in the manifest")
    return failures


def parse_book(path: Path, work: Path) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Import ``path`` into a fresh SQLite database under ``work``; return its source type, section titles and checks."""
    from sqlalchemy import select

    from book_agent.domain.models import Chapter, Document
    from book_agent.infra.db.base import Base
    from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
    from book_agent.services.parse_health import latest_report
    from book_agent.services.workflows import DocumentWorkflowService

    engine = build_engine(f"sqlite+pysqlite:///{work / 'corpus.db'}")
    try:
        Base.metadata.create_all(engine)
        session_factory = build_session_factory(engine=engine)
        with session_scope(session_factory) as session:
            document_id = DocumentWorkflowService(session, export_root=str(work / "exports")).bootstrap_document(path).document_id
        with session_scope(session_factory) as session:
            document = session.get(Document, document_id)
            titles = list(
                session.scalars(select(Chapter.title_src).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)).all()
            )
            report = latest_report(session, document_id)
            if report is None:
                raise AssertionError("the import wrote no parse health report")
            return document.source_type.value, [title or "" for title in titles], list(report.checks_json or [])
    finally:
        engine.dispose()


class CorpusManifestTest(unittest.TestCase):
    """The manifest itself: runs without the books."""

    def test_every_book_bounds_every_check(self) -> None:
        from book_agent.domain.structure.parse_health import THRESHOLDS

        books = load_manifest()
        self.assertGreaterEqual(len(books), 2)
        for book in books:
            with self.subTest(book=book["id"]):
                self.assertEqual(set(book["max_counts"]), set(THRESHOLDS))
                self.assertEqual(len(book["chapter_titles"]), book["section_count"])
                self.assertTrue(Path(book["path"]).suffix.lower() == ".pdf")
                # A bound above zero is a known residual and says what it is.
                for name, bound in book["max_counts"].items():
                    if bound:
                        self.assertIn(name, book.get("known_residuals", {}), f"{book['id']}: {name} needs a reason")

    def test_the_comparison_names_what_regressed(self) -> None:
        book = {
            "source_type": "pdf_text",
            "section_count": 2,
            "chapter_titles": ["preface", "1 Basics"],
            "max_counts": {"stray_glyphs": 0, "figure_regions": 1},
        }
        checks = [
            {"name": "stray_glyphs", "count": 3, "samples": [{"page": 15, "text": "¡ Learn how"}]},
            {"name": "figure_regions", "count": 1, "samples": []},
        ]
        clean = [{"name": "stray_glyphs", "count": 0, "samples": []}, checks[1]]
        self.assertEqual(corpus_failures(book, source_type="pdf_text", chapter_titles=["preface", "1  Basics"], checks=clean), [])
        self.assertEqual(
            corpus_failures(book, source_type="pdf_text", chapter_titles=["preface", "1 Basics"], checks=checks[1:]),
            ["check stray_glyphs did not run"],
        )
        failures = corpus_failures(book, source_type="pdf_text", chapter_titles=["preface", "References", "2 Next 17"], checks=checks)
        self.assertEqual(len(failures), 3, failures)
        self.assertIn("3 sections, expected 2", failures[0])
        self.assertIn("unexpected ['References', '2 Next 17']", failures[1])
        self.assertIn("stray_glyphs: 3 > 0 (p15: ¡ Learn how)", failures[2])


def _book_test(book: dict[str, Any]):
    @unittest.skipUnless(RUN_CORPUS, "Set BOOK_AGENT_RUN_CORPUS_TESTS=1 to parse the real-book corpus.")
    def test(self: unittest.TestCase) -> None:
        path = Path(book["path"])
        if not path.exists():
            self.skipTest(f"corpus book not on this machine: {path}")
        with tempfile.TemporaryDirectory() as tmpdir:
            source_type, titles, checks = parse_book(path, Path(tmpdir))
        failures = corpus_failures(book, source_type=source_type, chapter_titles=titles, checks=checks)
        self.assertEqual(failures, [], f"{book['id']} no longer parses as recorded")

    test.__doc__ = f"{book['id']} parses as recorded in the corpus manifest."
    return test


class CorpusBooksTest(unittest.TestCase):
    pass


for _book in load_manifest():
    setattr(CorpusBooksTest, f"test_{_book['id'].replace('-', '_')}", _book_test(_book))


if __name__ == "__main__":
    unittest.main()
