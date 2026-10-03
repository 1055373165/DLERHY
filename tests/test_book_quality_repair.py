"""Repair loop fixes for the whole-book gate: translation memory, settled terms, retries, new paths.

Runs on the pilot repair bench (tests/pilot_repair_fixture.py) with its fake Chinese model.
"""

from __future__ import annotations

import unittest

from sqlalchemy import select

from book_agent.domain.enums import BlockType, PacketStatus
from book_agent.domain.models import Block
from book_agent.domain.models.translation import TranslationPacket
from book_agent.domain.quality.pilot_repair import REPAIR_PATHS, WorkItem
from book_agent.infra.repositories.review import active_target_texts
from book_agent.services.pilot_repair_tools import PilotRepairToolbox, RepairBudget, RepairContext
from tests.pilot_repair_fixture import block_with_text, packet_of, sentence_ids
from tests.test_pilot_repair import PilotRepairBenchCase


def _path(check_id: str, name: str):
    return next(path for path in REPAIR_PATHS[check_id] if path.name == name)


class ToolboxCase(PilotRepairBenchCase):
    def toolbox(self, session, chapter_ids: list[str]) -> PilotRepairToolbox:
        ctx = RepairContext(
            session=session,
            document_id=self.bench.document_id,
            run=None,
            chapter_ids=list(chapter_ids),
            workflow_factory=self.bench.workflow,
            budget=RepairBudget(budget_usd=None, max_packets=100),
        )
        return PilotRepairToolbox(ctx)

    def restyle(self, contains: str, *, text: str, block_type: str) -> str:
        from book_agent.services.parse_revision_fork import ParseRevisionForkService

        with self.bench.session_factory() as session:
            block = block_with_text(session, self.bench.document_id, contains)
            block.source_text = text
            block.normalized_text = text
            block.block_type = BlockType(block_type)
            session.flush()
            fork = ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[block.id], reason="inject")
            workflow = self.bench.workflow(session)
            for packet_id in fork.retranslate_packet_ids:
                workflow.translation_service.execute_packet(packet_id)
            session.commit()
            return block.id


class TranslationMemoryTests(ToolboxCase):
    def test_a_restored_heading_the_book_already_translates_is_filled_without_retranslation(self) -> None:
        chapter_ids = self.translate()
        # Chapter 1 has a translated "Summary" heading; chapter 2's "Summary" was hidden as code.
        self.restyle("This chapter explains why", text="Summary", block_type="heading")
        hidden = self.restyle("The second technique masks", text="Summary", block_type="code")
        with self.bench.session_factory() as session:
            first = next(sid for sid in sentence_ids(session, self.bench.document_id, "Summary") if session.get(Block, hidden).id not in sid)
            known = active_target_texts(session, sentence_ids(session, self.bench.document_id, "Summary"))
            summary_zh = next(text for text in known.values() if text)
            neighbours = sentence_ids(session, self.bench.document_id, "The first technique reads")
            before = active_target_texts(session, neighbours)
        self.worker.calls.clear()

        with self.bench.session_factory() as session:
            toolbox = self.toolbox(session, chapter_ids)
            finding = {
                "key": f"L1.heading_as_text:block:{hidden}",
                "check_id": "L1.heading_as_text",
                "severity": "major",
                "location": {"block_id": hidden},
                "target": {"kind": "block", "id": hidden},
                "evidence": {"heading_level": 2},
            }
            result = toolbox.apply(WorkItem("L1.heading_as_text", _path("L1.heading_as_text", "structure_edit.relabel_as_heading"), [finding]))
            session.commit()

            self.assertEqual(self.worker.calls, [], "the packet was retranslated instead of filled from memory")
            self.assertEqual(result.packet_ids, [])
            self.assertTrue(result.params["translation_memory"])
            heading_sentences = [
                sid for sid in sentence_ids(session, self.bench.document_id, "Summary") if sid != first
            ]
            targets = active_target_texts(session, heading_sentences)
            self.assertIn(summary_zh, targets.values())
            # The rest of the packet is exactly as it was.
            self.assertEqual(active_target_texts(session, neighbours), before)
            packet = session.get(TranslationPacket, packet_of(session, heading_sentences[-1]))
            self.assertEqual(packet.status, PacketStatus.TRANSLATED)

    def test_retranslation_hints_carry_the_books_settled_term_renderings(self) -> None:
        chapter_ids = self.translate()
        from book_agent.services.glossary_service import GlossaryService

        with self.bench.session_factory() as session:
            GlossaryService(session).lock_term(
                self.bench.document_id, "LLM", "大语言模型", target_variants=["大型语言模型"], lock_level=__import__("book_agent.domain.enums", fromlist=["LockLevel"]).LockLevel.PREFERRED
            )
            session.commit()
            sentence = sentence_ids(session, self.bench.document_id, "An LLM answers from")[0]
            packet = packet_of(session, sentence)
        self.worker.calls.clear()
        with self.bench.session_factory() as session:
            toolbox = self.toolbox(session, chapter_ids)
            toolbox.retranslate([packet], ["上一版译文的问题：漏译。"])
            session.commit()
        hints = self.worker.calls[-1]["hints"]
        self.assertTrue(any("「LLM」译为「大语言模型」" in hint for hint in hints), hints)


