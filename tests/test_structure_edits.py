"""Block-level structure edits: relabel, merge, caption links, replay after refresh, and the Structure Agent's approval-gated tools."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import AgentTurnStatus, ApprovalStatus, ArtifactStatus, BlockType
from book_agent.domain.models import Block, Sentence, StructureEdit
from book_agent.domain.models.ops import AppendOnlyViolation
from book_agent.harness.agents.structure import StructureAgent
from book_agent.harness.approvals.service import ApprovalService
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import active_target_texts
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.orchestrator.run_plan import plan_for_run
from book_agent.services.structure_edits import StructureEditRejected, StructureEditService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService
from tests.test_harness_kernel import _FakeModel, _usage
from tests.test_translation_worker_abstraction import CONTAINER_XML, CONTENT_OPF, NAV_XHTML

FIRST = "The solution to this problem is"
SECOND = "context engineering, which is a discipline."
OTHER = "A separate paragraph about agents."
JOINED = "Agents plan their work. Tools carry it out."


def _chapter(second: str = SECOND, joined: str = JOINED) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>{FIRST}</p>
    <p>{second}</p>
    <p>{OTHER}</p>
    <p>{joined}</p>
    <blockquote>Figure 1. The agent loop.</blockquote>
  </body>
</html>
"""


class StructureEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'edits.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.epub_path = Path(self.tempdir.name) / "sample.epub"
        self._write_epub(_chapter())
        artifacts = BootstrapOrchestrator().bootstrap_epub(self.epub_path)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
            service = TranslationService(TranslationRepository(session))
            for packet in artifacts.translation_packets:
                service.execute_packet(packet.id)
            session.commit()
        self.document_id = artifacts.document.id
        with self.session_factory() as session:
            blocks = session.scalars(select(Block).order_by(Block.ordinal)).all()
            self.blocks = {block.source_text: block.id for block in blocks}

    def _write_epub(self, chapter_xhtml: str) -> None:
        with zipfile.ZipFile(self.epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", chapter_xhtml)

    def _active_sentences(self, session, block_id: str) -> list[Sentence]:
        return list(
            session.scalars(
                select(Sentence)
                .where(Sentence.block_id == block_id, Sentence.retired_by_revision_id.is_(None))
                .order_by(Sentence.ordinal_in_block)
            ).all()
        )

    def _edit(self, session) -> StructureEditService:
        return StructureEditService(session)

    # --- edits ------------------------------------------------------------------------

    def test_merge_joins_the_text_invalidates_the_second_block_and_retranslates_the_joined_sentence(self) -> None:
        first_id, second_id = self.blocks[FIRST], self.blocks[SECOND]
        with self.session_factory() as session:
            outcome = self._edit(session).merge_blocks(
                self.document_id, first_id, second_id, actor_id="person:editor", reason="one sentence cut across a page"
            )
            session.commit()
            first, second = session.get(Block, first_id), session.get(Block, second_id)
            self.assertEqual(first.source_text, f"{FIRST} {SECOND}")
            self.assertEqual(second.status, ArtifactStatus.INVALIDATED)
            self.assertEqual(self._active_sentences(session, second_id), [])
            merged = self._active_sentences(session, first_id)
            self.assertEqual(len(merged), 1)
            self.assertEqual(outcome.fork.relation_counts.get("merge"), 2)
            self.assertTrue(outcome.fork.retranslate_packet_ids)
            edit = session.get(StructureEdit, outcome.edit_id)
            self.assertEqual((edit.kind, edit.status, edit.actor_id), ("merge_blocks", "applied", "person:editor"))
            self.assertEqual([item["block_id"] for item in edit.blocks_json], [first_id, second_id])
            edit.reason = "rewritten"
            with self.assertRaises(AppendOnlyViolation):
                session.flush()
            session.rollback()

    def test_merge_of_two_whole_sentences_carries_both_translations(self) -> None:
        other_id = self.blocks[OTHER]
        second_id = self.blocks[SECOND]
        with self.session_factory() as session:
            before = active_target_texts(session, [s.id for s in self._active_sentences(session, second_id) + self._active_sentences(session, other_id)])
            outcome = self._edit(session).merge_blocks(self.document_id, second_id, other_id, actor_id="t", reason="r")
            session.commit()
            merged = self._active_sentences(session, second_id)
            self.assertEqual([s.source_text for s in merged], [SECOND, OTHER])
            self.assertEqual(outcome.fork.relation_counts, {"same": 2})
            after = active_target_texts(session, [s.id for s in merged])
            self.assertEqual(sorted(after.values()), sorted(before.values()))

    def test_merge_rejections(self) -> None:
        with self.session_factory() as session:
            service = self._edit(session)
            with self.assertRaisesRegex(StructureEditRejected, "directly follow"):
                service.merge_blocks(self.document_id, self.blocks[FIRST], self.blocks[OTHER], actor_id="t", reason="r")
            with self.assertRaisesRegex(StructureEditRejected, "same type"):
                service.merge_blocks(self.document_id, self.blocks[OTHER], self.blocks["Figure 1. The agent loop."], actor_id="t", reason="r")
            with self.assertRaisesRegex(StructureEditRejected, "not in this document"):
                service.merge_blocks(self.document_id, "00000000-0000-0000-0000-000000000000", self.blocks[OTHER], actor_id="t", reason="r")

    def test_relabel_to_code_protects_the_text_and_drops_its_translation(self) -> None:
        block_id = self.blocks[OTHER]
        with self.session_factory() as session:
            outcome = self._edit(session).relabel_block(self.document_id, block_id, "code", actor_id="t", reason="monospace listing")
            session.commit()
            block = session.get(Block, block_id)
            self.assertEqual((block.block_type, block.protected_policy.value), (BlockType.CODE, "protect"))
            (sentence,) = self._active_sentences(session, block_id)
            self.assertFalse(sentence.translatable)
            self.assertEqual(active_target_texts(session, [sentence.id]), {})
            self.assertIsNotNone(outcome.fork.parse_revision_id)
            with self.assertRaisesRegex(StructureEditRejected, "already"):
                self._edit(session).relabel_block(self.document_id, block_id, "code", actor_id="t", reason="again")
            with self.assertRaisesRegex(StructureEditRejected, "cannot relabel"):
                self._edit(session).relabel_block(self.document_id, block_id, "table", actor_id="t", reason="no")

    def test_relabel_to_heading_keeps_the_sentence_and_its_translation(self) -> None:
        block_id = self.blocks[OTHER]
        with self.session_factory() as session:
            (before,) = self._active_sentences(session, block_id)
            outcome = self._edit(session).relabel_block(self.document_id, block_id, "heading", heading_level=3, actor_id="t", reason="bold title")
            session.commit()
            block = session.get(Block, block_id)
            self.assertEqual((block.block_type, block.source_span_json["heading_level"]), (BlockType.HEADING, 3))
            self.assertIsNone(outcome.fork.parse_revision_id)
            (after,) = self._active_sentences(session, block_id)
            self.assertEqual(after.id, before.id)

    def test_link_caption_moves_the_link(self) -> None:
        caption_id, code_id = self.blocks["Figure 1. The agent loop."], self.blocks[OTHER]
        with self.session_factory() as session:
            service = self._edit(session)
            with self.assertRaisesRegex(StructureEditRejected, "must be a caption"):
                service.link_caption(self.document_id, caption_id, code_id, actor_id="t", reason="r")
            service.relabel_block(self.document_id, caption_id, "caption", actor_id="t", reason="r")
            with self.assertRaisesRegex(StructureEditRejected, "captions attach to"):
                service.link_caption(self.document_id, caption_id, code_id, actor_id="t", reason="r")
            service.relabel_block(self.document_id, code_id, "code", actor_id="t", reason="r")
            service.link_caption(self.document_id, caption_id, code_id, actor_id="t", reason="r")
            session.commit()
            self.assertEqual(session.get(Block, code_id).source_span_json["linked_caption_block_id"], caption_id)
            self.assertEqual(session.get(Block, caption_id).source_span_json["caption_for_block_id"], code_id)
            # A second artifact takes the caption over.
            service.relabel_block(self.document_id, self.blocks[FIRST], "code", actor_id="t", reason="r")
            service.link_caption(self.document_id, caption_id, self.blocks[FIRST], actor_id="t", reason="r")
            session.commit()
            self.assertNotIn("linked_caption_block_id", session.get(Block, code_id).source_span_json)

    def test_split_inserts_a_block_shifts_ordinals_and_carries_translations(self) -> None:
        joined_id, quote_id = self.blocks[JOINED], self.blocks["Figure 1. The agent loop."]
        with self.session_factory() as session:
            before = {s.source_text: active_target_texts(session, [s.id])[s.id] for s in self._active_sentences(session, joined_id)}
            quote_ordinal = session.get(Block, quote_id).ordinal
            outcome = self._edit(session).split_block(self.document_id, joined_id, "Tools carry", actor_id="t", reason="two paragraphs")
            session.commit()
            new_id = outcome.block_ids[1]
            original, new_block = session.get(Block, joined_id), session.get(Block, new_id)
            self.assertEqual((original.source_text, new_block.source_text), ("Agents plan their work.", "Tools carry it out."))
            self.assertEqual(new_block.ordinal, original.ordinal + 1)
            self.assertEqual(session.get(Block, quote_id).ordinal, quote_ordinal + 1)
            self.assertEqual(outcome.fork.relation_counts, {"same": 2})
            self.assertEqual(outcome.fork.unpacketed_block_ids, [])
            moved = self._active_sentences(session, new_id)
            self.assertEqual([s.source_text for s in moved], ["Tools carry it out."])
            self.assertEqual(active_target_texts(session, [moved[0].id])[moved[0].id], before["Tools carry it out."])
            service = self._edit(session)
            for marker, message in (("Agents plan", "after its start"), ("nowhere to be found", "after its start"), ("Ag", "at least")):
                with self.assertRaisesRegex(StructureEditRejected, message):
                    service.split_block(self.document_id, self.blocks[OTHER], marker, actor_id="t", reason="r")
            with self.assertRaisesRegex(StructureEditRejected, "more than once"):
                service.split_block(self.document_id, self.blocks[SECOND], "ine", actor_id="t", reason="r")

    def test_refresh_keeps_a_split_and_parks_it_when_the_source_changes(self) -> None:
        joined_id, quote_id = self.blocks[JOINED], self.blocks["Figure 1. The agent loop."]
        with self.session_factory() as session:
            outcome = self._edit(session).split_block(self.document_id, joined_id, "Tools carry", actor_id="t", reason="r")
            session.commit()
            new_id = outcome.block_ids[1]
            sentence_ids = [s.id for s in self._active_sentences(session, joined_id) + self._active_sentences(session, new_id)]
            quote_ordinal = session.get(Block, quote_id).ordinal
        with self.session_factory() as session:
            refreshed = DocumentWorkflowService(session, export_root=Path(self.tempdir.name) / "exports").refresh_epub_structure(self.document_id)
            session.commit()
            self.assertFalse(refreshed.parse_revision_fork.forked)
            self.assertEqual(session.get(Block, joined_id).source_text, "Agents plan their work.")
            self.assertEqual(session.get(Block, new_id).status, ArtifactStatus.ACTIVE)
            self.assertEqual(session.get(Block, quote_id).ordinal, quote_ordinal)
            self.assertEqual([s.id for s in self._active_sentences(session, joined_id) + self._active_sentences(session, new_id)], sentence_ids)
            self.assertEqual(session.get(Block, quote_id).source_text, "Figure 1. The agent loop.")

        self._write_epub(_chapter(joined="Agents plan their work carefully. Tools carry it out."))
        with self.session_factory() as session:
            DocumentWorkflowService(session, export_root=Path(self.tempdir.name) / "exports").refresh_epub_structure(self.document_id)
            session.commit()
            original, parked = session.get(Block, joined_id), session.get(Block, new_id)
            self.assertEqual(original.source_text, "Agents plan their work carefully. Tools carry it out.")
            self.assertEqual(parked.status, ArtifactStatus.INVALIDATED)
            self.assertGreaterEqual(parked.ordinal, 1_000_000)
            self.assertEqual(self._active_sentences(session, new_id), [])
            self.assertEqual(session.get(Block, quote_id).ordinal, quote_ordinal - 1)
            stale = [row.replay_of_edit_id for row in session.scalars(select(StructureEdit).where(StructureEdit.status == "stale"))]
            self.assertEqual(stale, [outcome.edit_id])

    # --- replay -----------------------------------------------------------------------

    def test_refresh_replays_edits_and_keeps_translations(self) -> None:
        first_id, second_id, other_id = self.blocks[FIRST], self.blocks[SECOND], self.blocks[OTHER]
        with self.session_factory() as session:
            service = self._edit(session)
            merge = service.merge_blocks(self.document_id, first_id, second_id, actor_id="t", reason="r")
            relabel = service.relabel_block(self.document_id, other_id, "heading", actor_id="t", reason="r")
            session.commit()
            merged_sentence_ids = [s.id for s in self._active_sentences(session, first_id)]

        with self.session_factory() as session:
            refreshed = DocumentWorkflowService(session, export_root=Path(self.tempdir.name) / "exports").refresh_epub_structure(self.document_id)
            session.commit()
            first, second, other = session.get(Block, first_id), session.get(Block, second_id), session.get(Block, other_id)
            self.assertEqual(first.source_text, f"{FIRST} {SECOND}")
            self.assertEqual(second.status, ArtifactStatus.INVALIDATED)
            self.assertEqual(other.block_type, BlockType.HEADING)
            self.assertEqual([s.id for s in self._active_sentences(session, first_id)], merged_sentence_ids)
            self.assertFalse(refreshed.parse_revision_fork.forked)
            statuses = {
                (row.replay_of_edit_id, row.status)
                for row in session.scalars(select(StructureEdit).where(StructureEdit.replay_of_edit_id.is_not(None)))
            }
            self.assertEqual(statuses, {(merge.edit_id, "reapplied"), (relabel.edit_id, "reapplied")})

        # The source changed under the merged blocks: the merge is stale and the parser's text wins.
        self._write_epub(_chapter(second="context engineering, a young discipline."))
        with self.session_factory() as session:
            DocumentWorkflowService(session, export_root=Path(self.tempdir.name) / "exports").refresh_epub_structure(self.document_id)
            session.commit()
            self.assertEqual(session.get(Block, first_id).source_text, FIRST)
            self.assertEqual(session.get(Block, second_id).status, ArtifactStatus.ACTIVE)
            stale = session.scalars(select(StructureEdit).where(StructureEdit.status == "stale")).all()
            self.assertEqual([row.replay_of_edit_id for row in stale], [merge.edit_id])
            # A stale edit is not retried on later refreshes.
            StructureEditService(session).replay(self.document_id)
            self.assertEqual(len(session.scalars(select(StructureEdit).where(StructureEdit.status == "stale")).all()), 1)

    # --- agent ------------------------------------------------------------------------

    def test_agent_edits_wait_for_approval_and_are_off_by_default(self) -> None:
        self.assertNotIn("merge_blocks", StructureAgent.registry().names())
        self.assertIn("merge_blocks", StructureAgent.registry(allow_edits=True).names())
        plan = plan_for_run("translate_full", {"run_request": {"structure_review": "full", "structure_edits": "on"}})
        self.assertTrue(plan.structure_edits)
        self.assertFalse(plan_for_run("translate_full", {"run_request": {"structure_review": "full"}}).structure_edits)

        with self.session_factory() as session:
            seed = StructureAgent(session).start_turn(document_id=self.document_id, model_name="m", mode="full", allow_edits=True)
            session.commit()
        model = _FakeModel(
            [
                AgentStep(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            "c1",
                            "merge_blocks",
                            {"first_block_id": self.blocks[FIRST], "second_block_id": self.blocks[SECOND], "reason": "cut at a page break"},
                        )
                    ],
                    usage=_usage(),
                ),
                AgentStep(text="已合并。", usage=_usage()),
            ]
        )
        runner = AgentTurnRunner(
            session_factory=self.session_factory,
            model=model,
            registry=StructureAgent.registry(allow_edits=True),
            policy=StructureAgent.policy(),
            compaction_threshold_tokens=None,
        )
        self.assertEqual(runner.run(seed.turn_id).status, AgentTurnStatus.AWAITING_APPROVAL)
        with self.session_factory() as session:
            self.assertEqual(session.get(Block, self.blocks[SECOND]).status, ArtifactStatus.ACTIVE)
            (pending,) = AgentLedgerRepository(session).list_approvals(self.document_id, status=ApprovalStatus.PENDING)
            self.assertEqual(pending.kind, "merge_blocks")
            ApprovalService(session).decide(pending.id, approved=True, decided_by="editor:sam", note="yes")
            session.commit()
        self.assertEqual(runner.run(seed.turn_id).status, AgentTurnStatus.SUCCEEDED)
        with self.session_factory() as session:
            self.assertEqual(session.get(Block, self.blocks[SECOND]).status, ArtifactStatus.INVALIDATED)
            (edit,) = session.scalars(select(StructureEdit)).all()
            self.assertEqual((edit.actor_id, edit.turn_id), ("agent:structure", seed.turn_id))


    # --- API --------------------------------------------------------------------------

    def test_api_applies_and_lists_edits(self) -> None:
        import os
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from book_agent.app.main import create_app
        from book_agent.core.config import get_settings

        patcher = patch.dict(os.environ, {"BOOK_AGENT_AUTH_MODE": "disabled", "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        app = create_app()
        app.state.session_factory = self.session_factory
        base = f"/v1/documents/{self.document_id}/structure-edits"
        body = {"kind": "merge_blocks", "first_block_id": self.blocks[FIRST], "second_block_id": self.blocks[SECOND], "reason": "page break"}
        with TestClient(app) as client:
            created = client.post(base, json=body)
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["block_ids"], [self.blocks[FIRST], self.blocks[SECOND]])
            self.assertGreaterEqual(created.json()["retranslate_packet_count"], 1)
            self.assertEqual(client.post(base, json=body).status_code, 409)
            self.assertEqual(client.post(base, json={"kind": "relabel_block", "reason": "x"}).status_code, 422)
            listed = client.get(base).json()
            self.assertEqual([edit["kind"] for edit in listed["edits"]], ["merge_blocks"])
            self.assertEqual(listed["edits"][0]["actor_id"], "api:local")


if __name__ == "__main__":
    unittest.main()
