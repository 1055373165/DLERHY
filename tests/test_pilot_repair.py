"""The pilot repair loop on a pilot with one known problem injected at a time (services/pilot_repair.py).

docs/agent-upgrade/08-pilot-quality-gate.md §7, §11 Q5: "在注入已知问题的样本上，自动修复并通过复检；修不掉的进入收件箱".
Each test breaks the bench's pilot (tests/pilot_repair_fixture.py) the way one of the two real
books was broken, runs the loop, and checks that the same QA passes afterwards, that the repair
chose the right tool, and that only the packets the problem touched were retranslated. The rest:
a repair that makes another check worse is rolled back, a problem no path fixes becomes one inbox
question, and the budget stops the loop.
"""

from __future__ import annotations

import unittest

from sqlalchemy import select

from book_agent.domain.enums import ApprovalStatus, LockLevel
from book_agent.domain.models import Block
from book_agent.domain.models.agent import Approval
from book_agent.domain.models.review import PilotDefectTicket, PilotQualityReport, PilotRepairAttempt
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.infra.repositories.review import active_target_texts
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.pilot_quality import PilotQualityService
from book_agent.services.pilot_repair import PilotRepairService, repair_payload
from book_agent.services.structure_edits import StructureEditService
from tests.pilot_repair_fixture import (
    FakeZhWorker,
    PilotBench,
    block_with_text,
    edit_translation,
    fold_into_previous,
    packet_of,
    sentence_ids,
)


def _findings(layers, check_id: str | None = None) -> list[dict]:
    return [
        finding
        for layer in layers
        for finding in layer["findings"]
        if check_id is None or finding["check_id"] == check_id
    ]


class PilotRepairBenchCase(unittest.TestCase):
    worker: FakeZhWorker

    def setUp(self) -> None:
        self.worker = FakeZhWorker()
        self.bench = PilotBench(self.worker)
        self.addCleanup(self.bench.close)

    def translate(self, *, all_chapters: bool = False) -> list[str]:
        if all_chapters:
            from book_agent.domain.models.translation import TranslationPacket

            with self.bench.session_factory() as session:
                workflow = self.bench.workflow(session)
                for packet_id in session.scalars(select(TranslationPacket.id)).all():
                    workflow.translation_service.execute_packet(packet_id)
                session.commit()
            with self.bench.session_factory() as session:
                return self.bench.pilot_chapter_ids(session)
        chapter_ids = self.bench.translate_pilot()
        self.bench.export(chapter_ids)
        return chapter_ids

    def qa(self, chapter_ids: list[str] | None = None) -> list[dict]:
        with self.bench.session_factory() as session:
            chapter_ids = chapter_ids or self.bench.pilot_chapter_ids(session)
            layers, _source = PilotQualityService(session).evaluate(self.bench.document_id, chapter_ids, use_export_file=False)
            return layers

    def repair(self, **kwargs):
        with self.bench.session_factory() as session:
            service = PilotRepairService(session, workflow_factory=self.bench.workflow, **kwargs)
            repair = service.run(self.bench.document_id)
            payload = repair_payload(session, repair)
            final = session.get(PilotQualityReport, repair.after_report_id) if repair.after_report_id else None
            return payload, (list(final.layers_json) if final is not None else [])

    def attempts(self, payload: dict, outcome: str | None = None) -> list[dict]:
        return [attempt for attempt in payload["attempts"] if outcome is None or attempt["outcome"] == outcome]


