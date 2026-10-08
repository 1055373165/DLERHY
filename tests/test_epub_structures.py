"""O'Reilly (HTMLBook) structures in an EPUB: lists, definition lists, note and sidebar boxes.

"An Illustrated Guide to AI Agents" showed its <ul>/<ol> items as plain paragraphs, lost every
<dt> term of its definition lists (the components list read "智能体用于与环境交互的工具" with
no "Actuators"), and set a <div data-type="note"> as a big "注意" heading over loose paragraphs.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from book_agent.domain.structure.epub import EPUB_STRUCTURE_VERSION, EPUBParser
from book_agent.export import markup
from book_agent.export.models import MergedRenderBlock
from tests.test_epub_parser import CONTAINER_XML, CONTENT_OPF, NAV_XHTML

CHAPTER = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <section data-type="chapter" id="ch1">
      <h1>Chapter One</h1>
      <p>An agent has these parts:</p>
      <dl>
        <dt>Environment</dt>
        <dd><p>The world the agent interacts with</p></dd>
        <dt><code>run</code></dt>
        <dd>Run the agent on a task</dd>
      </dl>
      <ul>
        <li><p><em>Understand</em> multiple modalities</p></li>
        <li><p>Generate them
          <ul><li><p>images</p></li><li><p>audio</p></li></ul>
        </p></li>
      </ul>
      <ol start="3">
        <li><p>The user asks.</p></li>
        <li><p>The agent answers.</p></li>
      </ol>
      <div data-type="note" epub:type="note"><h6>Note</h6>
        <p>Lists like this go out of date quickly.</p>
      </div>
      <aside data-type="sidebar" epub:type="sidebar"><div class="sidebar" id="built">
        <h1>What We Built</h1>
        <pre>TinyAgent/</pre>
      </div></aside>
      <h1>Summary</h1>
      <p>The end.</p>
    </section>
  </body>
</html>
"""


def _parse(chapter: str = CHAPTER):
    with tempfile.TemporaryDirectory() as tmpdir:
        epub_path = Path(tmpdir) / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", chapter)
        return EPUBParser().parse(epub_path).chapters[0].blocks


def _by_text(blocks, text):
    return next(block for block in blocks if block.text == text)


class ParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.blocks = _parse()

    def test_definition_terms_are_kept_with_their_descriptions(self) -> None:
        term = _by_text(self.blocks, "Environment")
        self.assertEqual(term.block_type, "paragraph")
        self.assertTrue(term.metadata["definition_term"])
        description = _by_text(self.blocks, "The world the agent interacts with")
        self.assertTrue(description.metadata["definition_description"])
        self.assertEqual(term.metadata["definition_list_id"], description.metadata["definition_list_id"])
        # A <dd> without paragraphs is its own description block.
        self.assertTrue(_by_text(self.blocks, "`run`").metadata["definition_term"])
        self.assertTrue(_by_text(self.blocks, "Run the agent on a task").metadata["definition_description"])

    def test_list_items_know_their_list(self) -> None:
        first = _by_text(self.blocks, "*Understand* multiple modalities")
        self.assertEqual((first.block_type, first.metadata["list_kind"], first.metadata["list_level"]), ("list_item", "bullet", 1))
        parent = _by_text(self.blocks, "Generate them")
        self.assertEqual(parent.metadata["list_level"], 1)
        nested = _by_text(self.blocks, "images")
        self.assertEqual((nested.metadata["list_kind"], nested.metadata["list_level"]), ("bullet", 2))
        numbered = [_by_text(self.blocks, "The user asks."), _by_text(self.blocks, "The agent answers.")]
        self.assertEqual([(b.metadata["list_kind"], b.metadata["list_number"]) for b in numbered], [("number", 3), ("number", 4)])

    def test_a_note_is_one_titled_box(self) -> None:
        title = _by_text(self.blocks, "Note")
        body = _by_text(self.blocks, "Lists like this go out of date quickly.")
        self.assertEqual(title.block_type, "paragraph")
        self.assertTrue(title.metadata["sidebar_title"])
        self.assertNotIn("heading_level", title.metadata)
        self.assertEqual((title.metadata["sidebar_kind"], body.metadata["sidebar_kind"]), ("note", "note"))
        self.assertEqual(title.metadata["sidebar_id"], body.metadata["sidebar_id"])

    def test_a_sidebar_heading_is_its_title_not_a_section(self) -> None:
        title = _by_text(self.blocks, "What We Built")
        code = _by_text(self.blocks, "TinyAgent/")
        self.assertEqual((title.block_type, title.metadata["sidebar_kind"]), ("paragraph", "sidebar"))
        self.assertTrue(title.metadata["sidebar_title"])
        self.assertEqual(code.metadata["sidebar_id"], title.metadata["sidebar_id"])
        note = _by_text(self.blocks, "Note")
        self.assertNotEqual(note.metadata["sidebar_id"], title.metadata["sidebar_id"])
        # The section heading after the box is a heading again, outside it.
        summary = _by_text(self.blocks, "Summary")
        self.assertEqual(summary.block_type, "heading")
        self.assertNotIn("sidebar_id", summary.metadata)

    def test_every_block_carries_the_structure_version(self) -> None:
        self.assertTrue(all(block.metadata.get("epub_structure_version") == EPUB_STRUCTURE_VERSION for block in self.blocks))