class FigurePathTests(ToolboxCase):
    def _figure(self, session) -> Block:
        return next(
            block
            for block in session.scalars(select(Block).where(Block.source_text.in_(["[Figure]", "[Image]"]))).all()
            if (block.source_span_json or {}).get("source_page_start") == 2
        )

    def _finding(self, check_id: str, block_id: str) -> dict:
        return {
            "key": f"{check_id}:block:{block_id}",
            "check_id": check_id,
            "severity": "minor",
            "location": {"block_id": block_id},
            "target": {"kind": "block", "id": block_id},
            "evidence": {},
        }

    def test_an_uncaptioned_figure_is_linked_to_the_caption_beside_it_or_accepted(self) -> None:
        from book_agent.domain.enums import ArtifactStatus
        from book_agent.services.pilot_repair_tools import NotApplicable

        chapter_ids = self.translate()
        with self.bench.session_factory() as session:
            figure = self._figure(session)
            caption_id = (figure.source_span_json or {}).get("linked_caption_block_id")
            # The link is lost (the parser did not pair them): the figure has no caption.
            span = dict(figure.source_span_json)
            span.pop("linked_caption_block_id", None)
            figure.source_span_json = span
            session.commit()
            found = [f for f in self._findings_now(chapter_ids) if f["check_id"] == "L2.figure_without_caption"]
            self.assertTrue(caption_id)
            if caption_id:
                self.assertTrue(found)
                toolbox = self.toolbox(session, chapter_ids)
                item = WorkItem("L2.figure_without_caption", _path("L2.figure_without_caption", "structure_edit.link_adjacent_caption"), [self._finding("L2.figure_without_caption", figure.id)])
                result = toolbox.apply(item)
                self.assertEqual(result.params["caption_block_id"], caption_id)
                session.commit()
                self.assertEqual(session.get(Block, figure.id).source_span_json.get("linked_caption_block_id"), caption_id)
                # With its caption taken, nothing is left to accept on the page... unless it has none at all.
                caption = session.get(Block, caption_id)
                caption.status = ArtifactStatus.INVALIDATED
                span = dict(session.get(Block, figure.id).source_span_json)
                span.pop("linked_caption_block_id", None)
                session.get(Block, figure.id).source_span_json = span
                session.commit()
            accept = WorkItem(
                "L2.figure_without_caption",
                _path("L2.figure_without_caption", "structure_edit.accept_uncaptioned_figure"),
                [self._finding("L2.figure_without_caption", figure.id)],
            )
            try:
                self.toolbox(session, chapter_ids).apply(accept)
            except NotApplicable as exc:  # pragma: no cover - a caption left on the page
                self.fail(str(exc))
            session.commit()
            self.assertTrue(session.get(Block, figure.id).source_span_json.get("figure_caption_absent"))
        self.assertEqual([f for f in self._findings_now(chapter_ids) if f["check_id"] == "L2.figure_without_caption" and f["location"].get("block_id") == figure.id], [])

    def _findings_now(self, chapter_ids: list[str]) -> list[dict]:
        return [finding for layer in self.qa(chapter_ids) for finding in layer["findings"]]

    def test_a_sparse_table_next_to_a_figure_is_absorbed(self) -> None:
        chapter_ids = self.translate()
        with self.bench.session_factory() as session:
            figure = self._figure(session)
            (left, top, right, bottom) = figure.source_span_json["source_bbox_json"]["regions"][0]["bbox"]
        table_id = self.restyle("The first technique reads", text="| a |  |  |\n|  |  | b |\n|  |  |  |", block_type="table")
        below = [left, bottom + 4.0, right, bottom + 30.0]
        with self.bench.session_factory() as session:
            table = session.get(Block, table_id)
            table.source_span_json = {**table.source_span_json, "source_bbox_json": {"regions": [{"page_number": 2, "bbox": below}]}}
            session.commit()
            item = WorkItem("L3.table_sparse", _path("L3.table_sparse", "structure_edit.absorb_sparse_table_into_figure"), [self._finding("L3.table_sparse", table_id)])
            result = self.toolbox(session, chapter_ids).apply(item)
            session.commit()
            self.assertFalse(result.params["inside"])
            self.assertTrue(session.get(Block, table_id).source_span_json.get("repair_hidden_from_export"))
            grown = session.get(Block, figure.id).source_span_json["source_bbox_json"]["regions"][0]["bbox"]
            self.assertAlmostEqual(grown[3], below[3], places=1)
            self.assertAlmostEqual(grown[1], top, places=1)

    def test_a_sparse_table_far_from_any_figure_is_left_alone(self) -> None:
        from book_agent.services.pilot_repair_tools import NotApplicable

        chapter_ids = self.translate()
        table_id = self.restyle("An indexing pipeline splits", text="| a |  |  |\n|  |  | b |", block_type="table")
        with self.bench.session_factory() as session:
            table = session.get(Block, table_id)
            table.source_span_json = {**table.source_span_json, "source_bbox_json": {"regions": [{"page_number": 3, "bbox": [100, 300, 400, 340]}]}}
            session.commit()
            item = WorkItem("L3.table_sparse", _path("L3.table_sparse", "structure_edit.absorb_sparse_table_into_figure"), [self._finding("L3.table_sparse", table_id)])
            with self.assertRaises(NotApplicable):
                self.toolbox(session, chapter_ids).apply(item)