class InjectedIssuesAreRepairedTests(PilotRepairBenchCase):
    def test_a_clean_pilot_needs_no_repair(self) -> None:
        self.translate()
        self.assertEqual(_findings(self.qa()), [])
        payload, _final = self.repair()
        self.assertEqual(payload["outcome"], "nothing_to_do")
        self.assertEqual(payload["attempts"], [])

    def test_a_folded_chapter_is_split_out_again_by_the_outline(self) -> None:
        self.translate(all_chapters=True)
        with self.bench.session_factory() as session:
            chapter_two = self.bench.chapters(session)[1]
            fold_into_previous(session, chapter_two.id)
            session.commit()
        before = self.qa()
        self.assertTrue(_findings(before, "L1.chapter_folded"))
        self.assertTrue(_findings(before, "L1.chapters_vs_outline"))

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        self.assertEqual(kept[0]["tool"], "resplit_chapters")
        self.assertEqual(kept[0]["classification"], "systemic")
        self.assertEqual(kept[0]["classification_rule"], 3)
        # Everything was translated before the fold: the split retranslates nothing.
        self.assertEqual(kept[0]["packet_ids"], [])
        self.assertEqual(_findings(final, "L1.chapter_folded"), [])
        self.assertEqual(_findings(final, "L1.chapters_vs_outline"), [])
        with self.bench.session_factory() as session:
            chapters = self.bench.chapters(session)
            self.assertEqual([chapter.title_src for chapter in chapters], ["1 Why retrieval", "2 Designing the system", "3 Running it in production"])
            # The new chapter is the one the outline has, with its own blocks and a Chinese title.
            new = chapters[1]
            self.assertTrue(new.title_tgt)
            first = session.scalars(select(Block).where(Block.chapter_id == new.id).order_by(Block.ordinal)).first()
            self.assertEqual(first.source_text, "2 Designing the system")
            # A defect ticket for the dev side, with evidence.
            ticket = session.scalars(select(PilotDefectTicket).where(PilotDefectTicket.check_id.like("L1.%"))).first()
            self.assertIsNotNone(ticket)
            self.assertEqual(ticket.component, "parser")
            self.assertTrue(ticket.evidence_json)

    def test_a_caption_that_swallowed_the_body_is_split(self) -> None:
        self.translate()
        with self.bench.session_factory() as session:
            caption = block_with_text(session, self.bench.document_id, "Figure 1.4 Two token")
            edits = StructureEditService(session)
            packets: list[str] = []
            for text in ("Figure 1.4 shows the two", "The first technique reads", "The second technique masks"):
                body = block_with_text(session, self.bench.document_id, text)
                for outcome in (
                    edits.relabel_block(self.bench.document_id, body.id, "caption", actor_id="test", reason="inject"),
                    edits.merge_blocks(self.bench.document_id, caption.id, body.id, actor_id="test", reason="inject"),
                ):
                    packets.extend(outcome.fork.retranslate_packet_ids if outcome.fork else [])
            workflow = self.bench.workflow(session)
            for packet_id in dict.fromkeys(packets):
                workflow.translation_service.execute_packet(packet_id)
            session.commit()
        self.worker.translated_once.clear()
        self.assertTrue(_findings(self.qa(), "L2.caption_swallows_body"))

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        self.assertEqual([(a["tool"], a["path"]) for a in kept][:1], [("structure_edit", "split_caption")])
        self.assertEqual(_findings(final, "L2.caption_swallows_body"), [])
        self.assertEqual([f for f in _findings(final) if f["severity"] == "blocking"], [])
        with self.bench.session_factory() as session:
            caption = block_with_text(session, self.bench.document_id, "Figure 1.4 Two token")
            self.assertEqual(" ".join(caption.source_text.split()), "Figure 1.4 Two token prediction techniques.")

    def test_a_figure_crop_into_the_header_band_is_fitted_to_the_figure(self) -> None:
        self.translate()
        with self.bench.session_factory() as session:
            figure = block_with_text(session, self.bench.document_id, "[Figure]")
            span = dict(figure.source_span_json)
            regions = [dict(region) for region in span["source_bbox_json"]["regions"]]
            original = list(regions[0]["bbox"])
            regions[0]["bbox"] = [original[0], 20.0, original[2], original[3]]
            span["source_bbox_json"] = {**span["source_bbox_json"], "regions": regions}
            figure.source_span_json = span
            session.commit()
        crops = _findings(self.qa(), "L2.figure_regions")
        self.assertTrue(crops)
        self.assertIn("page_furniture", crops[0]["evidence"]["detail"])

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        self.assertEqual(kept[0]["tool"], "figure_region_override")
        self.assertEqual(kept[0]["packet_ids"], [])
        self.assertEqual(_findings(final, "L2.figure_regions"), [])
        with self.bench.session_factory() as session:
            figure = block_with_text(session, self.bench.document_id, "[Figure]")
            bbox = figure.source_span_json["source_bbox_json"]["regions"][0]["bbox"]
            self.assertGreater(bbox[1], 36.0)  # below the running header
            self.assertEqual(figure.source_span_json["figure_region_override"]["previous_bbox"][1], 20.0)

    def test_a_term_rendered_two_ways_is_settled_and_only_its_packet_retranslated(self) -> None:
        with self.bench.session_factory() as session:
            GlossaryService(session).lock_term(
                self.bench.document_id, "LLM", "大语言模型", target_variants=["大型语言模型"], lock_level=LockLevel.PREFERRED
            )
            (variant,) = sentence_ids(session, self.bench.document_id, "Retrieval gives the LLM facts")
            session.commit()
        self.worker.variant_sentences.add(variant)
        self.translate()
        variants = _findings(self.qa(), "L5.term_variants")
        self.assertEqual(len(variants), 1)
        with self.bench.session_factory() as session:
            variant_packet = packet_of(session, variant)

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        self.assertEqual((kept[0]["tool"], kept[0]["path"]), ("glossary", "lock_majority_and_retranslate"))
        # Only the packet with the minority rendering was retranslated.
        self.assertEqual(kept[0]["packet_ids"], [variant_packet])
        self.assertEqual([call["packet_id"] for call in self.worker.calls if call["retranslation"]], [variant_packet])
        self.assertTrue(any("大语言模型" in hint for call in self.worker.calls for hint in call["hints"]))
        self.assertEqual(_findings(final, "L5.term_variants"), [])
        with self.bench.session_factory() as session:
            entry = next(e for e in GlossaryService(session).list_document_entries(self.bench.document_id) if e.source_term == "LLM")
            self.assertEqual((entry.target_term, list(entry.target_variants_json or [])), ("大语言模型", []))
            self.assertIn("大语言模型", active_target_texts(session, [variant])[variant])

    def test_a_locked_term_violation_is_retranslated(self) -> None:
        with self.bench.session_factory() as session:
            GlossaryService(session).lock_term(self.bench.document_id, "LLM", "大语言模型", lock_level=LockLevel.LOCKED)
            (variant,) = sentence_ids(session, self.bench.document_id, "The design of a retrieval system")
            session.commit()
        self.worker.variant_sentences.add(variant)
        self.translate()
        self.assertTrue(_findings(self.qa(), "L5.locked_term"))
        with self.bench.session_factory() as session:
            variant_packet = packet_of(session, variant)

        payload, final = self.repair()

        # The glossary has no other rendering to swap in: the loop falls through to retranslation.
        paths = [(a["path"], a["outcome"]) for a in payload["attempts"]]
        self.assertEqual(paths[:2], [("use_locked_rendering", "skipped"), ("with_hints", "kept")])
        self.assertEqual(self.attempts(payload, "kept")[0]["packet_ids"], [variant_packet])
        self.assertEqual(_findings(final, "L5.locked_term"), [])

    def test_a_leaked_program_note_and_missing_spaces_are_fixed_without_a_model(self) -> None:
        with self.bench.session_factory() as session:
            (leak,) = sentence_ids(session, self.bench.document_id, "The first technique reads")
            # One paragraph against the book's convention (with more, the book "mixes" and it is minor).
            unspaced = sentence_ids(session, self.bench.document_id, "3 simple parts")
            session.commit()
        self.worker.leak_sentences.add(leak)
        self.worker.unspaced_sentences.update(unspaced)
        self.translate()
        before = self.qa()
        self.assertTrue(_findings(before, "L7.leaked_marker"))
        self.assertTrue(_findings(before, "L7.cjk_latin_spacing"))

        payload, final = self.repair()

        kept = {(a["tool"], a["path"]) for a in self.attempts(payload, "kept")}
        self.assertIn(("segment_edit", "strip_leaked_markers"), kept)
        self.assertIn(("segment_edit", "cjk_latin_spacing"), kept)
        # Deterministic fixes: no model call, no retranslated packet.
        self.assertFalse(any(call["retranslation"] for call in self.worker.calls))
        self.assertTrue(all(a["packet_ids"] == [] for a in payload["attempts"]))
        self.assertEqual(_findings(final, "L7.leaked_marker"), [])
        self.assertEqual(_findings(final, "L7.cjk_latin_spacing"), [])
        with self.bench.session_factory() as session:
            text = active_target_texts(session, [leak])[leak]
            self.assertNotIn("图片锚点保留", text)
            # The book's convention goes into BOOK.md for the whole-book run.
            from book_agent.harness.context.book_md import book_prompt_guidance

            self.assertIn("中英文之间加一个空格", book_prompt_guidance(session, self.bench.document_id))

    def test_an_untranslated_sentence_retranslates_only_its_packet(self) -> None:
        with self.bench.session_factory() as session:
            (omitted,) = sentence_ids(session, self.bench.document_id, "Teams track the score")
            session.commit()
        self.worker.omit_sentences.add(omitted)
        self.translate()
        self.assertTrue(_findings(self.qa(), "L6.untranslated_sentence"))
        with self.bench.session_factory() as session:
            omitted_packet = packet_of(session, omitted)

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        self.assertEqual([(a["tool"], a["packet_ids"]) for a in kept], [("retranslate_packets", [omitted_packet])])
        self.assertEqual([call["packet_id"] for call in self.worker.calls if call["retranslation"]], [omitted_packet])
        self.assertEqual(_findings(final, "L6.untranslated_sentence"), [])
        self.assertEqual(payload["outcome"], "passed")
        self.assertEqual(payload["repaired"], 1)
        self.assertEqual(payload["questions"], [])


