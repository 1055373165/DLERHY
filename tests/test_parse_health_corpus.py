"""The real-book corpus: every book in tests/corpus/manifest.json parses the way it is recorded there.

Each book is imported with the app's own bootstrap into a throwaway SQLite database (which also runs
the parse health checks), then compared with its manifest entry: the number of sections, their
titles, and an upper bound for every parse health check. A parser change that brings back an old
problem (contents lines as chapters, "¡" bullets, broken words, figure crops in the header...)
raises a count over its bound and fails here. It also fails when a body paragraph of the book lies
inside a figure region (``prose_inside_figures``): a crop that swallows prose loses it for the reader.

Opt-in like the PostgreSQL tests, because the books are large and live outside the repository:

    BOOK_AGENT_RUN_CORPUS_TESTS=1 uv run python -m pytest tests/test_parse_health_corpus.py -q -p no:cacheprovider

A book whose file is missing is skipped, not failed. Parsing the RAG book takes about two minutes.
"""

from __future__ import annotations

import json
import os
import re
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


Region = tuple[int, tuple[float, float, float, float]]

_FULL_SENTENCE_RE = re.compile(r"[^.!?]*?[A-Za-z][^.!?]*[.!?][\"”’)]?(?=\s+[A-Z“\"(]|\s*$)")


def _full_sentences(text: str) -> int:
    return sum(1 for sentence in _FULL_SENTENCE_RE.findall(" ".join(text.split())) if len(sentence.split()) >= 4)


def prose_inside_figures(pdf_path: Path, figures: list[Region]) -> list[tuple[int, str]]:
    """Body paragraphs of the PDF that lie inside a figure region: prose the reader would lose.

    Judged from the PDF, not the parse: a text block set in the book's body font size (the size
    of its full-width, multi-line paragraphs) holding at least two full sentences, with at
    least 60% of its area inside a figure. A picture that is its page or the page's background
    (``parse_health.is_page_background``) is not a figure.
    """
    import fitz

    from book_agent.domain.structure.parse_health import is_page_background

    by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    with fitz.open(pdf_path) as document:
        for page_number, bbox in figures:
            if 1 <= page_number <= document.page_count:
                page = document[page_number - 1]
                if not is_page_background(bbox, (page.rect.width, page.rect.height)):
                    by_page.setdefault(page_number, []).append(bbox)
        pages: dict[int, list[tuple[Any, float, str]]] = {}
        wide_sizes: dict[float, int] = {}
        for page_number in by_page:
            page = document[page_number - 1]
            for block in page.get_text("dict")["blocks"]:
                lines = [line for line in block.get("lines", []) if "".join(span["text"] for span in line["spans"]).strip()]
                if block.get("type") != 0 or not lines:
                    continue
                sizes: dict[float, int] = {}
                for line in lines:
                    for span in line["spans"]:
                        sizes[round(span["size"], 1)] = sizes.get(round(span["size"], 1), 0) + len(span["text"].strip())
                size = max(sizes, key=sizes.__getitem__)
                text = " ".join("".join(span["text"] for span in line["spans"]) for line in lines)
                if len(lines) > 1 and max(line["bbox"][2] - line["bbox"][0] for line in lines) >= page.rect.width * 0.6:
                    wide_sizes[size] = wide_sizes.get(size, 0) + len(lines)
                pages.setdefault(page_number, []).append((fitz.Rect(block["bbox"]), size, text))
    body_size = max(wide_sizes, key=wide_sizes.__getitem__) if wide_sizes else None
    found: list[tuple[int, str]] = []
    for page_number, blocks in sorted(pages.items()):
        regions = [fitz.Rect(bbox) for bbox in by_page[page_number]]
        for rect, size, text in blocks:
            if body_size is None or abs(size - body_size) > 0.3 or _full_sentences(text) < 2:
                continue
            if any((rect & region).get_area() >= 0.6 * max(rect.get_area(), 1e-6) for region in regions):
                found.append((page_number, " ".join(text.split())[:80]))
    return found