class FlakyRetranslationTests(ToolboxCase):
    def test_a_repair_reverted_only_for_its_retranslations_flake_is_retried_once(self) -> None:
        with self.bench.session_factory() as session:
            (omitted,) = sentence_ids(session, self.bench.document_id, "Teams track the score")
            session.commit()
        self.worker.omit_sentences.add(omitted)
        self.translate()
        worker = self.worker
        original = worker.translate
        retranslations = {"count": 0}

        def flaky(task):
            # The first retranslation leaks a program note (the model's flake); the next one is clean.
            if task.context_packet.packet_id in worker.translated_once:
                retranslations["count"] += 1
                worker.retranslation_leaks = retranslations["count"] == 1
            return original(task)

        worker.translate = flaky
        payload, final = self.repair()

        (attempt,) = [a for a in payload["attempts"] if a["check_id"] == "L6.untranslated_sentence"]
        self.assertEqual(attempt["outcome"], "kept", attempt["reason"])
        self.assertTrue(attempt["params"]["retried_packet_ids"])
        self.assertIn("L7.leaked_marker", attempt["params"]["retry_reason"])
        self.assertEqual(retranslations["count"], 2)
        with self.bench.session_factory() as session:
            text = active_target_texts(session, [omitted]).get(omitted, "")
        self.assertTrue(text)
        self.assertNotIn("图片锚点保留", text)

    def test_a_repair_that_is_really_worse_is_still_reverted_after_one_retry(self) -> None:
        with self.bench.session_factory() as session:
            (omitted,) = sentence_ids(session, self.bench.document_id, "Teams track the score")
            session.commit()
        self.worker.omit_sentences.add(omitted)
        self.worker.retranslation_leaks = True
        self.translate()
        payload, _final = self.repair()
        (attempt,) = payload["attempts"]
        self.assertEqual(attempt["outcome"], "reverted")
        self.assertTrue(attempt["params"]["retried_packet_ids"])