class BoundsAndEscalationTests(PilotRepairBenchCase):
    def test_a_repair_that_makes_another_check_worse_is_reverted_and_the_issue_goes_to_the_inbox(self) -> None:
        with self.bench.session_factory() as session:
            (omitted,) = sentence_ids(session, self.bench.document_id, "Teams track the score")
            session.commit()
        self.worker.omit_sentences.add(omitted)
        # Every retranslation leaks a program note: it fixes the missing sentence and breaks L7.
        self.worker.retranslation_leaks = True
        self.translate()

        payload, final = self.repair()

        (attempt,) = payload["attempts"]
        self.assertEqual(attempt["outcome"], "reverted")
        self.assertTrue(attempt["reason"].startswith("worse:"), attempt["reason"])
        self.assertIn("L7.leaked_marker", attempt["reason"])
        # The retranslation was paid for and rolled back: the text is what it was.
        with self.bench.session_factory() as session:
            self.assertEqual(active_target_texts(session, [omitted]).get(omitted, ""), "")
            questions = session.scalars(select(Approval).where(Approval.kind == "pilot_question")).all()
            self.assertEqual(len(questions), 1)
            question = questions[0]
            self.assertEqual(question.status, ApprovalStatus.PENDING)
            self.assertEqual(question.payload_json["check_id"], "L6.untranslated_sentence")
            self.assertIn("？", question.payload_json["question"])
            self.assertTrue(question.payload_json["evidence"])
            self.assertTrue(question.payload_json["images"])  # the page, with the problem outlined
        self.assertEqual(payload["outcome"], "escalated")
        self.assertEqual(len(_findings(final, "L6.untranslated_sentence")), 1)

    def test_a_problem_no_path_fixes_becomes_one_question_per_issue_class(self) -> None:
        self.translate()
        with self.bench.session_factory() as session:
            for text in ("The first technique reads", "The second technique masks"):
                (sentence,) = sentence_ids(session, self.bench.document_id, text)
                edit_translation(session, sentence, lambda current: current.replace("这一句", "这¡句"))
            session.commit()
        self.assertEqual(len(_findings(self.qa(), "L7.stray_glyph")), 2)

        payload, _final = self.repair()

        self.assertEqual(payload["attempts"], [])
        self.assertEqual(len(payload["questions"]), 1)
        self.assertEqual(payload["questions"][0]["check_id"], "L7.stray_glyph")
        self.assertEqual(payload["questions"][0]["finding_count"], 2)
        # Systemic (a generic rule): a ticket for the dev side.
        (ticket,) = [ticket for ticket in payload["tickets"] if ticket["check_id"] == "L7.stray_glyph"]
        self.assertEqual(ticket["classification_rule"], 3)
        self.assertEqual(ticket["component"], "parser")
        # Asked again after another repair: the same question is updated, not repeated.
        payload, _final = self.repair()
        with self.bench.session_factory() as session:
            self.assertEqual(len(session.scalars(select(Approval).where(Approval.kind == "pilot_question")).all()), 1)
        # Once the problem is gone, the question is withdrawn.
        with self.bench.session_factory() as session:
            for text in ("The first technique reads", "The second technique masks"):
                (sentence,) = sentence_ids(session, self.bench.document_id, text)
                edit_translation(session, sentence, lambda current: current.replace("¡", "一"))
            session.commit()
        payload, _final = self.repair()
        self.assertEqual(payload["questions"], [])
        with self.bench.session_factory() as session:
            (question,) = session.scalars(select(Approval).where(Approval.kind == "pilot_question")).all()
            self.assertEqual(question.status, ApprovalStatus.EXPIRED)

    def test_the_budget_stops_the_loop(self) -> None:
        self.worker.cost_per_call = 0.01
        with self.bench.session_factory() as session:
            omitted = [
                sentence_ids(session, self.bench.document_id, text)[0]
                for text in ("Teams track the score", "The first technique reads", "The pipeline runs once")
            ]
            session.commit()
        self.worker.omit_sentences.update(omitted)
        self.translate()

        payload, _final = self.repair(budget_usd=0.015)

        self.assertEqual(payload["stop_reason"], "budget")
        retranslated = [call for call in self.worker.calls if call["retranslation"]]
        self.assertEqual(len(retranslated), 1)
        self.assertEqual(self.attempts(payload, "skipped")[-1]["reason"].split(":")[0], "budget")
        self.assertAlmostEqual(payload["cost_usd"], 0.01, places=4)


    def test_a_pilot_that_reused_translations_budgets_from_the_estimate(self) -> None:
        from unittest.mock import patch

        self.translate()
        with patch.object(PilotRepairService, "_estimated_pilot_cost", return_value=0.05):
            payload, _final = self.repair()
        # The bench's calls cost nothing; the estimate for these chapters sets the budget (×2).
        self.assertAlmostEqual(payload["budget_usd"], 0.1, places=6)