def parse_book(path: Path, work: Path) -> tuple[str, list[str], list[dict[str, Any]], list[Region]]:
    """Import ``path`` into a fresh SQLite database under ``work``.

    Returns its source type, section titles, parse health checks and figure regions.
    """
    from sqlalchemy import select

    from book_agent.domain.models import Block, Chapter, Document
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
            chapter_ids = list(session.scalars(select(Chapter.id).where(Chapter.document_id == document_id)).all())
            figures: list[Region] = []
            for block in session.scalars(select(Block).where(Block.chapter_id.in_(chapter_ids))).all():
                if getattr(block.status, "value", block.status) != "active":
                    continue
                if getattr(block.block_type, "value", block.block_type) not in {"image", "figure"}:
                    continue
                for region in ((block.source_span_json or {}).get("source_bbox_json") or {}).get("regions") or []:
                    if isinstance(region, dict) and isinstance(region.get("page_number"), int) and len(region.get("bbox") or []) == 4:
                        figures.append((region["page_number"], tuple(float(value) for value in region["bbox"])))
            return document.source_type.value, [title or "" for title in titles], list(report.checks_json or []), figures
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


class ProseInsideFiguresTest(unittest.TestCase):
    """The prose guard itself, on a synthetic page: runs without the books."""

    def test_body_paragraph_in_a_figure_region_is_reported_and_labels_are_not(self) -> None:
        import fitz

        body = (
            "Vector search quantifies the meaning of text and calculates similarity. First, it uses an "
            "embedding model to transform text into a point in a high-dimensional vector space."
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "page.pdf"
            document = fitz.open()
            page = document.new_page(width=531, height=666)
            page.insert_textbox(fitz.Rect(102, 60, 477, 160), body + " " + body, fontsize=10)
            page.insert_textbox(fitz.Rect(102, 300, 300, 340), "Retriever searches chunks. It finds the most similar ones.", fontsize=7)
            document.save(pdf_path)
            document.close()
            self.assertEqual(prose_inside_figures(pdf_path, [(1, (99.0, 290.0, 477.0, 350.0))]), [])
            swallowed = prose_inside_figures(pdf_path, [(1, (99.0, 50.0, 477.0, 350.0))])
            self.assertEqual([page for page, _text in swallowed], [1])
            self.assertTrue(swallowed[0][1].startswith("Vector search quantifies"))
            # A picture that is the page's background holds the page's text, not a figure's.
            self.assertEqual(prose_inside_figures(pdf_path, [(1, (0.0, 0.0, 531.0, 666.0))]), [])


def _book_test(book: dict[str, Any]):
    @unittest.skipUnless(RUN_CORPUS, "Set BOOK_AGENT_RUN_CORPUS_TESTS=1 to parse the real-book corpus.")
    def test(self: unittest.TestCase) -> None:
        # The owner moves books between disks: the first of its recorded places that exists.
        candidates = [Path(book["path"]), *(Path(alt).expanduser() for alt in book.get("alt_paths", []))]
        path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
        if not path.exists():
            self.skipTest(f"corpus book not on this machine: {path}")
        with tempfile.TemporaryDirectory() as tmpdir:
            source_type, titles, checks, figures = parse_book(path, Path(tmpdir))
        failures = corpus_failures(book, source_type=source_type, chapter_titles=titles, checks=checks)
        swallowed = prose_inside_figures(path, figures)
        # A book recorded with open figure-region work names how many such paragraphs it still has.
        if len(swallowed) > int(book.get("max_prose_inside_figures", 0)):
            failures += [f"body paragraph inside a figure: p{page} {text}" for page, text in swallowed]
        self.assertEqual(failures, [], f"{book['id']} no longer parses as recorded")

    test.__doc__ = f"{book['id']} parses as recorded in the corpus manifest."
    return test


class CorpusBooksTest(unittest.TestCase):
    pass


for _book in load_manifest():
    setattr(CorpusBooksTest, f"test_{_book['id'].replace('-', '_')}", _book_test(_book))


if __name__ == "__main__":
    unittest.main()