def _render_block(text: str, metadata: dict, *, block_type: str = "paragraph", target: str | None = None) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id=f"b-{abs(hash(text))}",
        chapter_id="c1",
        block_type=block_type,
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text=text,
        target_text=target,
        source_metadata=metadata,
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )


class RenderTest(unittest.TestCase):
    def test_bullet_and_numbered_items_draw_their_markers(self) -> None:
        bullet = markup.render_block_html(_render_block("Understand", {"list_kind": "bullet", "list_level": 1}, block_type="list_item", target="理解"))
        self.assertIn("class='block list_item bulleted'", bullet)
        numbered = markup.render_block_html(
            _render_block("2024 was the year.", {"list_kind": "number", "list_level": 1, "list_number": 3}, block_type="list_item", target="2024 年是关键的一年。")
        )
        self.assertIn("data-marker='3.'", numbered)
        # The item's own leading number is text, not its marker.
        self.assertIn("2024 年是关键的一年。", numbered)
        nested = markup.render_block_html(_render_block("images", {"list_kind": "bullet", "list_level": 2}, block_type="list_item", target="图像"))
        self.assertIn("sub-item", nested)

    def test_definition_terms_and_descriptions(self) -> None:
        term = markup.render_block_html(_render_block("Environment", {"definition_term": True}, target="环境"))
        description = markup.render_block_html(_render_block("The world", {"definition_description": True}, target="世界"))
        self.assertIn("definition-term", term)
        self.assertIn("definition-description", description)

    def test_a_note_box_is_one_aside_with_its_kind(self) -> None:
        box = {"sidebar_id": "x#note-1", "sidebar_kind": "note"}
        blocks = [
            _render_block("Note", {**box, "sidebar_title": True}, target="注意"),
            _render_block("Lists go out of date.", box, target="清单很快会过时。"),
            _render_block("After.", {}, target="之后。"),
        ]
        html = markup.render_blocks_html(blocks, markup.render_block_html)
        self.assertEqual(html.count("<aside"), 1)
        self.assertIn("<aside class='book-sidebar book-note'>", html)
        self.assertLess(html.index("清单很快会过时。"), html.index("</aside>"))
        self.assertGreater(html.index("之后。"), html.index("</aside>"))
        self.assertIn("sidebar-title", html)

    def test_markdown_numbers_an_ol_item_and_bolds_a_term(self) -> None:
        item = markup.render_block_markdown(
            _render_block("The user asks.", {"list_kind": "number", "list_level": 1, "list_number": 1}, block_type="list_item", target="用户提问。")
        )
        self.assertEqual(item, "1. 用户提问。")
        nested = markup.render_block_markdown(_render_block("images", {"list_kind": "bullet", "list_level": 2}, block_type="list_item", target="图像"))
        self.assertEqual(nested, "  - 图像")
        term = markup.render_block_markdown(_render_block("Environment", {"definition_term": True}, target="环境"))
        self.assertEqual(term, "**环境**")


