"""Minimal edits to a sentence's translation, recorded as a new translation attempt.

An edit never rewrites rows in place. It copies the packet's latest
successful attempt into a new ``translation_runs`` attempt with one target
segment's text replaced, supersedes the previous attempt's segments, and
leaves an audit row and a ``translation.segment.edited`` event. Review,
export and the Reviewer Agent all read "latest successful attempt", so the
edit takes effect everywhere and the old text stays in history.

Guards: protected blocks cannot be edited; the new text must keep every
locked glossary term the source contains (the shared matcher in
``domain.terminology.enforcement``); and a translation whose length is implausible
for the source (the output guardrail's bounds) is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ActorType,
    Detector,
    IssueStatus,
    PacketSentenceRole,
    ProtectedPolicy,
    RunStatus,
    TargetSegmentStatus,
)
from book_agent.domain.event_kinds import TRANSLATION_SEGMENT_EDITED
from book_agent.domain.models import Block, Sentence
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge, PacketSentenceMap, TargetSegment, TranslationRun
from book_agent.domain.terminology.enforcement import find_term_violations
from book_agent.infra.repositories.events import emit_event
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.services.glossary_service import GlossaryService

# Target/source character ratio bounds, as in the output guardrail: an edit
# outside them is a truncation or a rewrite, not a correction.
MIN_LENGTH_RATIO = 0.12
MAX_LENGTH_RATIO = 3.0
# Below this many source characters the ratio guard is meaningless.
MIN_SOURCE_CHARS_FOR_RATIO = 20


class EditRejected(ValueError):
    """The edit would break a guard; the message says which and why."""


@dataclass(slots=True)
class SegmentEditResult:
    translation_run_id: str
    attempt: int
    packet_id: str
    chapter_id: str
    target_segment_id: str
    covered_sentence_ids: list[str]
    previous_text: str
    new_text: str
    closed_model_issue_ids: list[str]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def close_model_issues_for_sentences(session: Session, sentence_ids: list[str], *, note: str, actor_id: str) -> list[str]:
    """Resolve open Reviewer Agent findings on sentences whose translation changed (human decisions untouched)."""
    if not sentence_ids:
        return []
    stale = list(
        session.scalars(
            select(ReviewIssue).where(
                ReviewIssue.sentence_id.in_(sentence_ids),
                ReviewIssue.detector == Detector.MODEL,
                ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
                ReviewIssue.decided_by.is_(None),
            )
        ).all()
    )
    if not stale:
        return []
    sync = ReviewRepository(session).sync_issues([], [], owned_existing=stale, resolution_note=note, actor_id=actor_id)
    return [issue.id for issue in sync.resolved]


class SegmentEditService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.translations = TranslationRepository(session)

    def edit_sentence(
        self,
        sentence_id: str,
        new_text: str,
        *,
        reason: str,
        actor_id: str,
        actor_type: ActorType = ActorType.MODEL,
        run_id: str | None = None,
    ) -> SegmentEditResult:
        new_text = (new_text or "").strip()
        if not new_text:
            raise EditRejected("new_text is empty")
        sentence = self.session.get(Sentence, sentence_id)
        if sentence is None:
            raise EditRejected(f"sentence not found: {sentence_id}")
        block = self.session.get(Block, sentence.block_id)
        if block is not None and block.protected_policy != ProtectedPolicy.TRANSLATE:
            raise EditRejected(f"block {block.id} is {block.protected_policy.value}; its text is not translated")

        run, segment, segments, edges = self._latest_translation(sentence)
        covered = sorted({edge.sentence_id for edge in edges if edge.target_segment_id == segment.id})
        previous_text = segment.text_zh or ""
        sources = self._covered_sources(sentence, covered)
        source_chars = len("".join("".join(sources).split()))
        if source_chars >= MIN_SOURCE_CHARS_FOR_RATIO:
            ratio = len("".join(new_text.split())) / source_chars
            if not MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO:
                raise EditRejected(
                    f"the edit is {len(new_text)} characters for {source_chars} source characters; "
                    "that is a truncation or a rewrite, not a correction"
                )
        self._check_locked_terms(sentence, sources, new_text)

        now = _utcnow()
        attempt = self.translations.next_attempt(run.packet_id)
        new_run = TranslationRun(
            id=stable_id("translation-run", run.packet_id, attempt),
            packet_id=run.packet_id,
            model_name=run.model_name,
            model_config_json={
                **(run.model_config_json or {}),
                "worker": "segment_edit",
                "edited_from_run_id": run.id,
                "edited_segment_ordinal": segment.ordinal,
                "edit_reason": reason,
                "edit_actor": actor_id,
            },
            prompt_version=run.prompt_version,
            attempt=attempt,
            status=RunStatus.SUCCEEDED,
            output_json={"edited_from_run_id": run.id},
            token_in=0,
            token_out=0,
            cost_usd=0,
            latency_ms=0,
            created_at=now,
            updated_at=now,
        )
        self.session.add(new_run)
        self.session.flush()
        new_segment_ids: dict[str, str] = {}
        for old in segments:
            new_id = stable_id("target-segment", new_run.id, old.ordinal)
            new_segment_ids[old.id] = new_id
            self.session.add(
                TargetSegment(
                    id=new_id,
                    chapter_id=old.chapter_id,
                    translation_run_id=new_run.id,
                    ordinal=old.ordinal,
                    text_zh=new_text if old.id == segment.id else old.text_zh,
                    segment_type=old.segment_type,
                    confidence=old.confidence,
                    final_status=TargetSegmentStatus.DRAFT,
                    created_at=now,
                    updated_at=now,
                )
            )
            old.final_status = TargetSegmentStatus.SUPERSEDED
            old.updated_at = now
        self.session.flush()
        for edge in edges:
            target_id = new_segment_ids[edge.target_segment_id]
            self.session.add(
                AlignmentEdge(
                    id=stable_id("alignment-edge", edge.sentence_id, target_id),
                    sentence_id=edge.sentence_id,
                    target_segment_id=target_id,
                    relation_type=edge.relation_type,
                    confidence=edge.confidence,
                    created_by=edge.created_by,
                )
            )
        edited_segment_id = new_segment_ids[segment.id]
        self.session.add(
            AuditEvent(
                object_type="target_segment",
                object_id=edited_segment_id,
                action="target_segment.edited",
                actor_type=actor_type,
                actor_id=actor_id,
                payload_json={
                    "reason": reason,
                    "previous_text": previous_text,
                    "new_text": new_text,
                    "previous_segment_id": segment.id,
                    "translation_run_id": new_run.id,
                },
            )
        )
        emit_event(
            self.session,
            kind=TRANSLATION_SEGMENT_EDITED,
            run_id=run_id,
            chapter_id=segment.chapter_id,
            packet_id=run.packet_id,
            actor_kind="user" if actor_type == ActorType.HUMAN else "agent" if actor_type == ActorType.MODEL else "system",
            actor_id=actor_id,
            payload={
                "translation_run_id": new_run.id,
                "attempt": attempt,
                "target_segment_id": edited_segment_id,
                "sentence_ids": covered,
                "reason": reason,
            },
        )
        self.session.flush()
        closed = close_model_issues_for_sentences(
            self.session,
            covered,
            note=f"Translation edited (attempt {attempt}): {reason}"[:500],
            actor_id=actor_id,
        )
        return SegmentEditResult(
            translation_run_id=new_run.id,
            attempt=attempt,
            packet_id=run.packet_id,
            chapter_id=segment.chapter_id,
            target_segment_id=edited_segment_id,
            covered_sentence_ids=covered,
            previous_text=previous_text,
            new_text=new_text,
            closed_model_issue_ids=closed,
        )

    def _latest_translation(
        self, sentence: Sentence
    ) -> tuple[TranslationRun, TargetSegment, list[TargetSegment], list[AlignmentEdge]]:
        packet_ids = list(
            self.session.scalars(
                select(PacketSentenceMap.packet_id).where(
                    PacketSentenceMap.sentence_id == sentence.id,
                    PacketSentenceMap.role == PacketSentenceRole.CURRENT,
                )
            ).all()
        )
        runs = list(
            self.session.scalars(
                select(TranslationRun)
                .where(TranslationRun.packet_id.in_(packet_ids), TranslationRun.status == RunStatus.SUCCEEDED)
                .order_by(TranslationRun.attempt.desc())
            ).all()
        ) if packet_ids else []
        for run in runs:
            segments = list(
                self.session.scalars(
                    select(TargetSegment).where(TargetSegment.translation_run_id == run.id).order_by(TargetSegment.ordinal)
                ).all()
            )
            if not segments:
                continue
            edges = list(
                self.session.scalars(
                    select(AlignmentEdge).where(AlignmentEdge.target_segment_id.in_([item.id for item in segments]))
                ).all()
            )
            aligned = [item for item in segments if any(e.sentence_id == sentence.id and e.target_segment_id == item.id for e in edges)]
            if not aligned:
                continue
            if len(aligned) > 1:
                raise EditRejected(
                    f"sentence {sentence.id} is split across {len(aligned)} target segments; retranslate the packet instead"
                )
            return run, aligned[0], segments, edges
        raise EditRejected(f"sentence {sentence.id} has no translation to edit; retranslate the packet instead")

    def _covered_sources(self, sentence: Sentence, covered: list[str]) -> list[str]:
        return [
            item.source_text or ""
            for item in self.session.scalars(select(Sentence).where(Sentence.id.in_(covered or [sentence.id]))).all()
        ]

    def _check_locked_terms(self, sentence: Sentence, sources: list[str], new_text: str) -> None:
        locked = GlossaryService(self.session).locked_terms_for_chapter(sentence.document_id, sentence.chapter_id)
        violations = find_term_violations([(sentence.id, " ".join(sources), new_text)], locked)
        if violations:
            detail = "; ".join(
                f'"{item.source_term}" must be rendered as {" / ".join(item.accepted_renderings)}' for item in violations
            )
            raise EditRejected(f"the edit breaks locked glossary terms: {detail}")