class HintedStructureEditTests(ToolboxCase):
    """The four structure edits the parser side's QA rules name in their repair hints."""

    def _item(self, check_id: str, hint: str, target_id: str, **evidence) -> WorkItem:
        from book_agent.domain.quality.pilot_repair import paths_for

        finding = {
            "key": f"{check_id}:block:{target_id}",
            "check_id": check_id,
            "severity": "major",
            "repair": f"structure_edit.{hint}",
            "location": {"block_id": target_id},
            "target": {"kind": "block", "id": target_id},
            "evidence": evidence,
        }
        path = paths_for(check_id, finding)[0]
        self.assertEqual(path.name, f"structure_edit.{hint}")
        return WorkItem(check_id, path, [finding])

    def test_a_hint_from_an_unknown_check_is_routed_to_its_tool(self) -> None:
        from book_agent.domain.quality.pilot_repair import paths_for, plan_work

        finding = {"key": "k", "check_id": "L1.some_new_rule", "severity": "major", "repair": "structure_edit.join_continuation", "location": {}}
        self.assertEqual([path.name for path in paths_for("L1.some_new_rule", finding)], ["structure_edit.join_continuation"])
        self.assertEqual(paths_for("L1.some_new_rule", {**finding, "repair": "structure_edit.rm_rf"}), ())
        (work,) = plan_work([finding], set())
        self.assertEqual(work.path.name, "structure_edit.join_continuation")

    def test_nest_list_items(self) -> None:
        chapter_ids = self.translate()
        dash = self.restyle("Retrieval gives the LLM facts", text="– Retrieval gives the LLM facts from outside sources.", block_type="list_item")
        with self.bench.session_factory() as session:
            result = self.toolbox(session, chapter_ids).apply(self._item("L1.sublist_as_text", "nest_list_items", dash))
            session.commit()
            span = session.get(Block, dash).source_span_json
            self.assertEqual((span["list_level"], span["list_kind"]), (2, "dash"))
            self.assertEqual(result.params["items"][0]["block_id"], dash)

    def test_nest_sub_items_set_inside_their_bullet(self) -> None:
        chapter_ids = self.translate()
        parent = self.restyle(
            "Retrieval gives the LLM facts",
            text="Retrieval gives the LLM facts from outside sources.\n\u2013 Basic data structures\n\u2013 Vector stores",
            block_type="list_item",
        )
        with self.bench.session_factory() as session:
            item = self._item(
                "L1.sublist_as_text", "nest_list_items", parent, parent_block_id=parent, inline_items=["\u2013 Basic data structures", "\u2013 Vector stores"]
            )
            result = self.toolbox(session, chapter_ids).apply(item)
            session.commit()
            items = [session.get(Block, entry["block_id"]) for entry in result.params["items"]]
            self.assertEqual([block.source_text.strip() for block in items], ["\u2013 Basic data structures", "\u2013 Vector stores"])
            self.assertTrue(all(block.source_span_json["list_level"] == 2 and block.source_span_json["list_kind"] == "dash" for block in items))
            self.assertNotIn("Vector stores", session.get(Block, parent).source_text)

    def test_relabel_as_sidebar(self) -> None:
        chapter_ids = self.translate()
        title = self.restyle("An LLM answers from", text="Why tokens matter", block_type="heading")
        with self.bench.session_factory() as session:
            body = block_with_text(session, self.bench.document_id, "Retrieval gives the LLM facts").id
            item = self._item("L1.sidebar_as_text", "relabel_as_sidebar", title, title_block_id=title, member_block_ids=[body])
            self.toolbox(session, chapter_ids).apply(item)
            session.commit()
            title_block, body_block = session.get(Block, title), session.get(Block, body)
            self.assertEqual(title_block.block_type, BlockType.PARAGRAPH)
            self.assertTrue(title_block.source_span_json["sidebar_title"])
            self.assertEqual(title_block.source_span_json["sidebar_id"], body_block.source_span_json["sidebar_id"])
            self.assertNotIn("sidebar_title", body_block.source_span_json)

    def test_join_continuation_across_a_figure(self) -> None:
        chapter_ids = self.translate()
        with self.bench.session_factory() as session:
            figure = next(
                block
                for block in session.scalars(select(Block).where(Block.source_text.in_(["[Figure]", "[Image]"]))).all()
                if (block.source_span_json or {}).get("source_page_start") == 2
            )
            blocks = self.toolbox(session, chapter_ids)._chapter_blocks(figure.chapter_id)
            index = next(i for i, block in enumerate(blocks) if block.id == figure.id)
            before = next(block for block in reversed(blocks[:index]) if block.block_type == BlockType.PARAGRAPH)
            after = next(block for block in blocks[index + 1 :] if block.block_type == BlockType.PARAGRAPH)
            between = [block for block in blocks if before.ordinal < block.ordinal < after.ordinal]
            if any(block.block_type not in {BlockType.FIGURE, BlockType.IMAGE, BlockType.CAPTION} and (block.source_span_json or {}).get("pdf_block_role") not in {"header", "footer"} for block in between):
                self.skipTest("the bench puts text between the paragraphs around its figure")
            first_text, second_text = before.source_text, after.source_text
            # As the parser side's rule reports it: on the second half, joining into the first.
            item = self._item("L1.paragraph_split_midsentence", "join_continuation", after.id, block_id=after.id, join_into_block_id=before.id)
            self.toolbox(session, chapter_ids).apply(item)
            session.commit()
            joined = session.get(Block, before.id)
            self.assertIn(first_text.strip()[:30], joined.source_text)
            self.assertIn(second_text.strip()[:30], joined.source_text)
            self.assertEqual(session.get(Block, after.id).status.value, "invalidated")

    def test_link_listing_caption(self) -> None:
        chapter_ids = self.translate()
        caption = self.restyle("The first technique reads", text="Listing 1.1 The retrieval loop", block_type="paragraph")
        code = self.restyle("The second technique masks", text="def retrieve(query):\n    return store.search(query)", block_type="code")
        with self.bench.session_factory() as session:
            self.toolbox(session, chapter_ids).apply(self._item("L2.listing_caption_unlinked", "link_listing_caption", caption, caption_block_id=caption, code_block_id=code))
            session.commit()
            self.assertEqual(session.get(Block, caption).block_type, BlockType.CAPTION)
            self.assertEqual(session.get(Block, code).source_span_json["linked_caption_block_id"], caption)