OLD_PARSE = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>An agent has these parts:</p>
    <dl><dd><p>The world the agent interacts with</p></dd><dd><p>Tools the agent uses</p></dd></dl>
    <p>The parts work together in a loop.</p>
  </body>
</html>
"""
NEW_PARSE = OLD_PARSE.replace("<dl><dd>", "<dl><dt>Environment</dt><dd>").replace("</dd><dd>", "</dd><dt>Actuators</dt><dd>")


class RefreshTest(unittest.TestCase):
    """A book parsed before the parser kept <dt> terms: a structure refresh adds the terms as new
    blocks; every other block keeps its id, its sentences and its translation."""

    def setUp(self) -> None:
        from sqlalchemy.pool import StaticPool

        from book_agent.infra.db.base import Base
        from book_agent.infra.db.session import build_engine, build_session_factory
        from book_agent.infra.repositories.bootstrap import BootstrapRepository
        from book_agent.infra.repositories.translation import TranslationRepository
        from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
        from book_agent.services.translation import TranslationService

        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'refresh.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.epub_path = Path(self.tempdir.name) / "sample.epub"
        self._write(OLD_PARSE)
        artifacts = BootstrapOrchestrator().bootstrap_epub(self.epub_path)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
            service = TranslationService(TranslationRepository(session))
            for packet in artifacts.translation_packets:
                service.execute_packet(packet.id)
            session.commit()
        self.document_id = artifacts.document.id

    def _write(self, chapter: str) -> None:
        with zipfile.ZipFile(self.epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", chapter)

    def _state(self, session):
        from sqlalchemy import select

        from book_agent.domain.enums import ArtifactStatus
        from book_agent.domain.models import Block
        from book_agent.infra.repositories.review import active_target_texts
        from book_agent.domain.models import Sentence

        blocks = session.scalars(select(Block).where(Block.status == ArtifactStatus.ACTIVE).order_by(Block.ordinal)).all()
        sentences = session.scalars(select(Sentence).where(Sentence.retired_by_revision_id.is_(None))).all()
        targets = active_target_texts(session, [sentence.id for sentence in sentences])
        by_block = {sentence.block_id: targets.get(sentence.id, "") for sentence in sentences}
        return [(block.id, block.ordinal, block.source_text, by_block.get(block.id, "")) for block in blocks]

    def test_new_terms_are_added_and_the_rest_keeps_its_ids_and_translations(self) -> None:
        from book_agent.services.workflows import DocumentWorkflowService

        with self.session_factory() as session:
            before = {text: (block_id, target) for block_id, _ordinal, text, target in self._state(session)}
        self._write(NEW_PARSE)
        with self.session_factory() as session:
            DocumentWorkflowService(session, export_root=Path(self.tempdir.name) / "exports").refresh_epub_structure(self.document_id)
            session.commit()
            after = self._state(session)
        texts = [text for _id, _ordinal, text, _target in after]
        self.assertEqual(
            texts,
            [
                "Chapter One",
                "An agent has these parts:",
                "Environment",
                "The world the agent interacts with",
                "Actuators",
                "Tools the agent uses",
                "The parts work together in a loop.",
            ],
        )
        self.assertEqual([ordinal for _id, ordinal, _text, _target in after], list(range(1, 8)))
        for block_id, _ordinal, text, target in after:
            if text in before:
                self.assertEqual(block_id, before[text][0], text)
                self.assertEqual(target, before[text][1], text)
        self.assertEqual(len({block_id for block_id, *_rest in after}), 7)


if __name__ == "__main__":
    unittest.main()