class RunInHeadingAndHeaderTests(PilotRepairBenchCase):
    """The Agent book's run-in subheadings and the RAG book's running headers."""

    def _replace_text(self, contains: str, new_text: str) -> None:
        from book_agent.services.parse_revision_fork import ParseRevisionForkService

        with self.bench.session_factory() as session:
            block = block_with_text(session, self.bench.document_id, contains)
            block.source_text = new_text
            block.normalized_text = " ".join(new_text.split())
            session.flush()
            fork = ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[block.id], reason="inject")
            workflow = self.bench.workflow(session)
            for packet_id in fork.retranslate_packet_ids:
                workflow.translation_service.execute_packet(packet_id)
            session.commit()
        # The injected text is the parse, not a repair: translate it as the first translation.
        self.worker.translated_once.clear()

    def test_a_run_in_heading_glued_to_its_paragraph_is_split_out(self) -> None:
        self.translate()
        self._replace_text(
            "The first technique reads",
            "The first technique reads the context from left to right.masking tokens The second technique masks a token in the middle.",
        )
        self.assertTrue(_findings(self.qa(), "L1.heading_merged_into_text"))

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        self.assertEqual((kept[0]["tool"], kept[0]["path"]), ("structure_edit", "split_run_in_heading"))
        self.assertEqual(kept[0]["params"]["heading"], "masking tokens")
        self.assertEqual(_findings(final, "L1.heading_merged_into_text"), [])
        with self.bench.session_factory() as session:
            heading = session.scalars(select(Block).where(Block.source_text == "masking tokens")).one()
            self.assertEqual(heading.block_type.value, "heading")

    def test_run_in_heading_lines_the_parser_kept_in_the_paragraph_are_split_out(self) -> None:
        """The Agent book's form: the heading is a line of the paragraph, the reading copy glues it on."""
        self.translate()
        self._replace_text(
            "The first technique reads",
            "The first technique reads the context from left to right.\nmasking tokens\nThe second technique masks a token.\n"
            "It predicts the token from both sides.\nwhy it helps\nBoth sides give more context.",
        )
        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        self.assertEqual((kept[0]["path"], kept[0]["params"]["headings"]), ("split_run_in_heading", ["masking tokens", "why it helps"]))
        self.assertEqual(_findings(final, "L1.heading_merged_into_text"), [])
        with self.bench.session_factory() as session:
            for heading in ("masking tokens", "why it helps"):
                self.assertEqual(session.scalars(select(Block).where(Block.source_text == heading)).one().block_type.value, "heading")

    def test_a_running_header_kept_as_text_is_hidden(self) -> None:
        self.translate()
        self._replace_text(
            "An indexing pipeline splits",
            "3 Why retrieval An indexing pipeline splits documents into chunks and stores each chunk with its embedding vector.",
        )
        self.assertTrue(_findings(self.qa(), "L1.running_header_as_text"))

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        self.assertEqual((kept[0]["tool"], kept[0]["path"]), ("structure_edit", "hide_running_header"))
        self.assertTrue(kept[0]["params"]["split"])
        self.assertEqual(_findings(final, "L1.running_header_as_text"), [])
        with self.bench.session_factory() as session:
            header = session.scalars(select(Block).where(Block.source_text == "3 Why retrieval")).one()
            self.assertTrue(header.source_span_json["repair_hidden_from_export"])