class BookScopeRepairTests(ToolboxCase):
    """The whole book's QA and repair (services/book_quality.py) on every chapter, with the fake model."""

    def test_the_book_repair_covers_chapters_the_pilot_never_saw(self) -> None:
        from book_agent.domain.models.review import PilotQualityReport
        from book_agent.services.book_quality import BOOK_QA_TRIGGER, book_chapter_ids, book_repair_budget
        from book_agent.services.pilot_quality import PilotQualityService
        from book_agent.services.pilot_repair import PilotRepairService, repair_payload

        with self.bench.session_factory() as session:
            # Chapter 3 is outside the pilot: a sentence the model left out there.
            third = self.bench.chapters(session)[2].id
            from book_agent.domain.models import Sentence

            omitted = session.scalars(
                select(Sentence.id)
                .where(Sentence.chapter_id == third, Sentence.translatable.is_(True), Sentence.retired_by_revision_id.is_(None))
                .order_by(Sentence.id)
            ).first()
            session.commit()
        self.worker.omit_sentences.add(omitted)
        pilot = self.translate(all_chapters=True)
        with self.bench.session_factory() as session:
            chapters = book_chapter_ids(session, self.bench.document_id)
            self.assertGreater(len(chapters), len(pilot))
            before = PilotQualityService(session).run(
                self.bench.document_id, trigger=BOOK_QA_TRIGGER, chapter_ids=chapters, scope="book"
            )
            session.commit()
            self.assertEqual(before.scope, "book")
            keys = [f["key"] for layer in before.layers_json for f in layer["findings"] if f["check_id"] == "L6.untranslated_sentence"]
            self.assertTrue(keys)
            budget = book_repair_budget(session, chapters)
            # No prices in the bench: 15% of the book's packets bound the retranslations.
            self.assertIsNone(budget.budget_usd)
            self.assertGreaterEqual(budget.max_packets, 1)

            service = PilotRepairService(session, workflow_factory=self.bench.workflow, scope="book")
            repair = service.run(self.bench.document_id)
            payload = repair_payload(session, repair)
            final = session.get(PilotQualityReport, repair.after_report_id)
        self.assertEqual((payload["scope"], final.scope, final.trigger), ("book", "book", "book_repair"))
        self.assertEqual(set(final.chapter_ids_json), set(chapters))
        kept = [a for a in payload["attempts"] if a["outcome"] == "kept" and a["check_id"] == "L6.untranslated_sentence"]
        self.assertTrue(kept, [(a["check_id"], a["path"], a["outcome"], a["reason"]) for a in payload["attempts"]] + [payload.get("stop_reason"), payload.get("summary")])
        self.assertEqual([f for layer in final.layers_json for f in layer["findings"] if f["key"] in keys], [])
        with self.bench.session_factory() as session:
            self.assertTrue(active_target_texts(session, [omitted]).get(omitted))
        # The pilot's own report is untouched by the book's.
        with self.bench.session_factory() as session:
            from book_agent.services.pilot_quality import latest_report

            self.assertIsNone(latest_report(session, self.bench.document_id))
            self.assertEqual(latest_report(session, self.bench.document_id, scope="book").id, final.id)


