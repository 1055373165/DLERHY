"""A callout a parse left in pieces (the icon an image block, each line a paragraph) is found by the
pilot QA and merged back into one callout block by the pilot repair, without a re-import.

The Agent book was parsed before callouts were recognised: its "QS" prompt icon became a figure
card and the prompt's lines loose body paragraphs (p.52, 53, 57). ``L1.icon_callout_split`` finds
them by geometry and fonts, ``L2.tiny_icon_figure`` finds the icon drawn as a figure, and the
``structure_edit.merge_icon_callout`` path merges them and retranslates only the callout's packets.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from sqlalchemy import select

from book_agent.domain.enums import ArtifactStatus, BlockType
from book_agent.domain.models import Block, Sentence, StructureEdit
from book_agent.domain.quality import pilot_qa
from book_agent.domain.quality.pilot_qa import QaBlock
from book_agent.services.parse_revision_fork import ParseRevisionForkService
from book_agent.services.structure_edits import StructureEditService
from tests.pilot_repair_fixture import block_with_text
from tests.test_pilot_repair import PilotRepairBenchCase, _findings

BODY_FONT = ("Times-Roman",)
SANS = ("Helvetica",)


def _qa_block(block_id: str, block_type: str, text: str, fonts: tuple[str, ...], bbox, *, html: str = "", page: int = 52) -> QaBlock:
    return QaBlock(
        block_id=block_id,
        chapter_id="c1",
        block_type=block_type,
        render_mode="source_artifact_full_width" if block_type == "image" else "zh_primary_with_optional_source",
        artifact_kind="image" if block_type == "image" else None,
        source_text=text,
        target_text="",
        html=html,
        page=page,
        font_names=fonts,
        regions=((page, tuple(float(value) for value in bbox)),),
    )


def _p52() -> list[QaBlock]:
    """p.52 of the Agent book as it was parsed: body, the QS icon, two prompt lines, body."""
    body = "What happens when you ask a typical chatbot about the weather? It asks whether to search. " * 3
    return [
        _qa_block("body-1", "paragraph", body, BODY_FONT, (102, 271, 477, 458)),
        _qa_block("icon", "image", "[Image]", (), (102.4, 468.3, 118.0, 483.5), html="<figure class='artifact-figure'><img/></figure>"),
        _qa_block("line-1", "paragraph", "When the user's intent is clear, execute immediately without confirmation.", SANS, (118.0, 469.8, 432.3, 480.9)),
        _qa_block("line-2", "paragraph", "Only when intent is unclear, ask minimal questions to clarify.", SANS, (126.4, 484.7, 376.8, 495.7)),
        _qa_block("body-2", "paragraph", "The last line is important. " * 4, BODY_FONT, (102, 509, 477, 547)),
    ]


class CalloutQaTests(unittest.TestCase):
    def test_split_callout_and_icon_card_are_found(self) -> None:
        split, tiny = pilot_qa.check_icon_callouts(SimpleNamespace(blocks=_p52()))
        self.assertEqual(len(split.findings), 1)
        finding = split.findings[0]
        self.assertEqual(finding.location.block_id, "icon")
        self.assertEqual(finding.evidence["line_block_ids"], ["line-1", "line-2"])
        self.assertEqual(finding.evidence["callout_kind"], "prose")
        self.assertEqual(len(tiny.findings), 1)
        self.assertTrue(tiny.findings[0].evidence["leads_callout"])
        self.assertIn("pilot_qa", pilot_qa.__name__)
        self.assertIn("L1.icon_callout_split", pilot_qa.CHECKS)
        self.assertIn("L2.tiny_icon_figure", pilot_qa.CHECKS)

    def test_icon_inline_with_body_text_is_not_a_callout(self) -> None:
        blocks = _p52()
        # The lines in the body face: an inline picture, not a callout.
        blocks[2] = _qa_block("line-1", "paragraph", blocks[2].source_text, BODY_FONT, (118.0, 469.8, 432.3, 480.9))
        split, _tiny = pilot_qa.check_icon_callouts(SimpleNamespace(blocks=blocks))
        self.assertEqual(split.findings, [])

    def test_lines_not_beside_the_icon_are_not_a_callout(self) -> None:
        blocks = _p52()
        blocks[2] = _qa_block("line-1", "paragraph", blocks[2].source_text, SANS, (102.0, 500.0, 432.3, 511.0))
        split, _tiny = pilot_qa.check_icon_callouts(SimpleNamespace(blocks=blocks))
        self.assertEqual(split.findings, [])

    def test_a_json_callout_is_code(self) -> None:
        blocks = _p52()
        blocks[2] = _qa_block("line-1", "paragraph", "{", SANS, (118.0, 469.8, 125.0, 480.9))
        blocks[3] = _qa_block("line-2", "paragraph", "“query”: “The question”,", SANS, (126.4, 484.7, 376.8, 495.7))
        blocks.insert(4, _qa_block("line-3", "paragraph", "}", SANS, (126.4, 499.0, 130.0, 510.0)))
        split, _tiny = pilot_qa.check_icon_callouts(SimpleNamespace(blocks=blocks))
        self.assertEqual(split.findings[0].evidence["callout_kind"], "code")


class CalloutRepairTests(PilotRepairBenchCase):
    """On the bench: page 2's first paragraph becomes a QS icon, the next two its prompt lines."""

    def _split_callout(self) -> tuple[str, str, str]:
        with self.bench.session_factory() as session:
            icon = block_with_text(session, self.bench.document_id, "Figure 1.4 shows")
            first = block_with_text(session, self.bench.document_id, "The first technique reads")
            second = block_with_text(session, self.bench.document_id, "The second technique masks")
            top = 350.0
            icon.block_type = BlockType.IMAGE
            icon.source_text = "[Image]"
            icon.normalized_text = "[Image]"
            icon.source_span_json = {
                **(icon.source_span_json or {}),
                "pdf_block_role": "image",
                "translatable": False,
                "source_bbox_json": {"regions": [{"page_number": 2, "bbox": [102.0, top, 118.0, top + 15]}]},
            }
            for block, (x0, y0) in ((first, (118.0, top + 2)), (second, (126.0, top + 17))):
                block.source_span_json = {
                    **(block.source_span_json or {}),
                    "pdf_font_names": ["Helvetica"],
                    "source_bbox_json": {"regions": [{"page_number": 2, "bbox": [x0, y0, 470.0, y0 + 11]}]},
                }
            session.flush()
            ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[icon.id], reason="inject")
            session.commit()
            return icon.id, first.id, second.id

    def test_the_repair_merges_the_icon_and_its_lines_into_one_callout(self) -> None:
        self.translate()
        icon_id, first_id, second_id = self._split_callout()
        before = self.qa()
        self.assertTrue(_findings(before, "L1.icon_callout_split"))
        calls_before = len(self.worker.calls)

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        merge = next(attempt for attempt in kept if attempt["path"] == "merge_icon_callout")
        self.assertEqual(merge["tool"], "structure_edit")
        self.assertEqual(merge["params"]["line_block_ids"], [first_id, second_id])
        self.assertEqual(_findings(final, "L1.icon_callout_split"), [])
        self.assertEqual(_findings(final, "L2.tiny_icon_figure"), [])
        # Only the callout's packets were retranslated, with the line-by-line hint.
        retranslations = self.worker.calls[calls_before:]
        self.assertTrue(retranslations)
        self.assertLessEqual(len(retranslations), len(merge["packet_ids"]) + 1)
        self.assertTrue(any("每一行单独翻译" in hint for call in retranslations for hint in call["hints"]))
        with self.bench.session_factory() as session:
            callout = session.get(Block, first_id)
            self.assertEqual(callout.status, ArtifactStatus.ACTIVE)
            self.assertEqual(callout.block_type, BlockType.QUOTE)
            self.assertEqual(callout.source_span_json["pdf_block_role"], "callout")
            self.assertEqual(callout.source_span_json["callout_icon"]["page_number"], 2)
            self.assertEqual(callout.source_text.count("\n"), 1)
            self.assertEqual(session.get(Block, icon_id).status, ArtifactStatus.INVALIDATED)
            self.assertEqual(session.get(Block, second_id).status, ArtifactStatus.INVALIDATED)
            lines = session.scalars(
                select(Sentence)
                .where(Sentence.block_id == first_id, Sentence.retired_by_revision_id.is_(None))
                .order_by(Sentence.ordinal_in_block)
            ).all()
            self.assertEqual([sentence.source_text[:26] for sentence in lines], ["The first technique reads ", "The second technique masks"])
            self.assertEqual(
                session.scalars(select(StructureEdit.kind).where(StructureEdit.document_id == self.bench.document_id)).all(),
                ["merge_callout"],
            )

    def test_a_merged_callout_is_left_alone_by_replay_and_rejects_non_adjacent_lines(self) -> None:
        self.translate()
        icon_id, first_id, second_id = self._split_callout()
        with self.bench.session_factory() as session:
            service = StructureEditService(session)
            from book_agent.services.structure_edits import StructureEditRejected

            with self.assertRaises(StructureEditRejected):
                service.merge_callout(self.bench.document_id, icon_id, [second_id], actor_id="test", reason="skip a line")
            service.merge_callout(self.bench.document_id, icon_id, [first_id, second_id], actor_id="test", reason="callout")
            session.flush()
            result = service.replay(self.bench.document_id)
            self.assertEqual(result.reapplied_edit_ids, [])
            self.assertEqual(result.stale_edit_ids, [])


if __name__ == "__main__":
    unittest.main()