class _ScriptedModel:
    """An agent model that proposes one split, then stops."""

    def __init__(self, proposal: dict) -> None:
        self.proposal = proposal
        self.steps = 0

    def step(self, *, model_name, messages, tools):
        self.steps += 1
        if self.steps == 1:
            return AgentStep(text=None, tool_calls=[ToolCall(call_id="c1", name="propose_structure_edit", arguments=self.proposal)])
        return AgentStep(text="已提出拆分。", tool_calls=[])


class RepairAgentTests(PilotRepairBenchCase):
    def test_the_agent_places_a_split_the_rules_cannot(self) -> None:
        from book_agent.harness.agents.pilot_repair import AgentStructureJudge

        self.translate()
        # A caption that is one long "sentence" (no sentence break the rules can split at).
        long_caption = "Figure 1.4 Two token prediction techniques " + " ".join(["and"] + ["more words about the model"] * 16)
        with self.bench.session_factory() as session:
            caption = block_with_text(session, self.bench.document_id, "Figure 1.4 Two token")
            caption_id = caption.id
        self._set_caption(caption_id, long_caption)
        self.assertTrue(_findings(self.qa(), "L2.caption_swallows_body"))
        model = _ScriptedModel(
            {"kind": "split", "block_id": caption_id, "second_part_starts_with": "and more words about", "second_part_type": "paragraph", "reason": "图注后是正文"}
        )
        judge = AgentStructureJudge(session_factory=self.bench.session_factory, model=model, model_name="scripted")

        payload, final = self.repair(judge=judge)

        kept = self.attempts(payload, "kept")
        self.assertEqual((kept[0]["path"], kept[0]["decided_by"]), ("agent_proposal", "agent"))
        self.assertEqual(_findings(final, "L2.caption_swallows_body"), [])
        with self.bench.session_factory() as session:
            from book_agent.domain.models.agent import AgentTurn

            (turn,) = session.scalars(select(AgentTurn).where(AgentTurn.agent_kind == "pilot_repair")).all()
            self.assertEqual(turn.status.value, "succeeded")
            self.assertEqual(session.scalars(select(Approval)).all(), [])  # never waits on a person

    def _set_caption(self, block_id: str, text: str) -> None:
        from book_agent.services.parse_revision_fork import ParseRevisionForkService

        with self.bench.session_factory() as session:
            block = session.get(Block, block_id)
            block.source_text = text
            block.normalized_text = text
            session.flush()
            fork = ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[block.id], reason="inject")
            workflow = self.bench.workflow(session)
            for packet_id in fork.retranslate_packet_ids:
                workflow.translation_service.execute_packet(packet_id)
            session.commit()
        self.worker.translated_once.clear()