class DefectTicketTests(ToolboxCase):
    def test_a_defect_seen_again_updates_its_ticket(self) -> None:
        from book_agent.domain.models.review import PilotDefectTicket
        from tests.pilot_repair_fixture import edit_translation

        self.translate()
        with self.bench.session_factory() as session:
            (sentence,) = sentence_ids(session, self.bench.document_id, "The first technique reads")
            edit_translation(session, sentence, lambda current: current.replace("这一句", "这¡句"))
            session.commit()
        first, _ = self.repair()
        with self.bench.session_factory() as session:
            (second_sentence,) = sentence_ids(session, self.bench.document_id, "The second technique masks")
            edit_translation(session, second_sentence, lambda current: current.replace("这一句", "这¡句"))
            session.commit()
        second, _ = self.repair()
        with self.bench.session_factory() as session:
            tickets = session.scalars(select(PilotDefectTicket).where(PilotDefectTicket.check_id == "L7.stray_glyph")).all()
            self.assertEqual(len(tickets), 1)
            ticket = tickets[0]
            self.assertEqual(ticket.impact_json["occurrences"], 2)
            self.assertEqual(ticket.impact_json["repair_ids"], [first["repair_id"], second["repair_id"]])
            self.assertEqual(ticket.impact_json["findings_before"], 2)
            self.assertEqual(len({item["key"] for item in ticket.evidence_json}), 2)
        # Both repairs list it.
        self.assertEqual([t["ticket_id"] for t in second["tickets"] if t["check_id"] == "L7.stray_glyph"], [ticket.id])
        self.assertEqual([t["ticket_id"] for t in first["tickets"] if t["check_id"] == "L7.stray_glyph"], [ticket.id])