class AttemptRecordTests(PilotRepairBenchCase):
    def _open_review_issue(self, chapter_id: str, issue_type: str, *, detector: str, evidence: dict) -> str:
        from datetime import datetime, timezone

        from book_agent.domain.enums import Detector, IssueStatus, RootCauseLayer, Severity
        from book_agent.domain.models.review import ReviewIssue

        with self.bench.session_factory() as session:
            now = datetime.now(timezone.utc)
            issue = ReviewIssue(
                document_id=self.bench.document_id,
                chapter_id=chapter_id,
                issue_type=issue_type,
                root_cause_layer=RootCauseLayer.STRUCTURE,
                severity=Severity.CRITICAL,
                blocking=True,
                detector=Detector(detector),
                evidence_json=evidence,
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            session.add(issue)
            session.commit()
            return issue.id

    def test_a_stale_blocking_review_issue_is_a_finding_and_its_recheck_resolves_it(self) -> None:
        # The RAG book: a MISORDERING the review pass opened under the old blanket policy.
        from book_agent.domain.enums import IssueStatus
        from book_agent.domain.models.review import ReviewIssue

        chapter_ids = self.translate()
        issue_id = self._open_review_issue(
            chapter_ids[0],
            "MISORDERING",
            detector="rule",
            evidence={"layout_risk": "high", "review_policy": "default_blocking_layout_risk", "page_layout_risk_pages": []},
        )
        (before,) = _findings(self.qa(), "L1.review_blocking")
        self.assertEqual((before["severity"], before["key"]), ("blocking", f"L1.review_blocking:issue:{issue_id}"))

        payload, final = self.repair()

        (kept,) = self.attempts(payload, "kept")
        self.assertEqual((kept["tool"], kept["path"]), ("review_recheck", "rerun_detectors"))
        self.assertEqual(_findings(final, "L1.review_blocking"), [])
        self.assertEqual(payload["questions"], [])
        with self.bench.session_factory() as session:
            self.assertEqual(session.get(ReviewIssue, issue_id).status, IssueStatus.RESOLVED)

    def test_a_blocking_issue_no_detector_owns_goes_to_the_reader(self) -> None:
        chapter_ids = self.translate()
        self._open_review_issue(chapter_ids[0], "MISORDERING", detector="human", evidence={"page_layout_risk_pages": [2]})

        payload, final = self.repair()

        self.assertEqual(self.attempts(payload, "kept"), [])
        self.assertEqual(len(_findings(final, "L1.review_blocking")), 1)
        (question,) = payload["questions"]
        self.assertEqual(question["check_id"], "L1.review_blocking")

    def test_every_attempt_is_recorded_for_the_ledger(self) -> None:
        with self.bench.session_factory() as session:
            (omitted,) = sentence_ids(session, self.bench.document_id, "Teams track the score")
            session.commit()
        self.worker.omit_sentences.add(omitted)
        self.translate()
        payload, _final = self.repair()
        with self.bench.session_factory() as session:
            (row,) = session.scalars(select(PilotRepairAttempt)).all()
            self.assertEqual(row.document_id, self.bench.document_id)
            self.assertEqual(row.finding_keys_json, [f"L6.untranslated_sentence:sentence:{omitted}"])
            self.assertEqual((row.classification, row.classification_rule), ("book_specific", 4))
            self.assertEqual(row.tool, "retranslate_packets")
            self.assertEqual(row.outcome, "kept")
            self.assertEqual(row.before_json["counts"]["blocking"], 1)
            self.assertEqual(row.after_json["counts"]["blocking"], 0)
            self.assertEqual(row.resolved_keys_json, row.finding_keys_json)
            self.assertEqual(row.token_in, 1000)



class ReaderVisibleStructureTests(PilotRepairBenchCase):
    """Section headings set as paragraphs and a figure's text kept as a table (both Manning pilots)."""

    def _style(self, contains: str, *, text: str | None = None, fonts: list[str], block_type: str | None = None, level: int | None = None) -> str:
        """Give a block the fonts it is set in (and new text, or a heading type), as the parse would have."""
        from book_agent.domain.enums import BlockType
        from book_agent.services.parse_revision_fork import ParseRevisionForkService

        with self.bench.session_factory() as session:
            block = block_with_text(session, self.bench.document_id, contains)
            if text is not None:
                block.source_text = text
                block.normalized_text = text
            span = dict(block.source_span_json or {})
            span["pdf_font_names"] = fonts
            if level is not None:
                span["heading_level"] = level
            block.source_span_json = span
            if block_type is not None:
                block.block_type = BlockType(block_type)
            session.flush()
            if text is not None:
                fork = ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[block.id], reason="inject")
                workflow = self.bench.workflow(session)
                for packet_id in fork.retranslate_packet_ids:
                    workflow.translation_service.execute_packet(packet_id)
            session.commit()
            block_id = block.id
        self.worker.translated_once.clear()
        return block_id

    def test_a_section_heading_kept_as_a_paragraph_becomes_a_heading(self) -> None:
        self.translate()
        # Chapter 1 as Manning sets it: numbered headings in the heading face, body text in the body face.
        self._style("An LLM answers from", text="1.1 Why facts go stale", fonts=["Heading-Dem"], block_type="heading", level=2)
        self._style("Retrieval gives the LLM facts", text="1.2 How retrieval helps", fonts=["Heading-Dem"], block_type="heading", level=2)
        self._style("This chapter explains why", fonts=["Body-Roman"])
        subsection = self._style("The first technique reads", text="1.2.1 What a retriever returns", fonts=["Heading-Dem"])
        found = _findings(self.qa(), "L1.heading_as_text")
        self.assertEqual([(f["target"]["id"], f["evidence"]["heading_level"]) for f in found], [(subsection, 3)])

        payload, final = self.repair()

        kept = [attempt for attempt in self.attempts(payload, "kept") if attempt["check_id"] == "L1.heading_as_text"]
        self.assertEqual((kept[0]["tool"], kept[0]["path"]), ("structure_edit", "relabel_as_heading"))
        self.assertEqual(_findings(final, "L1.heading_as_text"), [])
        with self.bench.session_factory() as session:
            block = session.get(Block, subsection)
            self.assertEqual((block.block_type.value, block.source_span_json.get("heading_level")), ("heading", 3))

    def test_figure_text_kept_as_a_table_is_hidden(self) -> None:
        self.translate()
        with self.bench.session_factory() as session:
            figure = next(
                block
                for block in session.scalars(select(Block).where(Block.source_text.in_(["[Figure]", "[Image]"]))).all()
                if (block.source_span_json or {}).get("source_page_start") == 2
            )
            (left, top, right, bottom) = figure.source_span_json["source_bbox_json"]["regions"][0]["bbox"]
        inside = [left + 4.0, top + 4.0, left + 60.0, top + 24.0]
        table = self._style("The second technique masks", text="| The | teacher |\n| The | teacher |", fonts=["ArialMT"], block_type="table")
        with self.bench.session_factory() as session:
            block = session.get(Block, table)
            block.source_span_json = {
                **block.source_span_json,
                "source_bbox_json": {"regions": [{"page_number": 2, "bbox": inside}]},
            }
            session.commit()
        found = _findings(self.qa(), "L2.figure_residue")
        self.assertEqual([(f["target"]["id"], f["severity"]) for f in found], [(table, "blocking")])

        payload, final = self.repair()

        kept = [attempt for attempt in self.attempts(payload, "kept") if attempt["check_id"] == "L2.figure_residue"]
        self.assertEqual((kept[0]["tool"], kept[0]["path"]), ("structure_edit", "hide_figure_residue"))
        self.assertEqual(_findings(final, "L2.figure_residue"), [])
        with self.bench.session_factory() as session:
            self.assertTrue(session.get(Block, table).source_span_json.get("repair_hidden_from_export"))


if __name__ == "__main__":
    unittest.main()