class StaleDraftTests(ToolboxCase):
    """After a merge, one sentence had the current translation of several packets (the p.53 callout)."""

    def test_a_fork_over_packets_on_one_block_keeps_one_and_invalidates_the_rest(self) -> None:
        from book_agent.domain.enums import PacketSentenceRole
        from book_agent.domain.models import Sentence
        from book_agent.domain.models.translation import PacketSentenceMap
        from book_agent.services.parse_revision_fork import ParseRevisionForkService

        self.translate()
        with self.bench.session_factory() as session:
            block = block_with_text(session, self.bench.document_id, "An LLM answers from")
            owner = session.get(TranslationPacket, packet_of(session, sentence_ids(session, self.bench.document_id, "An LLM answers from")[0]))
            # A merge moved a second packet's bounds onto the same blocks (the p.53 callout had six).
            twin = TranslationPacket(
                id="00000000-0000-4000-8000-00000000d0b1",
                chapter_id=owner.chapter_id,
                block_start_id=owner.block_start_id,
                block_end_id=owner.block_end_id,
                packet_type=owner.packet_type,
                book_profile_version=owner.book_profile_version,
                chapter_brief_version=owner.chapter_brief_version,
                termbase_version=owner.termbase_version,
                entity_snapshot_version=owner.entity_snapshot_version,
                packet_json=dict(owner.packet_json or {}),
                status=PacketStatus.TRANSLATED,
            )
            session.add(twin)
            session.flush()
            for mapping in session.scalars(select(PacketSentenceMap).where(PacketSentenceMap.packet_id == owner.id)).all():
                session.add(PacketSentenceMap(packet_id=twin.id, sentence_id=mapping.sentence_id, role=mapping.role))
            session.flush()
            block.source_text = block.source_text + " It is a new sentence here."
            block.normalized_text = block.source_text
            fork = ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[block.id], reason="test")
            session.commit()
            self.assertIn(twin.id, fork.invalidated_packet_ids)
            self.assertEqual(session.get(TranslationPacket, twin.id).status, PacketStatus.INVALIDATED)
            current = list(session.scalars(select(Sentence.id).where(Sentence.block_id == block.id, Sentence.retired_by_revision_id.is_(None))).all())
            owners = session.execute(
                select(PacketSentenceMap.sentence_id, PacketSentenceMap.packet_id).where(
                    PacketSentenceMap.sentence_id.in_(current), PacketSentenceMap.role == PacketSentenceRole.CURRENT
                )
            ).all()
            self.assertEqual({packet_id for _sentence, packet_id in owners}, {owner.id})
            # Nothing is left to translate in the invalidated packet: progress ignores it.
            from book_agent.orchestrator.stage_status import StageStatusCalculator

            evidence = StageStatusCalculator(session)._translate_evidence("no-run", self.bench.document_id)
            self.assertNotEqual(evidence.status.value, "failed")

    def test_the_review_query_prefers_the_packet_that_owns_the_sentence(self) -> None:
        """Another packet's newer-numbered attempt left active does not replace the owner's text."""
        from book_agent.core.ids import stable_id
        from book_agent.domain.enums import RunStatus, TargetSegmentStatus
        from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationRun

        chapter_ids = self.translate()
        with self.bench.session_factory() as session:
            sentence = sentence_ids(session, self.bench.document_id, "An LLM answers from")[0]
            owner = packet_of(session, sentence)
            before = active_target_texts(session, [sentence])[sentence]
            other = session.scalars(
                select(TranslationPacket.id).where(TranslationPacket.chapter_id == chapter_ids[1], TranslationPacket.id != owner)
            ).first()
            run = TranslationRun(
                id=stable_id("translation-run", other, 99),
                packet_id=other,
                model_name="stale",
                model_config_json={},
                prompt_version="x",
                attempt=99,
                status=RunStatus.SUCCEEDED,
                output_json={},
            )
            session.add(run)
            session.flush()
            segment = TargetSegment(
                id=stable_id("target-segment", run.id, 1),
                chapter_id=chapter_ids[1],
                translation_run_id=run.id,
                ordinal=1,
                text_zh="一段过时的整段译文",
                segment_type="sentence",
                confidence=0.5,
                final_status=TargetSegmentStatus.DRAFT,
            )
            session.add(segment)
            session.flush()
            session.add(AlignmentEdge(id=stable_id("alignment-edge", sentence, segment.id), sentence_id=sentence, target_segment_id=segment.id, relation_type="1:1", confidence=0.5, created_by="system"))
            session.flush()
            self.assertEqual(active_target_texts(session, [sentence])[sentence], before)


if __name__ == "__main__":
    unittest.main()
