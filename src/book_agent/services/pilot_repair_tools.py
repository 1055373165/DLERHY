"""The pilot repair's closed toolbox (docs/agent-upgrade/08-pilot-quality-gate.md §7.2).

Every repair the loop (services/pilot_repair.py) makes is one of these, applied to this book's
structure data and translation settings through the services that already own them:

=========================  ====================================================================
tool                       how
=========================  ====================================================================
resplit_chapters           a new chapter starting at another chapter's heading (by the PDF
                           outline, or by a chapter heading found inside a chapter); its blocks,
                           sentences, packets and translations move with it
structure_edit             ``StructureEditService``: relabel, split, merge, link a caption, or
                           override reading-copy metadata (hide a block, a table's cells, keep a
                           paragraph out of a figure); re-segmentation goes through the parse
                           revision fork, which keeps translations it can carry
figure_region_override     a figure's crop fitted to the figure: out of the header band and away
                           from its caption, out to lines it cut (``figure_region_problems``)
glossary                   the book's rendering of a term settled in the glossary (it stays for
                           the whole book), then the pilot's text made to follow it
book_instructions          a BOOK.md decision (the book's convention) for the whole-book run
retranslate_packets        the affected packets only, with the problems as hints
segment_edit               a deterministic correction of the translation text, as a new attempt
review_recheck             the rule review pass or the export gate run again on a chapter whose
                           blocking review issue is a finding: what they no longer find resolves
=========================  ====================================================================

Nothing here changes code, and nothing commits: the loop runs each repair in a savepoint and keeps
it only when the layered QA says it helped. A tool raises ``NotApplicable`` when a finding gives it
nothing to act on, and ``BudgetExhausted`` before a model call the budget no longer covers.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import (
    ArtifactStatus,
    BlockType,
    DecisionScope,
    LockLevel,
    MemoryScopeType,
    MemoryStatus,
    PacketSentenceRole,
    PacketStatus,
    RunStatus,
    SnapshotType,
    TargetSegmentStatus,
)
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED
from book_agent.domain.models import Block, Chapter, Document, Event, MemorySnapshot, Sentence
from book_agent.domain.models.document import DocumentImage
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.models.translation import (
    AlignmentEdge,
    PacketSentenceMap,
    TargetSegment,
    TranslationPacket,
    TranslationRun,
)
from book_agent.domain.quality import pilot_qa
from book_agent.domain.quality import zh_text_fixes as fixes
from book_agent.domain.quality.pilot_repair import WorkItem
from book_agent.domain.structure import parse_health
from book_agent.domain.structure.callouts import (
    CALLOUT_KIND_CODE,
    CALLOUT_KIND_PROSE,
    LooseBlock,
    looks_like_code_callout,
    pdf_callout_lines,
    split_callouts,
)
from book_agent.domain.structure.figure_clustering import FIGURE_REGION_CONTENT_EXTENT
from book_agent.domain.structure.parse_health import HealthBlock
from book_agent.domain.terminology.matching import SourceTermIndex, source_term_key
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.repositories.review import active_target_texts
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.segment_edit import SegmentEditService
from book_agent.services.structure_edits import StructureEditRejected, StructureEditService

logger = logging.getLogger(__name__)

ACTOR_ID = "agent.pilot_repair"


class NotApplicable(Exception):
    """The path has nothing to act on for these findings (the loop tries the next one)."""


class BudgetExhausted(Exception):
    """The next model call would go over the repair budget (the loop stops)."""


@dataclass(slots=True)
class ToolResult:
    params: dict[str, Any] = field(default_factory=dict)
    block_ids: list[str] = field(default_factory=list)
    # Packets retranslated by the model (what the budget paid for).
    packet_ids: list[str] = field(default_factory=list)
    # Packets whose text was corrected without a model call.
    edited_packet_ids: list[str] = field(default_factory=list)
    decided_by: str = "rule"


class StructureJudge(Protocol):
    """Proposes one structure edit where the rules cannot place it (the pilot repair agent)."""

    def propose(self, document_id: str, finding: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
        """{"kind": "split"|"relabel"|"hide", "block_id": ..., "second_part_starts_with"?: ..., "block_type"?: ...}."""


@dataclass(slots=True)
class RepairBudget:
    """Twice the pilot's cost (§7.4); without prices, twice the pilot's packet count in retranslations."""

    budget_usd: float | None
    max_packets: int
    cost_per_packet_usd: float = 0.0
    spent_usd: float = 0.0
    packets: int = 0

    def charge(self, cost_usd: float) -> None:
        self.spent_usd += max(0.0, float(cost_usd or 0.0))

    def allow_packet(self) -> bool:
        if self.packets + 1 > self.max_packets:
            return False
        # What the next packet will cost: the pilot's average, else what this repair's packets cost so far.
        projected = self.cost_per_packet_usd or (self.spent_usd / self.packets if self.packets else 0.0)
        if self.budget_usd is not None and self.spent_usd + projected > self.budget_usd:
            return False
        return True


@dataclass(slots=True)
class RepairContext:
    session: Session
    document_id: str
    run: DocumentRun | None
    chapter_ids: list[str]
    workflow_factory: Callable[[Session], Any]
    budget: RepairBudget
    # figure_problems(page, bbox, furniture) for a PDF, else None.
    figure_problems: Callable[[int, Sequence[float], list[Sequence[float]]], list[Any]] | None = None
    judge: StructureJudge | None = None
    # The book's CJK–Latin spacing ("spaced" | "unspaced" | None): retranslations are written that way.
    spacing_convention: str | None = None
    _workflow: Any = None

    @property
    def run_id(self) -> str | None:
        return self.run.id if self.run is not None else None

    def workflow(self) -> Any:
        if self._workflow is None:
            self._workflow = self.workflow_factory(self.session)
        return self._workflow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str | None) -> str:
    return pilot_qa._norm(text)


class PilotRepairToolbox:
    def __init__(self, ctx: RepairContext) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self.structure = StructureEditService(ctx.session)
        self._proposal: tuple[dict[str, Any] | None, list[str]] | None = None
        self._consulted = False
        # Packets an attempt completed from the translation memory instead of retranslating them.
        self.memory_filled: list[dict[str, Any]] = []
        self._settled_cache: dict[str, str] | None = None

    # --- dispatch ----------------------------------------------------------------------

    def consult(self, item: WorkItem) -> None:
        """Ask the repair agent before the repair's savepoint opens (its turn is written on its own).

        The proposal waits for ``apply``. Nothing of the main session stays open while the agent
        runs: the agent's ledger writes on another connection.
        """
        self._proposal = None
        self._consulted = False
        if not item.path.needs_agent or self.ctx.judge is None:
            return
        finding = item.findings[0]
        try:
            block = self._finding_block(finding)
        except NotApplicable:
            self.session.commit()
            return
        previous, following = self._neighbours(block)
        context = [
            {"block_id": b.id, "block_type": b.block_type.value, "text": (b.source_text or "")[:1500], "is_target": b.id == block.id}
            for b in (previous, block, following)
            if b is not None
        ]
        self.session.commit()
        self._proposal = (self.ctx.judge.propose(self.ctx.document_id, finding, context), [entry["block_id"] for entry in context])
        self._consulted = True

    def apply(self, item: WorkItem) -> ToolResult:
        handler = getattr(self, "_" + item.path.name.replace(".", "__"), None)
        if handler is None:
            raise NotApplicable(f"no handler for {item.path.name}")
        self.memory_filled = []
        self._settled_cache = None
        result = handler(item)
        if self.memory_filled:
            filled = [entry["packet_id"] for entry in self.memory_filled]
            result.edited_packet_ids = list(dict.fromkeys([*result.edited_packet_ids, *filled]))
            result.packet_ids = [packet_id for packet_id in result.packet_ids if packet_id not in filled]
            result.params = {**result.params, "translation_memory": list(self.memory_filled)}
        return result

    # --- shared helpers --------------------------------------------------------------------

    def _block(self, block_id: str | None) -> Block:
        block = self.session.get(Block, block_id) if block_id else None
        if block is None or block.status != ArtifactStatus.ACTIVE:
            raise NotApplicable(f"block {block_id} is not active")
        chapter = self.session.get(Chapter, block.chapter_id)
        if chapter is None or chapter.document_id != self.ctx.document_id:
            raise NotApplicable(f"block {block_id} is not in this book")
        return block

    def _finding_block(self, finding: dict[str, Any]) -> Block:
        location = finding.get("location") or {}
        target = finding.get("target") or {}
        block_id = location.get("block_id") or (target.get("id") if target.get("kind") == "block" else None)
        return self._block(block_id)

    def _chapter_blocks(self, chapter_id: str) -> list[Block]:
        return list(
            self.session.scalars(
                select(Block)
                .where(Block.chapter_id == chapter_id, Block.status == ArtifactStatus.ACTIVE, Block.ordinal < 1_000_000)
                .order_by(Block.ordinal)
            ).all()
        )

    def _neighbours(self, block: Block) -> tuple[Block | None, Block | None]:
        blocks = self._chapter_blocks(block.chapter_id)
        index = next((i for i, item in enumerate(blocks) if item.id == block.id), None)
        if index is None:
            return None, None
        return (blocks[index - 1] if index > 0 else None, blocks[index + 1] if index + 1 < len(blocks) else None)

    def _packets_of_blocks(self, block_ids: Sequence[str]) -> list[str]:
        if not block_ids:
            return []
        rows = self.session.execute(
            select(PacketSentenceMap.packet_id)
            .join(Sentence, Sentence.id == PacketSentenceMap.sentence_id)
            .where(
                Sentence.block_id.in_(list(block_ids)),
                Sentence.retired_by_revision_id.is_(None),
                PacketSentenceMap.role == PacketSentenceRole.CURRENT,
            )
        ).all()
        return list(dict.fromkeys(str(packet_id) for (packet_id,) in rows))

    def _finding_packets(self, finding: dict[str, Any]) -> list[str]:
        location = finding.get("location") or {}
        target = finding.get("target") or {}
        if location.get("sentence_id"):
            packet = self.session.scalar(
                select(PacketSentenceMap.packet_id).where(
                    PacketSentenceMap.sentence_id == location["sentence_id"], PacketSentenceMap.role == PacketSentenceRole.CURRENT
                )
            )
            if packet:
                return [str(packet)]
        if location.get("block_id"):
            packets = self._packets_of_blocks([location["block_id"]])
            if packets:
                return packets
        if target.get("kind") == "packet" and target.get("id"):
            return [str(target["id"])]
        if location.get("packet_id"):
            return [str(location["packet_id"])]
        return []

    def retranslate(self, packet_ids: Sequence[str], hints: Sequence[str]) -> list[str]:
        """Retranslate these packets (only these) with the problems as hints; returns the ones translated.

        A packet a structure edit left with a few untranslated sentences whose exact source the book
        already translates (a restored "Summary" heading) is completed from that translation instead
        (``fill_from_memory``): nothing else in it changes. The hints carry the book's settled renderings
        of the glossary terms in the packets, so a retranslation does not bring a new variant.
        """
        packet_ids = [packet_id for packet_id in dict.fromkeys(packet_ids) if not self.fill_from_memory(packet_id)]
        if not packet_ids:
            return []
        settled = self.settled_term_hint(packet_ids)
        hints = [*[hint for hint in hints if hint][:7], *([settled] if settled else [])]
        workflow = self.ctx.workflow()
        translation = workflow.translation_service
        ops = OpsRepository(self.session)
        translation.memory_service.reject_pending_packet_proposals(packet_ids=packet_ids)
        done: list[str] = []
        for packet_id in packet_ids:
            if not self.ctx.budget.allow_packet():
                raise BudgetExhausted(
                    f"retranslating packet {packet_id} would exceed the repair budget "
                    f"(${self.ctx.budget.spent_usd:.4f} of ${self.ctx.budget.budget_usd or 0:.4f}, "
                    f"{self.ctx.budget.packets}/{self.ctx.budget.max_packets} packets)"
                )
            packet = ops.mark_packet_ready_for_rerun(packet_id)
            if packet.status != PacketStatus.BUILT:
                continue
            watermark = int(self.session.scalar(select(func.coalesce(func.max(Event.id), 0))) or 0)
            artifacts = translation.execute_packet(
                packet_id,
                rerun_hints=tuple(hint for hint in hints if hint)[:8],
                auto_commit_memory=False,
                run_id=self.ctx.run_id,
            )
            # The new attempt is the packet's translation; older attempts must not stay active
            # (repositories/translation.py: supersede_other_attempts).
            if artifacts.translation_run.status == RunStatus.SUCCEEDED:
                TranslationRepository(self.session).supersede_other_attempts(packet_id, artifacts.translation_run.id)
            self.ctx.budget.packets += 1
            # Charged as it is spent, so the next packet is checked against it (the loop settles the attempt).
            self.ctx.budget.charge(
                float(
                    self.session.scalar(
                        select(func.coalesce(func.sum(Event.payload["cost_usd"].as_float()), 0.0)).where(
                            Event.id > watermark, Event.kind == LLM_CALL_COMPLETED
                        )
                    )
                    or 0.0
                )
            )
            done.append(packet_id)
        self.session.flush()
        self._normalize(done)
        return done

    def _charge_since(self, watermark: int) -> None:
        self.ctx.budget.charge(
            float(
                self.session.scalar(
                    select(func.coalesce(func.sum(Event.payload["cost_usd"].as_float()), 0.0)).where(
                        Event.id > watermark, Event.kind == LLM_CALL_COMPLETED
                    )
                )
                or 0.0
            )
        )

    def translate_block_sentences(
        self, packet_ids: Sequence[str], block_ids: Sequence[str], hints: Sequence[str], *, redo_all: bool = False
    ) -> tuple[list[str], list[str]]:
        """Translate only the untranslated sentences of these blocks in each packet; the rest keeps its text.

        A rebuilt table leaves its new cells without a translation in a packet whose other sentences
        are translated; those cells alone go to the model (the packet is the context) and are added
        to the packet's translation as one new attempt. A packet with other missing sentences, or an
        answer that does not give each cell its own translation, is retranslated whole.
        Returns (packets completed this way, packets retranslated).
        """
        wanted_blocks = set(block_ids)
        filled: list[str] = []
        whole: list[str] = []
        translation = self.ctx.workflow().translation_service
        for packet_id in dict.fromkeys(packet_ids):
            packet = self.session.get(TranslationPacket, packet_id)
            if packet is None:
                continue
            run, missing = self._uncovered_sentences(packet_id)
            if redo_all and run is not None and not any(sentence.block_id not in wanted_blocks for sentence in missing):
                # Every translatable sentence of the blocks: the translations the fork carried over may be
                # an old answer that gave the whole table in one cell.
                missing = [sentence for sentence in self._current_sentences(packet_id) if sentence.block_id in wanted_blocks]
            if not missing:
                continue
            if run is None or any(sentence.block_id not in wanted_blocks for sentence in missing):
                whole.append(packet_id)
                continue
            if not self.ctx.budget.allow_packet():
                raise BudgetExhausted(
                    f"translating the cells of packet {packet_id} would exceed the repair budget "
                    f"(${self.ctx.budget.spent_usd:.4f} of ${self.ctx.budget.budget_usd or 0:.4f})"
                )
            watermark = int(self.session.scalar(select(func.coalesce(func.max(Event.id), 0))) or 0)
            texts = translation.translate_sentences(
                packet_id, [sentence.id for sentence in missing], rerun_hints=tuple(hint for hint in hints if hint)[:8], run_id=self.ctx.run_id
            )
            self.ctx.budget.packets += 1
            self._charge_since(watermark)
            if not texts:
                whole.append(packet_id)
                continue
            try:
                with self.session.begin_nested():
                    SegmentEditService(self.session).fill_sentences(
                        packet_id, texts, reason="只翻译重建后的表格单元格", actor_id=ACTOR_ID, run_id=self.ctx.run_id, replace=redo_all
                    )
            except Exception:  # a guard refused the cells: the packet is retranslated as before
                logger.debug("filling the cells of packet %s refused", packet_id, exc_info=True)
                whole.append(packet_id)
                continue
            packet.status = PacketStatus.TRANSLATED
            self.session.flush()
            filled.append(packet_id)
        retranslated = self.retranslate(whole, hints) if whole else []
        self._normalize(filled)
        return filled, retranslated

    # --- translation memory and settled terms ---------------------------------------------------

    def _uncovered_sentences(self, packet_id: str) -> tuple[TranslationRun | None, list[Sentence]]:
        """The packet's latest attempt, and its translatable current sentences that attempt does not translate."""
        run = self.session.scalars(
            select(TranslationRun)
            .where(TranslationRun.packet_id == packet_id, TranslationRun.status == RunStatus.SUCCEEDED)
            .order_by(TranslationRun.attempt.desc())
            .limit(1)
        ).first()
        current = list(
            self.session.scalars(
                select(Sentence)
                .join(PacketSentenceMap, PacketSentenceMap.sentence_id == Sentence.id)
                .where(
                    PacketSentenceMap.packet_id == packet_id,
                    PacketSentenceMap.role == PacketSentenceRole.CURRENT,
                    Sentence.translatable.is_(True),
                    Sentence.retired_by_revision_id.is_(None),
                )
            ).all()
        )
        if run is None:
            return None, current
        covered = set(
            self.session.scalars(
                select(AlignmentEdge.sentence_id)
                .join(TargetSegment, TargetSegment.id == AlignmentEdge.target_segment_id)
                .where(TargetSegment.translation_run_id == run.id, TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED)
            ).all()
        )
        return run, [sentence for sentence in current if sentence.id not in covered]

    def _current_sentences(self, packet_id: str) -> list[Sentence]:
        return list(
            self.session.scalars(
                select(Sentence)
                .join(PacketSentenceMap, PacketSentenceMap.sentence_id == Sentence.id)
                .where(
                    PacketSentenceMap.packet_id == packet_id,
                    PacketSentenceMap.role == PacketSentenceRole.CURRENT,
                    Sentence.translatable.is_(True),
                    Sentence.retired_by_revision_id.is_(None),
                )
            ).all()
        )

    def remembered_translation(self, sentence: Sentence) -> str | None:
        """The book's translation of this exact source sentence elsewhere (the most used one), if any."""
        key = " ".join((sentence.source_text or "").split())
        if not key:
            return None
        twins = [
            twin_id
            for twin_id, text in self.session.execute(
                select(Sentence.id, Sentence.source_text).where(
                    Sentence.document_id == sentence.document_id,
                    Sentence.id != sentence.id,
                    Sentence.translatable.is_(True),
                    Sentence.retired_by_revision_id.is_(None),
                    Sentence.source_text.like(key.split()[0].replace("%", "").replace("_", "") + "%"),
                )
            ).all()
            if " ".join((text or "").split()) == key
        ]
        if not twins:
            return None
        texts = Counter(text.strip() for text in active_target_texts(self.session, twins).values() if text and text.strip())
        if not texts:
            return None
        return texts.most_common(1)[0][0]

    def fill_from_memory(self, packet_id: str) -> bool:
        """Complete a packet whose only missing translations are sentences the book already translates."""
        packet = self.session.get(TranslationPacket, packet_id)
        if packet is None or packet.status != PacketStatus.BUILT:
            return False
        run, missing = self._uncovered_sentences(packet_id)
        if run is None or not missing:
            return False
        texts: dict[str, str] = {}
        for sentence in missing:
            remembered = self.remembered_translation(sentence)
            if remembered is None:
                return False
            texts[sentence.id] = remembered
        try:
            with self.session.begin_nested():
                SegmentEditService(self.session).fill_sentences(
                    packet_id, texts, reason="沿用全书对同一句原文的译法", actor_id=ACTOR_ID, run_id=self.ctx.run_id
                )
        except Exception:  # a guard refused it: the packet is retranslated as before
            logger.debug("translation memory refused for packet %s", packet_id, exc_info=True)
            return False
        packet.status = PacketStatus.TRANSLATED
        self.session.flush()
        self.memory_filled.append({"packet_id": packet_id, "sentences": {sid: text for sid, text in texts.items()}})
        return True

    def _settled_renderings(self) -> dict[str, str]:
        """Glossary term -> the rendering the book's translation uses most (its target when unused)."""
        if self._settled_cache is not None:
            return self._settled_cache
        entries = [
            entry
            for entry in GlossaryService(self.session).list_document_entries(self.ctx.document_id)
            if entry.source_term and entry.target_term
        ]
        settled: dict[str, str] = {}
        if entries:
            index = SourceTermIndex([entry.source_term for entry in entries])
            by_key = {source_term_key(entry.source_term): entry for entry in entries}
            rows = self.session.execute(
                select(Sentence.id, Sentence.source_text).where(
                    Sentence.chapter_id.in_(list(self.ctx.chapter_ids) or ["-"]),
                    Sentence.translatable.is_(True),
                    Sentence.retired_by_revision_id.is_(None),
                )
            ).all()
            keys_by_sentence = {str(sentence_id): index.keys_in(text or "") for sentence_id, text in rows}
            with_terms = [sentence_id for sentence_id, keys in keys_by_sentence.items() if keys]
            targets = active_target_texts(self.session, with_terms) if with_terms else {}
            uses: dict[str, Counter[str]] = {}
            for sentence_id in with_terms:
                for key in keys_by_sentence[sentence_id]:
                    entry = by_key.get(key)
                    if entry is None:
                        continue
                    renderings = [r for r in dict.fromkeys([entry.target_term, *(entry.target_variants_json or [])]) if r]
                    for rendering in pilot_qa._renderings_used(targets.get(sentence_id, ""), renderings)[:1]:
                        uses.setdefault(key, Counter())[rendering] += 1
            for key, entry in by_key.items():
                counts = uses.get(key)
                settled[key] = counts.most_common(1)[0][0] if counts else entry.target_term
        self._settled_cache = settled
        return settled

    def settled_term_hint(self, packet_ids: Sequence[str]) -> str | None:
        """One hint naming the book's settled rendering of every glossary term in these packets."""
        settled = self._settled_renderings()
        if not settled:
            return None
        entries = {source_term_key(entry.source_term): entry for entry in GlossaryService(self.session).list_document_entries(self.ctx.document_id)}
        sources = self.session.scalars(
            select(Sentence.source_text)
            .join(PacketSentenceMap, PacketSentenceMap.sentence_id == Sentence.id)
            .where(PacketSentenceMap.packet_id.in_(list(packet_ids)), PacketSentenceMap.role == PacketSentenceRole.CURRENT)
        ).all()
        index = SourceTermIndex([entries[key].source_term for key in settled if key in entries])
        keys = sorted({key for text in sources for key in index.keys_in(text or "")})
        pairs = [f"「{entries[key].source_term}」译为「{settled[key]}」" for key in keys if key in entries and settled.get(key)]
        if not pairs:
            return None
        return "本书已定的术语译法，照用，不要换别的说法：" + "；".join(pairs[:12]) + "。"

    def _normalize(self, packet_ids: Sequence[str]) -> None:
        """A fresh translation written the book's way: its CJK–Latin spacing, full-width punctuation.

        The same deterministic fixes the L7 checks call for; without them a retranslation that fixes
        its problem would be rolled back for spacing the rest of the book does not have.
        """
        convention = self.ctx.spacing_convention
        if not packet_ids:
            return
        editor = SegmentEditService(self.session)

        def transform(text: str, _ids: list[str]) -> str:
            fixed = fixes.fix_halfwidth_punctuation(fixes.fix_odd_whitespace(fixes.strip_markdown_emphasis(text)))
            return fixes.fix_cjk_latin_spacing(fixed, convention) if convention in {"spaced", "unspaced"} else fixed

        for packet_id in packet_ids:
            try:
                editor.rewrite_packet(packet_id, transform, reason="按全书习惯规范空格和标点", actor_id=ACTOR_ID, run_id=self.ctx.run_id)
            except Exception:  # a guard refused the correction: the translation stays as the model wrote it
                logger.debug("normalizing packet %s refused", packet_id, exc_info=True)

    def _retranslate_fork(self, outcome: Any, hints: Sequence[str]) -> list[str]:
        fork = getattr(outcome, "fork", None)
        packet_ids = list(getattr(fork, "retranslate_packet_ids", []) or []) if fork is not None else []
        return self.retranslate(packet_ids, hints) if packet_ids else []

    def _rewrite(self, packet_ids: Sequence[str], transform: Callable[[str, list[str]], str | None], reason: str) -> list[str]:
        editor = SegmentEditService(self.session)
        changed = []
        for packet_id in dict.fromkeys(packet_ids):
            result = editor.rewrite_packet(packet_id, transform, reason=reason, actor_id=ACTOR_ID, run_id=self.ctx.run_id)
            if result is not None:
                changed.append(packet_id)
        if not changed:
            raise NotApplicable("the translation has nothing this fix changes")
        return changed

    def _pilot_packets(self) -> list[str]:
        return [
            str(packet_id)
            for packet_id in self.session.scalars(
                select(TranslationPacket.id).where(TranslationPacket.chapter_id.in_(list(self.ctx.chapter_ids)))
            ).all()
        ]

    @staticmethod
    def _hints(findings: Sequence[dict[str, Any]]) -> list[str]:
        hints = []
        for finding in findings:
            message = str(finding.get("message") or "")
            evidence = finding.get("evidence") or {}
            extra = ""
            if evidence.get("expected"):
                extra = f"（应译为「{evidence['expected']}」）"
            elif evidence.get("suggested"):
                extra = f"（审校建议：{evidence['suggested']}）"
            hints.append(f"上一版译文的问题：{message}{extra}。请修正，其余保持原意。")
        return list(dict.fromkeys(hints))

    # --- retranslate_packets --------------------------------------------------------------

    def _retranslate_packets__with_hints(self, item: WorkItem) -> ToolResult:
        packets = list(dict.fromkeys(packet for finding in item.findings for packet in self._finding_packets(finding)))
        if not packets:
            raise NotApplicable("the findings name no packet")
        done = self.retranslate(packets, self._hints(item.findings))
        return ToolResult(params={"packet_ids": packets, "checks": sorted({f["check_id"] for f in item.findings})}, packet_ids=done)

    def _retranslate_packets__callout_line_by_line(self, item: WorkItem) -> ToolResult:
        """A callout whose lines a retranslation joined: its packets again, each line on its own."""
        packets = list(dict.fromkeys(packet for finding in item.findings for packet in self._finding_packets(finding)))
        if not packets:
            raise NotApplicable("the findings name no packet")
        done = self.retranslate(
            packets,
            [
                "这是书中的一段示例提示词（放在带图标的提示框里）：每一行单独翻译成中文，一行对应一个译文，"
                "保持原来的分行和列表符号，不要把几行并成一段；{query} 这类占位符、代码标识符、JSON 键名原样保留。"
            ],
        )
        return ToolResult(params={"packet_ids": packets}, packet_ids=done)

    # --- segment_edit ---------------------------------------------------------------------

    def _segment_edit__strip_doubled_list_marker(self, item: WorkItem) -> ToolResult:
        packets = self._block_packets(item.findings)
        sources: dict[str, str] = {}

        def transform(text: str, ids: list[str]) -> str:
            missing = [sentence_id for sentence_id in ids if sentence_id not in sources]
            if missing:
                for sentence in self.session.scalars(select(Sentence).where(Sentence.id.in_(missing))).all():
                    sources[sentence.id] = sentence.source_text or ""
            return fixes.strip_unsourced_bullet(text, [sources.get(sentence_id, "") for sentence_id in ids])

        changed = self._rewrite(packets, transform, "编号列表项译文开头多出的圆点去掉")
        return ToolResult(params={"packet_count": len(packets)}, edited_packet_ids=changed)


    def _block_packets(self, findings: Sequence[dict[str, Any]]) -> list[str]:
        packets: list[str] = []
        for finding in findings:
            packets.extend(self._finding_packets(finding))
        return list(dict.fromkeys(packets))

    def _segment_edit__strip_leaked_markers(self, item: WorkItem) -> ToolResult:
        packets = self._block_packets(item.findings)
        markers = sorted({str((finding.get("evidence") or {}).get("marker") or "") for finding in item.findings} - {""})
        convention = self.ctx.spacing_convention

        def transform(text: str, _ids: list[str]) -> str:
            fixed = fixes.strip_leaked_markers(text)
            # "使用**LLM**的" becomes "使用LLM的": the words the marks separated take the book's spacing.
            if fixed != text and convention in {"spaced", "unspaced"}:
                fixed = fixes.fix_cjk_latin_spacing(fixed, convention)
            return fixed

        changed = self._rewrite(packets, transform, "去掉译文里的程序提示")
        return ToolResult(params={"markers": markers}, edited_packet_ids=changed)

    def _segment_edit__cjk_latin_spacing(self, item: WorkItem) -> ToolResult:
        conventions = Counter(str((finding.get("evidence") or {}).get("convention") or "") for finding in item.findings)
        convention = conventions.most_common(1)[0][0] if conventions else ""
        if convention not in {"spaced", "unspaced"}:
            raise NotApplicable("the findings do not say the book's convention")
        packets = self._block_packets(item.findings)

        def transform(text: str, _ids: list[str]) -> str:
            return fixes.fix_odd_whitespace(fixes.fix_cjk_latin_spacing(text, convention))

        changed = self._rewrite(packets, transform, f"中英文之间{'加空格' if convention == 'spaced' else '不加空格'}，与全书一致")
        # The whole-book run writes it this way from the start.
        self._record_book_decision(
            "book.style.cjk_latin_spacing",
            "中英文之间加一个空格（如「使用 RAG 系统」）。" if convention == "spaced" else "中英文之间不加空格（如「使用RAG系统」）。",
            "试译质检：全书多数段落的写法",
        )
        return ToolResult(params={"convention": convention, "packet_count": len(packets)}, edited_packet_ids=changed)

    def _segment_edit__fullwidth_punctuation(self, item: WorkItem) -> ToolResult:
        packets = self._block_packets(item.findings)
        changed = self._rewrite(packets, lambda text, _ids: fixes.fix_halfwidth_punctuation(text), "中文里的半角标点改为全角")
        return ToolResult(params={"packet_count": len(packets)}, edited_packet_ids=changed)

    def _segment_edit__use_locked_rendering(self, item: WorkItem) -> ToolResult:
        """A locked term the translation renders another known way: that way becomes the locked one."""
        (finding,) = item.findings[:1]
        evidence = finding.get("evidence") or {}
        source_term, expected = str(evidence.get("source_term") or ""), str(evidence.get("expected") or "")
        entry = next(
            (
                entry
                for entry in GlossaryService(self.session).list_document_entries(self.ctx.document_id)
                if source_term_key(entry.source_term) == source_term_key(source_term)
            ),
            None,
        )
        others = [str(v) for v in (entry.target_variants_json or [])] if entry is not None else []
        others = [other for other in others if other and other != expected]
        if not source_term or not expected or not others:
            raise NotApplicable("the translation uses no known other rendering to replace")
        packets = self._finding_packets(finding)

        def transform(text: str, _ids: list[str]) -> str:
            for other in others:
                text = fixes.replace_rendering(text, other, expected)
            return text

        changed = self._rewrite(packets, transform, f"「{source_term}」按锁定术语译为「{expected}」")
        return ToolResult(params={"source_term": source_term, "expected": expected, "replaced": others}, edited_packet_ids=changed)

    def _segment_edit__apply_reviewer_suggestion(self, item: WorkItem) -> ToolResult:
        edited: list[str] = []
        applied = []
        editor = SegmentEditService(self.session)
        for finding in item.findings:
            evidence = finding.get("evidence") or {}
            sentence_id = (finding.get("location") or {}).get("sentence_id")
            suggestion = str(evidence.get("suggested") or "").strip()
            issue_id = evidence.get("issue_id")
            if issue_id:
                issue = self.session.get(ReviewIssue, issue_id)
                if issue is not None:
                    suggestion = str((issue.evidence_json or {}).get("suggested_target_text") or suggestion).strip()
                    sentence_id = issue.sentence_id or sentence_id
            if not sentence_id or not suggestion or suggestion.endswith("…"):
                continue
            result = editor.edit_sentence(
                sentence_id, suggestion, reason=f"采纳模型审校的修改建议：{finding.get('message')}"[:300], actor_id=ACTOR_ID, run_id=self.ctx.run_id
            )
            edited.append(result.packet_id)
            applied.append({"sentence_id": sentence_id, "issue_id": issue_id})
        if not edited:
            raise NotApplicable("the reviewer left no complete suggestion to apply")
        return ToolResult(params={"applied": applied}, edited_packet_ids=edited)

    # --- glossary ---------------------------------------------------------------------------

    def _term_entry(self, source_term: str):
        key = source_term_key(source_term)
        return next(
            (
                entry
                for entry in GlossaryService(self.session).list_document_entries(self.ctx.document_id)
                if source_term_key(entry.source_term) == key
            ),
            None,
        )

    def _settle_term(self, source_term: str, rendering: str, *, drop: Sequence[str] = ()) -> dict[str, Any]:
        """The book's rendering of the term in the glossary, kept at its lock level (PREFERRED if new).

        The renderings the book rejects (``drop``: the minority of the pilot) leave the entry; every
        other accepted form stays (an acronym kept in English, "LLM", is still accepted).
        """
        entry = self._term_entry(source_term)
        level = entry.lock_level if entry is not None and entry.lock_level in (LockLevel.LOCKED, LockLevel.PREFERRED) else LockLevel.PREFERRED
        term_type = entry.term_type if entry is not None else None
        kwargs = {"term_type": term_type} if term_type is not None else {}
        previous = [entry.target_term, *(entry.target_variants_json or [])] if entry is not None else []
        variants = [form for form in dict.fromkeys(str(value) for value in previous if value) if form != rendering and form not in set(drop)]
        written = GlossaryService(self.session).lock_term(
            self.ctx.document_id, entry.source_term if entry is not None else source_term, rendering, target_variants=variants, lock_level=level, **kwargs
        )
        return {
            "source_term": written.source_term,
            "target_term": written.target_term,
            "target_variants": list(written.target_variants_json or []),
            "lock_level": written.lock_level.value,
            "previous_target": entry.target_term if entry is not None else None,
            "previous_variants": list(entry.target_variants_json or []) if entry is not None else [],
        }

    def _sentences_with_rendering(self, source_term: str, renderings: Sequence[str]) -> dict[str, list[str]]:
        """Pilot sentences whose source has the term, by the rendering their translation uses: rendering -> packet ids."""
        index = SourceTermIndex([source_term])
        sentences = self.session.execute(
            select(Sentence.id, Sentence.source_text).where(
                Sentence.chapter_id.in_(list(self.ctx.chapter_ids)),
                Sentence.retired_by_revision_id.is_(None),
                Sentence.translatable.is_(True),
            )
        ).all()
        with_term = [sentence_id for sentence_id, text in sentences if index.find(text or "")]
        targets = active_target_texts(self.session, with_term)
        packets_by_rendering: dict[str, list[str]] = {}
        for sentence_id in with_term:
            used = pilot_qa._renderings_used(targets.get(sentence_id, ""), list(renderings))
            if not used:
                continue
            packet = self.session.scalar(
                select(PacketSentenceMap.packet_id).where(
                    PacketSentenceMap.sentence_id == sentence_id, PacketSentenceMap.role == PacketSentenceRole.CURRENT
                )
            )
            if packet:
                packets_by_rendering.setdefault(used[0], []).append(str(packet))
        return {rendering: list(dict.fromkeys(packets)) for rendering, packets in packets_by_rendering.items()}

    def _term_variant_plan(self, finding: dict[str, Any]) -> tuple[str, str, list[str]]:
        evidence = finding.get("evidence") or {}
        renderings = evidence.get("renderings") or {}
        source_term = str(evidence.get("source_term") or "")
        if not source_term or len(renderings) < 2:
            raise NotApplicable("the finding does not list the renderings in use")
        majority = max(renderings.items(), key=lambda item: (int(item[1]), len(item[0])))[0]
        return source_term, majority, [rendering for rendering in renderings if rendering != majority]

    def _glossary__lock_majority_and_retranslate(self, item: WorkItem) -> ToolResult:
        (finding,) = item.findings[:1]
        source_term, majority, minority = self._term_variant_plan(finding)
        settled = self._settle_term(source_term, majority, drop=minority)
        by_rendering = self._sentences_with_rendering(source_term, [majority, *minority])
        packets = list(dict.fromkeys(packet for rendering in minority for packet in by_rendering.get(rendering, [])))
        if not packets:
            raise NotApplicable("no pilot sentence uses the other rendering")
        hint = f"术语「{source_term}」全书统一译为「{majority}」，不要用「{'」「'.join(minority)}」。"
        done = self.retranslate(packets, [hint])
        return ToolResult(params={**settled, "minority": minority, "packet_ids": packets}, packet_ids=done)

    def _glossary__lock_majority_and_unify(self, item: WorkItem) -> ToolResult:
        (finding,) = item.findings[:1]
        source_term, majority, minority = self._term_variant_plan(finding)
        settled = self._settle_term(source_term, majority, drop=minority)
        by_rendering = self._sentences_with_rendering(source_term, [majority, *minority])
        packets = list(dict.fromkeys(packet for rendering in minority for packet in by_rendering.get(rendering, [])))
        if not packets:
            raise NotApplicable("no pilot sentence uses the other rendering")
        index = SourceTermIndex([source_term])
        sources = dict(
            self.session.execute(select(Sentence.id, Sentence.source_text).where(Sentence.chapter_id.in_(list(self.ctx.chapter_ids)))).all()
        )

        def transform(text: str, sentence_ids: list[str]) -> str:
            # Only where the segment translates a sentence that has the term.
            if not any(index.find(sources.get(sentence_id) or "") for sentence_id in sentence_ids):
                return text
            for rendering in minority:
                text = fixes.replace_rendering(text, rendering, majority)
            return text

        changed = self._rewrite(packets, transform, f"术语「{source_term}」统一为「{majority}」")
        return ToolResult(params={**settled, "minority": minority}, edited_packet_ids=changed)

    def _glossary__lock_and_retranslate(self, item: WorkItem) -> ToolResult:
        """A term sometimes left in English: its rendering settled, the sentences that kept English retranslated."""
        (finding,) = item.findings[:1]
        evidence = finding.get("evidence") or {}
        source_term = str(evidence.get("source_term") or "")
        entry = self._term_entry(source_term) if source_term else None
        if entry is None or not entry.target_term:
            raise NotApplicable("the term has no rendering in the glossary")
        settled = self._settle_term(entry.source_term, entry.target_term)
        packets = self._finding_packets(finding)
        hint = f"术语「{entry.source_term}」译为「{entry.target_term}」，不要留英文。"
        done = self.retranslate(packets, [hint])
        return ToolResult(params={**settled, "packet_ids": packets}, packet_ids=done)

    def _label_plan(self, finding: dict[str, Any]) -> tuple[str, str, list[str]]:
        evidence = finding.get("evidence") or {}
        label = str(evidence.get("label") or "")
        renderings = evidence.get("renderings") or {}
        if not label or len(renderings) < 2:
            raise NotApplicable("the finding does not list the label's names")
        wanted = max(renderings.items(), key=lambda item: (int(item[1]), len(item[0])))[0]
        return label, wanted, [name for name in renderings if name != wanted]

    def _label_packets(self, label: str) -> tuple[list[str], dict[str, list[str]]]:
        """Pilot packets whose source names a numbered label of this kind, and the numbers per sentence."""
        pattern = pilot_qa._LABEL_RE
        numbers_by_sentence: dict[str, list[str]] = {}
        rows = self.session.execute(
            select(Sentence.id, Sentence.source_text).where(
                Sentence.chapter_id.in_(list(self.ctx.chapter_ids)), Sentence.retired_by_revision_id.is_(None)
            )
        ).all()
        for sentence_id, text in rows:
            numbers = [
                match.group(2)
                for match in pattern.finditer(text or "")
                if ("figure" if match.group(1).lower().startswith("fig") else match.group(1).lower()) == label
            ]
            if numbers:
                numbers_by_sentence[str(sentence_id)] = numbers
        packets = self.session.execute(
            select(PacketSentenceMap.packet_id).where(
                PacketSentenceMap.sentence_id.in_(list(numbers_by_sentence) or ["-"]),
                PacketSentenceMap.role == PacketSentenceRole.CURRENT,
            )
        ).all()
        return list(dict.fromkeys(str(packet) for (packet,) in packets)), numbers_by_sentence

    def _glossary__unify_label_rendering(self, item: WorkItem) -> ToolResult:
        (finding,) = item.findings[:1]
        label, wanted, others = self._label_plan(finding)
        packets, numbers_by_sentence = self._label_packets(label)

        def transform(text: str, sentence_ids: list[str]) -> str:
            numbers = [number for sentence_id in sentence_ids for number in numbers_by_sentence.get(sentence_id, [])]
            return fixes.unify_label(text, numbers, wanted, others) if numbers else text

        changed = self._rewrite(packets, transform, f"「{label.capitalize()} N」统一译为「{wanted} N」")
        self._record_book_decision(
            f"book.style.label.{label}",
            f"「{label.capitalize()} N」统一译为「{wanted} N」。",
            "试译质检：前两章多数处的译法",
        )
        return ToolResult(params={"label": label, "wanted": wanted, "others": others}, edited_packet_ids=changed)

    def _glossary__lock_label_and_retranslate(self, item: WorkItem) -> ToolResult:
        (finding,) = item.findings[:1]
        label, wanted, others = self._label_plan(finding)
        packets, numbers_by_sentence = self._label_packets(label)
        targets = active_target_texts(self.session, list(numbers_by_sentence))
        affected_sentences = [
            sentence_id
            for sentence_id in numbers_by_sentence
            if any(re.search(rf"(?<!{re.escape(wanted[:-len(other)]) if wanted.endswith(other) else '^'}){re.escape(other)}\s*\d", targets.get(sentence_id, "")) for other in others)
        ]
        affected_packets = self.session.execute(
            select(PacketSentenceMap.packet_id).where(
                PacketSentenceMap.sentence_id.in_(affected_sentences or ["-"]), PacketSentenceMap.role == PacketSentenceRole.CURRENT
            )
        ).all()
        packets = list(dict.fromkeys(str(packet) for (packet,) in affected_packets))
        if not packets:
            raise NotApplicable("no pilot sentence names the label the other way")
        self._record_book_decision(f"book.style.label.{label}", f"「{label.capitalize()} N」统一译为「{wanted} N」。", "试译质检")
        done = self.retranslate(packets, [f"「{label.capitalize()} N」统一译为「{wanted} N」。"])
        return ToolResult(params={"label": label, "wanted": wanted, "packet_ids": packets}, packet_ids=done)

    def _record_book_decision(self, key: str, text: str, rationale: str) -> None:
        AgentLedgerRepository(self.session).record_decision(
            document_id=self.ctx.document_id,
            scope=DecisionScope.BOOK,
            key=key,
            value={"text": text},
            rationale=rationale,
            decided_by=ACTOR_ID,
        )

    # --- structure_edit ----------------------------------------------------------------------

    def _structure_edit__relabel_as_paragraph(self, item: WorkItem) -> ToolResult:
        block = self._finding_block(item.findings[0])
        if block.block_type == BlockType.PARAGRAPH:
            raise NotApplicable("the block is already a paragraph")
        outcome = self.structure.relabel_block(
            self.ctx.document_id, block.id, BlockType.PARAGRAPH.value, actor_id=ACTOR_ID, reason=str(item.findings[0].get("message"))
        )
        done = self._retranslate_fork(outcome, self._hints(item.findings))
        return ToolResult(params={"block_id": block.id, "from": outcome.kind}, block_ids=outcome.block_ids, packet_ids=done)

    def _structure_edit__relabel_as_heading(self, item: WorkItem) -> ToolResult:
        """A section heading kept as a paragraph becomes a heading of the level the check read off
        its numbering ("1.2.1" is level 3; "Summary" is level 2)."""
        finding = item.findings[0]
        block = self._finding_block(finding)
        if block.block_type == BlockType.HEADING:
            raise NotApplicable("the block is already a heading: the export sets it as a paragraph")
        level = int((finding.get("evidence") or {}).get("heading_level") or 2)
        outcome = self.structure.relabel_block(
            self.ctx.document_id,
            block.id,
            BlockType.HEADING.value,
            heading_level=max(1, min(level, 6)),
            actor_id=ACTOR_ID,
            reason=f"小标题「{(block.source_text or '').strip()[:60]}」被排成了正文",
        )
        done = self._retranslate_fork(outcome, [f"「{(block.source_text or '').strip()[:60]}」是小标题，译成简短的标题。"])
        return ToolResult(params={"block_id": block.id, "heading_level": level}, block_ids=outcome.block_ids, packet_ids=done)

    def _structure_edit__relabel_as_code(self, item: WorkItem) -> ToolResult:
        block = self._finding_block(item.findings[0])
        outcome = self.structure.relabel_block(
            self.ctx.document_id, block.id, BlockType.CODE.value, actor_id=ACTOR_ID, reason=str(item.findings[0].get("message"))
        )
        block_ids = list(outcome.block_ids)
        previous, following = self._neighbours(block)
        merged_with = None
        for first, second in ((previous, block), (block, following)):
            if first is not None and second is not None and first.block_type == BlockType.CODE and second.block_type == BlockType.CODE:
                merge = self.structure.merge_blocks(
                    self.ctx.document_id, first.id, second.id, actor_id=ACTOR_ID, reason="代码注释并回它的代码块"
                )
                block_ids.extend(merge.block_ids)
                merged_with = second.id if first.id == block.id else first.id
                break
        return ToolResult(params={"block_id": block.id, "merged_with": merged_with}, block_ids=list(dict.fromkeys(block_ids)))

    def _pdf_callout_lines(self, lines: Sequence[LooseBlock]) -> list[str] | None:
        """The callout's lines read again from the PDF page (None without the PDF or on a mismatch)."""
        document = self.session.get(Document, self.ctx.document_id)
        source = getattr(document, "source_path", None)
        if not source or not Path(source).is_file() or not str(source).lower().endswith(".pdf"):
            return None
        try:
            import fitz

            pdf = fitz.open(source)
        except Exception:  # pragma: no cover - an unreadable file only loses the page's line breaks
            logger.warning("pilot repair: cannot open %s", source, exc_info=True)
            return None
        try:
            return pdf_callout_lines(pdf, lines)
        finally:
            pdf.close()

    def _structure_edit__merge_icon_callout(self, item: WorkItem) -> ToolResult:
        """An icon block and the loose lines beside it become one callout block.

        The lines are found again on the chapter's blocks (``callouts.split_callouts``: set at the
        icon's right in a non-body face), merged with a structure edit, and the callout's packets are
        retranslated line by line. A code callout (a JSON object, a string assigned to a name) stays
        code and is not translated.
        """
        finding = item.findings[0]
        evidence = finding.get("evidence") or {}
        icon = self._block(evidence.get("icon_block_id") or (finding.get("location") or {}).get("block_id"))
        if icon.block_type not in {BlockType.IMAGE, BlockType.FIGURE}:
            raise NotApplicable(f"a {icon.block_type.value} block is not an icon")
        loose = []
        for block in self._chapter_blocks(icon.chapter_id):
            span = block.source_span_json or {}
            if span.get("pdf_block_role") in {"header", "footer"} or span.get("repair_hidden_from_export"):
                continue
            loose.append(
                LooseBlock(
                    block_id=block.id,
                    block_type=block.block_type.value,
                    text=block.source_text or "",
                    font_names=tuple(str(name) for name in span.get("pdf_font_names") or () if name),
                    regions=tuple(parse_health.block_regions(span)),
                )
            )
        callout = next((found for found in split_callouts(loose) if found.icon.block_id == icon.id), None)
        if callout is None:
            raise NotApplicable("no lines set beside this icon: it does not lead a callout")
        line_ids = [line.block_id for line in callout.lines]
        line_texts = self._pdf_callout_lines(callout.lines)
        # The page's lines decide code or prose (an old parse's joined blocks can read either way).
        kind = (CALLOUT_KIND_CODE if looks_like_code_callout(line_texts) else CALLOUT_KIND_PROSE) if line_texts else callout.kind
        outcome = self.structure.merge_callout(
            self.ctx.document_id,
            icon.id,
            line_ids,
            line_texts=line_texts,
            actor_id=ACTOR_ID,
            reason=f"图标和它后面的 {len(line_ids)} 行是一个{'代码' if kind == CALLOUT_KIND_CODE else '提示'}框",
        )
        packets: list[str] = []
        if kind != CALLOUT_KIND_CODE:
            fork_packets = list(getattr(outcome.fork, "retranslate_packet_ids", []) or []) if outcome.fork else []
            packets = list(dict.fromkeys([*fork_packets, *self._packets_of_blocks([line_ids[0]])]))
        done = self.retranslate(
            packets,
            [
                "这是书中的一段示例提示词（放在带图标的提示框里）：每一行单独翻译成中文，保持原来的分行和列表符号，"
                "不要把几行并成一段；{query} 这类占位符、代码标识符、JSON 键名原样保留。"
            ],
        )
        return ToolResult(
            params={
                "icon_block_id": icon.id,
                "line_block_ids": line_ids,
                "callout_kind": kind,
                "lines_from_page": line_texts is not None,
            },
            block_ids=outcome.block_ids,
            packet_ids=done,
        )

    def _structure_edit__relabel_table_as_code(self, item: WorkItem) -> ToolResult:
        """A listing typed as a table becomes code; the export gate then checks the chapter again,
        so the table it could not draw is no longer an open issue."""
        block = self._finding_block(item.findings[0])
        if block.block_type != BlockType.TABLE:
            raise NotApplicable(f"a {block.block_type.value} block is not a table")
        outcome = self.structure.relabel_block(
            self.ctx.document_id, block.id, BlockType.CODE.value, actor_id=ACTOR_ID, reason="代码被识别成了表格，改为代码块"
        )
        self.session.flush()
        self._recheck_export_gate([block.chapter_id])
        return ToolResult(params={"block_id": block.id, "from": BlockType.TABLE.value}, block_ids=list(outcome.block_ids))

    def _recheck_export_gate(self, chapter_ids: Sequence[str]) -> None:
        workflow = self.ctx.workflow()
        for chapter_id in dict.fromkeys(chapter_ids):
            workflow._revalidate_export_gate(chapter_id)
        self.session.flush()

    # --- review_recheck ------------------------------------------------------------------------

    def _review_recheck__rerun_detectors(self, item: WorkItem) -> ToolResult:
        """Run the detector that opened each finding's review issue again: the export gate for its
        issues, the rule review pass for its own. What they no longer find is resolved; an issue
        they still find stays (and the finding with it). Model and human issues are not theirs."""
        from book_agent.domain.enums import Detector, IssueStatus
        from book_agent.infra.repositories.review import ReviewRepository, is_export_gate_issue
        from book_agent.services.review import ReviewService

        issue_ids = [str((finding.get("evidence") or {}).get("issue_id") or "") for finding in item.findings]
        issues = [self.session.get(ReviewIssue, issue_id) for issue_id in dict.fromkeys(issue_ids) if issue_id]
        issues = [
            issue
            for issue in issues
            if issue is not None
            and issue.document_id == self.ctx.document_id
            and issue.status in (IssueStatus.OPEN, IssueStatus.TRIAGED)
        ]
        if not issues:
            raise NotApplicable("no open review issue behind these findings")
        gate_chapters = [str(issue.chapter_id) for issue in issues if issue.chapter_id and is_export_gate_issue(issue)]
        review_chapters = [
            str(issue.chapter_id)
            for issue in issues
            if issue.chapter_id and not is_export_gate_issue(issue) and issue.detector == Detector.RULE
        ]
        if not gate_chapters and not review_chapters:
            raise NotApplicable("the issues were opened by a person or the Reviewer Agent: no detector to rerun")
        if review_chapters:
            review = ReviewService(ReviewRepository(self.session))
            for chapter_id in dict.fromkeys(review_chapters):
                review.review_chapter(chapter_id)
            self.session.flush()
        if gate_chapters:
            self._recheck_export_gate(gate_chapters)
        return ToolResult(
            params={
                "issue_ids": [issue.id for issue in issues],
                "review_chapter_ids": list(dict.fromkeys(review_chapters)),
                "gate_chapter_ids": list(dict.fromkeys(gate_chapters)),
            }
        )

    def _hide(self, block: Block, reason: str) -> None:
        self.structure.override_metadata(
            self.ctx.document_id, block.id, {"repair_hidden_from_export": True}, actor_id=ACTOR_ID, reason=reason
        )

    def _structure_edit__hide_duplicated_label(self, item: WorkItem) -> ToolResult:
        block = self._finding_block(item.findings[0])
        if block.block_type not in {BlockType.PARAGRAPH, BlockType.LIST_ITEM, BlockType.CAPTION, BlockType.HEADING, BlockType.QUOTE}:
            raise NotApplicable(f"a {block.block_type.value} block is not a label to hide")
        self._hide(block, "图中的标注，不作为正文重复出现")
        return ToolResult(params={"block_id": block.id}, block_ids=[block.id])

    def _structure_edit__hide_figure_residue(self, item: WorkItem) -> ToolResult:
        """A figure's own text kept as a table, code or equation block leaves the reading copy: the
        figure's picture already shows it."""
        block = self._finding_block(item.findings[0])
        if block.block_type not in {BlockType.TABLE, BlockType.CODE, BlockType.EQUATION}:
            raise NotApplicable(f"a {block.block_type.value} block is not a figure's residue")
        self._hide(block, "图里的文字，图片里已经有，不作为表格或代码重复出现")
        return ToolResult(params={"block_id": block.id, "block_type": block.block_type.value}, block_ids=[block.id])

    # --- figures without captions, sparse tables ---------------------------------------------------

    @staticmethod
    def _regions(block: Block) -> list[tuple[int, tuple[float, float, float, float]]]:
        return parse_health.block_regions(dict(block.source_span_json or {}))

    def _is_caption_like(self, block: Block) -> bool:
        if (block.source_span_json or {}).get("repair_hidden_from_export"):
            return False
        text = " ".join((block.source_text or "").split())
        label = pilot_qa._FIGURE_CAPTION_RE.match(text)
        return block.block_type == BlockType.CAPTION or bool(label and not re.match(r"\s*[a-z]", text[label.end() :]))

    def _structure_edit__link_adjacent_caption(self, item: WorkItem) -> ToolResult:
        """A figure without a caption, with a caption block right before or after it on its page: link them."""
        figure = self._finding_block(item.findings[0])
        if figure.block_type not in {BlockType.FIGURE, BlockType.IMAGE}:
            raise NotApplicable(f"a {figure.block_type.value} block is not a figure")
        page = (figure.source_span_json or {}).get("source_page_start")
        linked = {
            str((b.source_span_json or {}).get("linked_caption_block_id"))
            for b in self._chapter_blocks(figure.chapter_id)
            if (b.source_span_json or {}).get("linked_caption_block_id")
        }
        previous, following = self._neighbours(figure)
        caption = next(
            (
                b
                for b in (following, previous)
                if b is not None
                and b.id not in linked
                and (b.source_span_json or {}).get("source_page_start") == page
                and self._is_caption_like(b)
            ),
            None,
        )
        if caption is None:
            raise NotApplicable("no unlinked caption right next to the figure on its page")
        if caption.block_type != BlockType.CAPTION:
            self.structure.relabel_block(self.ctx.document_id, caption.id, BlockType.CAPTION.value, actor_id=ACTOR_ID, reason="图注")
        self.structure.link_caption(self.ctx.document_id, caption.id, figure.id, actor_id=ACTOR_ID, reason="紧挨着图的图注配给这张图")
        return ToolResult(params={"figure_block_id": figure.id, "caption_block_id": caption.id}, block_ids=[figure.id, caption.id])

    def _structure_edit__accept_uncaptioned_figure(self, item: WorkItem) -> ToolResult:
        """A figure whose page has no caption for it (an icon, a decorative image, an unlabelled diagram) is fine."""
        figure = self._finding_block(item.findings[0])
        if figure.block_type not in {BlockType.FIGURE, BlockType.IMAGE}:
            raise NotApplicable(f"a {figure.block_type.value} block is not a figure")
        page = (figure.source_span_json or {}).get("source_page_start")
        linked = {
            str((b.source_span_json or {}).get("linked_caption_block_id"))
            for b in self._chapter_blocks(figure.chapter_id)
            if (b.source_span_json or {}).get("linked_caption_block_id")
        }
        unlinked = [
            b
            for b in self._chapter_blocks(figure.chapter_id)
            if b.id not in linked and (b.source_span_json or {}).get("source_page_start") == page and self._is_caption_like(b)
        ]
        if unlinked:
            raise NotApplicable("the page has a caption no figure took: it may be this figure's")
        self.structure.override_metadata(
            self.ctx.document_id, figure.id, {"figure_caption_absent": True}, actor_id=ACTOR_ID, reason="原书这张图没有图注"
        )
        return ToolResult(params={"figure_block_id": figure.id, "page": page}, block_ids=[figure.id])

    def _structure_edit__absorb_sparse_table_into_figure(self, item: WorkItem) -> ToolResult:
        """A mostly empty table inside or right next to a figure is the figure's own text.

        Inside the figure's crop: the table is hidden (the picture shows it). Next to it (touching or
        within ``ADJACENT_GAP`` points, overlapping horizontally): the figure's crop grows to take it in,
        then the table is hidden. Without a figure on its page the table is left alone.
        """
        table = self._finding_block(item.findings[0])
        if table.block_type != BlockType.TABLE:
            raise NotApplicable(f"a {table.block_type.value} block is not a table")
        regions = self._regions(table)
        if not regions:
            raise NotApplicable("the table has no region on its page")
        page, box = regions[0]
        figures = [
            b
            for b in self._chapter_blocks(table.chapter_id)
            if b.block_type in {BlockType.FIGURE, BlockType.IMAGE}
            and any(region_page == page for region_page, _bbox in self._regions(b))
            and parse_health.image_fragment_kind(next(bbox for region_page, bbox in self._regions(b) if region_page == page)) is None
        ]

        def gap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
            dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
            dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
            return max(dx, dy)

        candidates = []
        for figure in figures:
            figure_box = next(bbox for region_page, bbox in self._regions(figure) if region_page == page)
            overlap_x = min(box[2], figure_box[2]) - max(box[0], figure_box[0])
            distance = gap(box, figure_box)
            if distance <= ADJACENT_GAP and overlap_x > 0:
                candidates.append((distance, figure, figure_box))
        if not candidates:
            raise NotApplicable("no figure on the table's page is next to it")
        _distance, figure, figure_box = min(candidates, key=lambda entry: entry[0])
        inside = box[0] >= figure_box[0] - 1 and box[1] >= figure_box[1] - 1 and box[2] <= figure_box[2] + 1 and box[3] <= figure_box[3] + 1
        grown = None
        if not inside:
            grown = [min(box[0], figure_box[0]), min(box[1], figure_box[1]), max(box[2], figure_box[2]), max(box[3], figure_box[3])]
            span = dict(figure.source_span_json or {})
            bbox_json = dict(span.get("source_bbox_json") or {})
            new_regions = []
            for region in bbox_json.get("regions") or []:
                region = dict(region)
                if region.get("page_number") == page:
                    region["bbox"] = [round(value, 2) for value in grown]
                new_regions.append(region)
            self.structure.override_metadata(
                self.ctx.document_id,
                figure.id,
                {
                    "source_bbox_json": {**bbox_json, "regions": new_regions},
                    "figure_region": FIGURE_REGION_CONTENT_EXTENT,
                    "figure_region_override": {"previous_bbox": list(figure_box), "absorbed_block_id": table.id},
                },
                actor_id=ACTOR_ID,
                reason="图旁边的文字（被当成了表格）并进图片区域",
            )
            for image in self.session.scalars(select(DocumentImage).where(DocumentImage.block_id == figure.id)).all():
                image.metadata_json = {**(image.metadata_json or {}), "storage_status": "stale", "stale_reason": "figure_region_override"}
        self._hide(table, "图里的文字，图片里已经有，不作为表格重复出现")
        return ToolResult(
            params={"table_block_id": table.id, "figure_block_id": figure.id, "inside": inside, "grown_to": grown},
            block_ids=[table.id, figure.id],
        )

    # --- lists, sidebars, split paragraphs, listing captions (the parser side's QA hints) ------------

    def _hint_blocks(self, finding: dict[str, Any], *keys: str) -> list[Block]:
        """The blocks a finding names: its evidence's id lists (in the given keys), else its own block."""
        evidence = finding.get("evidence") or {}
        ids: list[str] = []
        for key in keys:
            value = evidence.get(key)
            if isinstance(value, str) and value:
                ids.append(value)
            elif isinstance(value, (list, tuple)):
                ids.extend(str(item) for item in value if item)
        if not ids:
            return [self._finding_block(finding)]
        blocks = [self._block(block_id) for block_id in dict.fromkeys(ids)]
        return sorted(blocks, key=lambda block: block.ordinal)

    def _fork_packets(self, outcomes: Sequence[Any]) -> list[str]:
        return list(
            dict.fromkeys(
                packet for outcome in outcomes for packet in (getattr(getattr(outcome, "fork", None), "retranslate_packet_ids", []) or [])
            )
        )

    def _split_inline_items(self, parent: Block, items: Sequence[str]) -> tuple[list[Block], list[Any]]:
        """Sub-items set inside their bullet's text, one per line: each split off as its own block."""
        outcomes: list[Any] = []
        blocks: list[Block] = []
        current = parent
        for line in items:
            text = current.source_text or ""
            at = text.find(line.strip()[:40])
            if at <= 0:
                raise NotApplicable(f"cannot find the sub-item {line[:30]!r} in its bullet")
            outcome = self.structure.split_block(self.ctx.document_id, current.id, _marker(text, at), actor_id=ACTOR_ID, reason="子项从上一项里拆出来")
            outcomes.append(outcome)
            current = self.session.get(Block, outcome.block_ids[-1])
            blocks.append(current)
        return blocks, outcomes

    def _structure_edit__nest_list_items(self, item: WorkItem) -> ToolResult:
        """Sub-items ("– Basic data structures") the parse kept flat: drawn as a nested list.

        Loose sub-item paragraphs after the list (evidence ``item_block_ids``) become level-2 items;
        sub-items set inside their bullet's text (``inline_items``) are split off first.
        """
        finding = item.findings[0]
        evidence = finding.get("evidence") or {}
        level = int(evidence.get("list_level") or 2)
        outcomes: list[Any] = []
        inline = [str(line) for line in evidence.get("inline_items") or [] if str(line).strip()]
        if inline and not evidence.get("item_block_ids"):
            parent = self._block(str(evidence.get("parent_block_id") or (finding.get("target") or {}).get("id") or ""))
            blocks, outcomes = self._split_inline_items(parent, inline)
        else:
            blocks = self._hint_blocks(finding, "item_block_ids", "block_ids")
        changed: list[dict[str, Any]] = []
        for block in blocks:
            if block.block_type not in {BlockType.LIST_ITEM, BlockType.PARAGRAPH}:
                raise NotApplicable(f"a {block.block_type.value} block is not a list item")
            text = (block.source_text or "").lstrip()
            kind = str(evidence.get("list_kind") or "") or (
                "dash" if re.match(r"[\u2013\u2014-]\s", text) else "number" if re.match(r"\d{1,3}(?:[.)]\s*|\s+)\S", text) else ""
            )
            if block.block_type == BlockType.PARAGRAPH:
                outcomes.append(
                    self.structure.relabel_block(self.ctx.document_id, block.id, BlockType.LIST_ITEM.value, actor_id=ACTOR_ID, reason="列表的子项")
                )
            updates: dict[str, Any] = {"list_level": level}
            if kind:
                updates["list_kind"] = kind
            span = self.session.get(Block, block.id).source_span_json or {}
            if all(span.get(key) == value for key, value in updates.items()) and block.block_type == BlockType.LIST_ITEM:
                continue
            self.structure.override_metadata(self.ctx.document_id, block.id, updates, actor_id=ACTOR_ID, reason="列表子项按层级缩进")
            changed.append({"block_id": block.id, **updates})
        if not changed:
            raise NotApplicable("the items are already nested")
        done = self.retranslate(self._fork_packets(outcomes), [])
        return ToolResult(params={"items": changed}, block_ids=[entry["block_id"] for entry in changed], packet_ids=done)

    def _structure_edit__relabel_as_sidebar(self, item: WorkItem) -> ToolResult:
        """A sidebar's blocks drawn as one titled box; its title is not a section heading of the chapter."""
        finding = item.findings[0]
        evidence = finding.get("evidence") or {}
        blocks = self._hint_blocks(finding, "member_block_ids", "sidebar_block_ids", "block_ids")
        title_id = str(evidence.get("title_block_id") or (finding.get("target") or {}).get("id") or blocks[0].id)
        title = next((block for block in blocks if block.id == title_id), None) or self._block(title_id)
        sidebar_id = str(evidence.get("sidebar_id") or title.id)
        outcomes: list[Any] = []
        if title.block_type == BlockType.HEADING:
            outcomes.append(
                self.structure.relabel_block(
                    self.ctx.document_id, title.id, BlockType.PARAGRAPH.value, actor_id=ACTOR_ID, reason="侧栏的标题，不是本章的小节标题"
                )
            )
        for block in dict.fromkeys([title, *blocks]):
            updates: dict[str, Any] = {"sidebar_id": sidebar_id}
            if block.id == title.id:
                updates["sidebar_title"] = True
            self.structure.override_metadata(self.ctx.document_id, block.id, updates, actor_id=ACTOR_ID, reason="侧栏排成一个带标题的框")
        done = self.retranslate(self._fork_packets(outcomes), [])
        return ToolResult(
            params={"sidebar_id": sidebar_id, "title_block_id": title.id, "block_ids": [block.id for block in blocks]},
            block_ids=list(dict.fromkeys([title.id, *[block.id for block in blocks]])),
            packet_ids=done,
        )

    def _structure_edit__join_continuation(self, item: WorkItem) -> ToolResult:
        """A paragraph the page cut (at a figure or a page break): its two parts become one block again."""
        finding = item.findings[0]
        evidence = finding.get("evidence") or {}
        if evidence.get("join_into_block_id"):
            # The finding is on the second half; the first half is where it joins.
            first = self._block(str(evidence["join_into_block_id"]))
            second_id = evidence.get("block_id") or (finding.get("target") or {}).get("id")
        else:
            first = self._finding_block(finding)
            second_id = evidence.get("continuation_block_id") or evidence.get("next_block_id")
        if second_id:
            second = self._block(str(second_id))
        else:
            following = [block for block in self._chapter_blocks(first.chapter_id) if block.ordinal > first.ordinal]
            second = next(
                (
                    block
                    for block in following
                    if block.block_type == first.block_type
                    and not (block.source_span_json or {}).get("repair_hidden_from_export")
                    and (block.source_span_json or {}).get("pdf_block_role") not in {"header", "footer"}
                ),
                None,
            )
            if second is None:
                raise NotApplicable("no continuation after the block")
        outcome = self.structure.merge_blocks(
            self.ctx.document_id, first.id, second.id, actor_id=ACTOR_ID, reason="被图或换页截断的段落接回一段", across_artifacts=True
        )
        done = self.retranslate(self._fork_packets([outcome]), ["这是一个完整的段落（原来被图或换页截断），译成连贯的一段。"])
        return ToolResult(params={"first_block_id": first.id, "second_block_id": second.id}, block_ids=list(outcome.block_ids), packet_ids=done)

    def _structure_edit__link_listing_caption(self, item: WorkItem) -> ToolResult:
        """A "Listing N.N ..." caption kept as a paragraph: made the caption of the code block it names."""
        finding = item.findings[0]
        evidence = finding.get("evidence") or {}
        caption = self._block(str(evidence.get("caption_block_id") or "")) if evidence.get("caption_block_id") else self._finding_block(finding)
        code_id = evidence.get("listing_block_id") or evidence.get("code_block_id")
        if code_id:
            code = self._block(str(code_id))
        else:
            previous, following = self._neighbours(caption)
            code = next((block for block in (following, previous) if block is not None and block.block_type == BlockType.CODE), None)
            if code is None:
                raise NotApplicable("no code block right next to the listing caption")
        if code.block_type != BlockType.CODE:
            raise NotApplicable(f"a {code.block_type.value} block is not a listing")
        if (code.source_span_json or {}).get("linked_caption_block_id") == caption.id:
            raise NotApplicable("the caption is already linked to its listing")
        outcomes: list[Any] = []
        if caption.block_type != BlockType.CAPTION:
            outcomes.append(
                self.structure.relabel_block(self.ctx.document_id, caption.id, BlockType.CAPTION.value, actor_id=ACTOR_ID, reason="代码清单的标题")
            )
        self.structure.link_caption(self.ctx.document_id, caption.id, code.id, actor_id=ACTOR_ID, reason="代码清单标题配给它的代码")
        done = self.retranslate(self._fork_packets(outcomes), [])
        return ToolResult(params={"caption_block_id": caption.id, "listing_block_id": code.id}, block_ids=[caption.id, code.id], packet_ids=done)

    def _heading_titles(self, chapter_id: str) -> list[str]:
        titles = [block.source_text for block in self._chapter_blocks(chapter_id) if block.block_type == BlockType.HEADING]
        chapters = self.session.scalars(select(Chapter.title_src).where(Chapter.document_id == self.ctx.document_id)).all()
        titles.extend(title for title in chapters if title)
        normalized = {_norm(title) for title in titles} | {_norm(pilot_qa._SECTION_NUMBER_RE.sub("", title or "")) for title in titles}
        return sorted((title for title in normalized if len(title) >= 6), key=len, reverse=True)

    def _structure_edit__hide_running_header(self, item: WorkItem) -> ToolResult:
        block = self._finding_block(item.findings[0])
        text = block.source_text or ""
        match = pilot_qa._LEADING_NUMBER_RE.match(text.strip())
        if not match:
            raise NotApplicable("the block does not start with a page number")
        rest_raw = match.group(2)
        rest = _norm(rest_raw)
        title = next((title for title in self._heading_titles(block.chapter_id) if rest == title or rest.startswith(title + " ")), None)
        if title is None:
            raise NotApplicable("no chapter or section title matches the header")
        if rest == title:
            self._hide(block, f"页眉「{text.strip()[:60]}」")
            return ToolResult(params={"block_id": block.id, "header": text.strip()[:80], "split": False}, block_ids=[block.id])
        # The header runs into the text after it: split it off, then hide it.
        words = rest_raw.split()
        body_start = None
        for count in range(1, len(words) + 1):
            if _norm(" ".join(words[:count])) == title:
                body_start = " ".join(words[count:])
                break
        if not body_start:
            raise NotApplicable("cannot tell where the header ends")
        marker = body_start[:80]
        outcome = self.structure.split_block(
            self.ctx.document_id, block.id, marker, actor_id=ACTOR_ID, reason="把页眉从正文里拆出来"
        )
        header_block = self.session.get(Block, block.id)
        self._hide(header_block, f"页眉「{(header_block.source_text or '')[:60]}」")
        done = self._retranslate_fork(outcome, [])
        return ToolResult(
            params={"block_id": block.id, "header": (header_block.source_text or "")[:80], "split": True},
            block_ids=outcome.block_ids,
            packet_ids=done,
        )

    def _structure_edit__split_run_in_heading(self, item: WorkItem) -> ToolResult:
        """Run-in subheadings out of their paragraph, each its own heading block.

        The parser keeps a run-in heading as a line of the paragraph ("... over time.\\nhow the agent
        loop works\\nThe loop is necessary ..."): a short line that starts in lowercase, after a line
        that ends a sentence (or at the block's start), before a line that starts a sentence. Every
        such line of the block is split out. A text without line breaks falls back to the glue the
        check saw ("...considering.cost optimization When ...").
        """
        finding = item.findings[0]
        block = self._finding_block(finding)
        # A run-in heading sits below the chapter's numbered sections ("1.2.1 ..." is level 3, so 4).
        numbered_levels = [
            int((b.source_span_json or {}).get("heading_level") or 0)
            for b in self._chapter_blocks(block.chapter_id)
            if b.block_type == BlockType.HEADING
            and (b.source_span_json or {}).get("heading_level")
            and pilot_qa._SECTION_NUMBER_RE.match(b.source_text or "")
        ]
        level = min(6, max(numbered_levels) + 1) if numbered_levels else 3
        outcomes: list[Any] = []
        headings: list[str] = []
        heading_ids: list[str] = []
        current: Block | None = block
        if _run_in_heading_line(block.source_text or "") is None and _glued_heading(block.source_text or "") is None:
            # The reading copy joined this paragraph with the next one, which opens with the heading.
            _previous, following = self._neighbours(block)
            opening = _run_in_heading_line(following.source_text or "") if following is not None else None
            if following is not None and following.block_type in {BlockType.PARAGRAPH, BlockType.LIST_ITEM} and opening and opening[0] == 0:
                current = following
        while current is not None:
            text = current.source_text or ""
            spot = _run_in_heading_line(text) or _glued_heading(text)
            if spot is None:
                break
            start, end = spot
            heading = text[start:end].strip()
            body = text[end:].strip()
            if start > 0:
                # Split the heading (and what follows) off the text before it.
                split = self.structure.split_block(
                    self.ctx.document_id, current.id, _marker(text, start), actor_id=ACTOR_ID, reason=f"拆出小标题「{heading}」"
                )
                outcomes.append(split)
                heading_block = self.session.get(Block, split.block_ids[-1])
            else:
                heading_block = current
            rest: Block | None = None
            if body:
                heading_text = heading_block.source_text or ""
                body_at = heading_text.find(body[:40])
                if body_at <= 0:
                    raise NotApplicable("cannot find where the text after the heading starts")
                split = self.structure.split_block(
                    self.ctx.document_id, heading_block.id, _marker(heading_text, body_at), actor_id=ACTOR_ID, reason=f"小标题「{heading}」后面的正文"
                )
                outcomes.append(split)
                rest = self.session.get(Block, split.block_ids[-1])
            outcomes.append(
                self.structure.relabel_block(
                    self.ctx.document_id, heading_block.id, BlockType.HEADING.value, heading_level=level, actor_id=ACTOR_ID, reason="行内小标题"
                )
            )
            headings.append(heading)
            heading_ids.append(heading_block.id)
            current = rest
            if len(headings) >= 8:
                break
        if not headings:
            raise NotApplicable("cannot tell where the run-in heading ends")
        packets: list[str] = []
        block_ids: list[str] = [block.id]
        for outcome in outcomes:
            packets.extend(getattr(outcome.fork, "retranslate_packet_ids", []) or [])
            block_ids.extend(outcome.block_ids)
        hint = "、".join(f"「{heading}」" for heading in headings)
        done = self.retranslate(list(dict.fromkeys(packets)), [f"{hint}是小标题，单独成行，不要和正文连在一起。"])
        return ToolResult(
            params={"block_id": block.id, "heading": headings[0], "headings": headings, "heading_block_ids": heading_ids, "heading_level": level},
            block_ids=list(dict.fromkeys(block_ids)),
            packet_ids=done,
        )

    def _caption_block(self, block: Block) -> Block:
        if block.block_type == BlockType.CAPTION:
            return block
        caption_id = (block.source_span_json or {}).get("linked_caption_block_id")
        caption = self.session.get(Block, caption_id) if caption_id else None
        if caption is None or caption.status != ArtifactStatus.ACTIVE:
            raise NotApplicable("the figure has no caption block")
        return caption

    def _structure_edit__split_caption(self, item: WorkItem) -> ToolResult:
        figure = self._finding_block(item.findings[0])
        caption = self._caption_block(figure)
        text = " ".join((caption.source_text or "").split())
        sentences = [part for part in pilot_qa._SENTENCE_SPLIT_RE.split(text) if part.strip()]
        if len(sentences) < 2:
            raise NotApplicable("the caption is one sentence")
        # The caption proper: its label sentence ("Figure 1.2 The RAG pipeline."); what follows is body text.
        keep = 1
        second = sentences[keep]
        marker = second[:80]
        outcome = self.structure.split_block(
            self.ctx.document_id, caption.id, marker, actor_id=ACTOR_ID, reason="图注后面的正文从图注里拆出来"
        )
        body_id = outcome.block_ids[-1]
        relabel = self.structure.relabel_block(
            self.ctx.document_id, body_id, BlockType.PARAGRAPH.value, actor_id=ACTOR_ID, reason="拆出的正文"
        )
        self.structure.override_metadata(
            self.ctx.document_id, body_id, {"artifact_group_context_excluded": True}, actor_id=ACTOR_ID, reason="正文，不属于图"
        )
        packets = [
            *(getattr(outcome.fork, "retranslate_packet_ids", []) or []),
            *(getattr(relabel.fork, "retranslate_packet_ids", []) or []),
        ]
        done = self.retranslate(list(dict.fromkeys(packets)), [])
        return ToolResult(
            params={"caption_block_id": caption.id, "body_block_id": body_id, "caption": sentences[0][:120]},
            block_ids=[caption.id, body_id],
            packet_ids=done,
        )

    def _structure_edit__unfold_caption_context(self, item: WorkItem) -> ToolResult:
        from book_agent.domain.models import Document
        from book_agent.domain.structure.artifact_grouping import (
            is_academic_paper_document,
            resolve_artifact_group_context_ids,
        )

        figure = self._finding_block(item.findings[0])
        blocks = self._chapter_blocks(figure.chapter_id)
        document = self.session.get(Document, self.ctx.document_id)
        context = resolve_artifact_group_context_ids(blocks, academic_paper=is_academic_paper_document(document)).get(figure.id, [])
        if not context:
            raise NotApplicable("nothing is folded into this figure")
        for block_id in context:
            self.structure.override_metadata(
                self.ctx.document_id, block_id, {"artifact_group_context_excluded": True}, actor_id=ACTOR_ID, reason="正文，不属于图"
            )
        return ToolResult(params={"artifact_block_id": figure.id, "context_block_ids": context}, block_ids=list(context))

    def _structure_edit__link_caption(self, item: WorkItem) -> ToolResult:
        caption = self._finding_block(item.findings[0])
        page = (caption.source_span_json or {}).get("source_page_start")
        blocks = self._chapter_blocks(caption.chapter_id)
        linked = {str((b.source_span_json or {}).get("linked_caption_block_id")) for b in blocks}
        candidates = [
            b
            for b in blocks
            if b.block_type in {BlockType.FIGURE, BlockType.IMAGE}
            and (b.source_span_json or {}).get("source_page_start") == page
            and not (b.source_span_json or {}).get("linked_caption_block_id")
        ]
        if not candidates or caption.id in linked:
            raise NotApplicable("no uncaptioned figure on the caption's page")
        # The nearest one in reading order.
        figure = min(candidates, key=lambda b: abs(b.ordinal - caption.ordinal))
        if caption.block_type != BlockType.CAPTION:
            self.structure.relabel_block(self.ctx.document_id, caption.id, BlockType.CAPTION.value, actor_id=ACTOR_ID, reason="图注")
        self.structure.link_caption(self.ctx.document_id, caption.id, figure.id, actor_id=ACTOR_ID, reason="图注配到同一页的图")
        return ToolResult(params={"caption_block_id": caption.id, "figure_block_id": figure.id}, block_ids=[caption.id, figure.id])

    def _structure_edit__rebuild_table_cells(self, item: WorkItem) -> ToolResult:
        from book_agent.domain.structure.table_cells import split_table_candidate_line

        block = self._finding_block(item.findings[0])
        lines = [line.strip() for line in (block.source_text or "").splitlines() if line.strip()]
        rows = []
        for line in lines:
            cells = split_table_candidate_line(line) if "|" in line else None
            if cells:
                rows.append([cell.strip() for cell in cells])
        rows = [row for row in rows if not all(re.fullmatch(r":?-{3,}:?", cell or "---") for cell in row)]
        width = max((len(row) for row in rows), default=0)
        if len(rows) < 2 or width < 2 or any("|" in cell for row in rows for cell in row):
            raise NotApplicable("the table text does not split into a grid")
        rows = [row + [""] * (width - len(row)) for row in rows]
        markdown = "\n".join(
            ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
            + ["| " + " | ".join(row) + " |" for row in rows[1:]]
        )
        self.structure.override_metadata(
            self.ctx.document_id, block.id, {"table_markdown": markdown}, actor_id=ACTOR_ID, reason="按原文的竖线重建表格单元格"
        )
        return ToolResult(params={"block_id": block.id, "rows": len(rows), "columns": width}, block_ids=[block.id])

    def _pdf(self) -> Any:
        """The book's PDF, opened (None without it); the caller closes it."""
        document = self.session.get(Document, self.ctx.document_id)
        source = getattr(document, "source_path", None)
        if not source or not Path(source).is_file() or not str(source).lower().endswith(".pdf"):
            return None
        try:
            import fitz

            return fitz.open(source)
        except Exception:  # pragma: no cover - an unreadable file only loses the PDF's evidence
            logger.warning("pilot repair: cannot open %s", source, exc_info=True)
            return None

    def _structure_edit__reextract_table(self, item: WorkItem) -> ToolResult:
        """The table read again from its page with the current extractor (rows that share one cell box
        split apart) replaces the block's text; only its cells are translated again."""
        from book_agent.ingestion.pdf.extract import ruled_table_rows_at
        from book_agent.ingestion.pdf.hyphenation import inline_word_counts, rejoin_line_break_hyphens

        block = self._finding_block(item.findings[0])
        if block.block_type != BlockType.TABLE:
            raise NotApplicable(f"a {block.block_type.value} block is not a table")
        regions = self._regions(block)
        if not regions:
            raise NotApplicable("the table has no region on its page")
        page_number, bbox = regions[0]
        pdf = self._pdf()
        if pdf is None:
            raise NotApplicable("no PDF to read the table from")
        try:
            if not 1 <= page_number <= pdf.page_count:
                raise NotApplicable("the table's page is not in the PDF")
            rows = ruled_table_rows_at(pdf[page_number - 1], bbox)
            # A cell's line-break hyphen ("evalu-ation") is removed on the book's own evidence, as the parser does.
            counts = inline_word_counts(line for page in pdf for line in page.get_text("text").splitlines()) if rows else None
        finally:
            pdf.close()
        if not rows or len(rows) < 2:
            raise NotApplicable("no ruled data table at the table's place on its page")
        cells = [[rejoin_line_break_hyphens(cell, counts) if counts else cell for cell in row] for row in rows]
        table_text = "\n".join("| " + " | ".join(row) + " |" for row in cells)
        try:
            outcome = self.structure.rebuild_table(
                self.ctx.document_id, block.id, table_text, actor_id=ACTOR_ID, reason=f"按原书第 {page_number} 页重新读取表格的 {len(rows)} 行"
            )
        except StructureEditRejected as exc:
            raise NotApplicable(str(exc)) from exc
        fork_packets = list(getattr(outcome.fork, "retranslate_packet_ids", []) or []) if outcome.fork else []
        packets = list(dict.fromkeys([*fork_packets, *self._packets_of_blocks([block.id])]))
        filled, retranslated = self.translate_block_sentences(
            packets, [block.id], ["这是一张表格的单元格：每个单元格单独译成简短的中文，数字、代码标识符、模型名原样保留。"], redo_all=True
        )
        return ToolResult(
            params={"block_id": block.id, "page": page_number, "rows": len(rows), "columns": max(len(row) for row in rows), "cells_only_packets": filled},
            block_ids=[block.id],
            packet_ids=list(dict.fromkeys([*filled, *retranslated])),
        )

    def _structure_edit__hide_clipped_copy(self, item: WorkItem) -> ToolResult:
        """A block that repeats the block beside it cut short at a box edge leaves the reading copy."""
        finding = item.findings[0]
        block = self._finding_block(finding)
        original = self._block(str((finding.get("evidence") or {}).get("copy_of") or ""))
        if not pilot_qa._clipped_lines((block.source_text or "").split("\n"), (original.source_text or "").split("\n")):
            raise NotApplicable("the block is not a cut-short copy of its neighbour")
        self._hide(block, "旁边一段文字被裁掉一截的副本")
        return ToolResult(params={"block_id": block.id, "copy_of": original.id}, block_ids=[block.id])

    def _structure_edit__relabel_code_face_as_code(self, item: WorkItem) -> ToolResult:
        """A paragraph set wholly in the code face (program output, the end of a listing) becomes code."""
        block = self._finding_block(item.findings[0])
        if block.block_type not in {BlockType.PARAGRAPH, BlockType.LIST_ITEM, BlockType.QUOTE}:
            raise NotApplicable(f"a {block.block_type.value} block is not prose")
        if not parse_health.set_wholly_in_code_face((block.source_span_json or {}).get("pdf_font_names")):
            raise NotApplicable("the block is not set in the code face")
        return self._structure_edit__relabel_as_code(item)

    def _structure_edit__split_part_name(self, item: WorkItem) -> ToolResult:
        """"Part 1" whose name ran into the part's introduction: the name, read from the part's page,
        is split off the introduction and set as a heading after the label."""
        finding = item.findings[0]
        chapter_id = str((finding.get("location") or {}).get("chapter_id") or (finding.get("target") or {}).get("id") or "")
        blocks = [block for block in self._chapter_blocks(chapter_id) if (block.source_text or "").strip()]
        label = next((block for block in blocks if block.block_type == BlockType.HEADING), None)
        if label is None or not re.fullmatch(r"\s*part\s+\w+\s*", label.source_text or "", re.IGNORECASE):
            raise NotApplicable("the part has no bare 'Part N' heading")
        following = blocks[blocks.index(label) + 1 :]
        intro = following[0] if following else None
        if intro is None or intro.block_type != BlockType.PARAGRAPH:
            raise NotApplicable("no introduction paragraph after the part's label")
        page = (label.source_span_json or {}).get("source_page_start")
        name = self._part_name_on_page(int(page)) if isinstance(page, int) else None
        if not name:
            raise NotApplicable("the part's page sets no name in display type")
        text = intro.source_text or ""
        pattern = re.compile(r"\s*" + r"\s+".join(re.escape(word) for word in name.split()) + r"\s+")
        match = pattern.match(text)
        if match is None:
            raise NotApplicable("the introduction does not start with the part's name")
        rest = text[match.end():]
        marker = " ".join(rest.split()[:6])
        if marker not in text:
            marker = rest.split("\n", 1)[0].strip()
        try:
            split = self.structure.split_block(
                self.ctx.document_id, intro.id, marker, actor_id=ACTOR_ID, reason="部分的名称并进了正文第一段"
            )
            level = (label.source_span_json or {}).get("heading_level")
            relabel = self.structure.relabel_block(
                self.ctx.document_id,
                intro.id,
                BlockType.HEADING.value,
                heading_level=level if isinstance(level, int) else None,
                actor_id=ACTOR_ID,
                reason="部分的名称",
            )
        except StructureEditRejected as exc:
            raise NotApplicable(str(exc)) from exc
        # The part's running footer ("2 building your first llm agent"), read as a footnote: it repeats
        # the name, now a heading, and leaves the reading copy.
        footers = []
        name_key = _norm(name)
        for block in blocks:
            if block.block_type not in {BlockType.FOOTNOTE, BlockType.PARAGRAPH} or block.id == intro.id:
                continue
            number = re.match(r"\s*\d{1,4}\s+(.+)$", " ".join((block.source_text or "").split()))
            if number and _norm(number.group(1)) == name_key:
                self._hide(block, "部分的页脚（页码 + 部分名称）")
                footers.append(block.id)
        packets: list[str] = []
        for outcome in (split, relabel):
            packets.extend(getattr(outcome.fork, "retranslate_packet_ids", []) or [] if outcome.fork else [])
        done = self.retranslate(
            list(dict.fromkeys([*packets, *self._packets_of_blocks(split.block_ids)])),
            [f"「{name}」是这一部分（Part）的名称，单独译成简短的标题；后面的段落是这一部分的导言。"],
        )
        return ToolResult(
            params={"label_block_id": label.id, "name": name, "page": page, "hidden_footers": footers},
            block_ids=[*split.block_ids, *footers],
            packet_ids=done,
        )

    def _part_name_on_page(self, page_number: int) -> str | None:
        """The display-size lines of a part page other than its "Part N" label, joined."""
        pdf = self._pdf()
        if pdf is None:
            return None
        try:
            if not 1 <= page_number <= pdf.page_count:
                return None
            lines: list[tuple[float, str]] = []
            weights: Counter[float] = Counter()
            for block in pdf[page_number - 1].get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    sizes: Counter[float] = Counter()
                    for span in line.get("spans", []):
                        sizes[round(float(span.get("size", 0.0)), 1)] += len((span.get("text") or "").strip())
                    text = " ".join("".join(span.get("text", "") for span in line.get("spans", [])).split())
                    if text and sizes:
                        size = sizes.most_common(1)[0][0]
                        lines.append((size, text))
                        weights[size] += len(text)
        finally:
            pdf.close()
        if not weights:
            return None
        body = weights.most_common(1)[0][0]
        display = [text for size, text in lines if size >= body * 1.8 and not re.fullmatch(r"part\s+\w+", text, re.IGNORECASE)]
        name = " ".join(display).strip()
        return name or None

    def _structure_edit__agent_proposal(self, item: WorkItem) -> ToolResult:
        if self.ctx.judge is None:
            raise NotApplicable("no repair agent in this environment")
        if not getattr(self, "_consulted", False) or self._proposal is None:
            raise NotApplicable("the repair agent was not consulted")
        proposal, shown = self._proposal
        if not proposal:
            raise NotApplicable("the repair agent proposed no edit")
        kind = str(proposal.get("kind") or "")
        target = self._block(str(proposal.get("block_id") or ""))
        if target.id not in shown:
            raise NotApplicable("the agent proposed an edit outside the finding's blocks")
        packets: list[str] = []
        block_ids = [target.id]
        if kind == "split":
            outcome = self.structure.split_block(
                self.ctx.document_id, target.id, str(proposal.get("second_part_starts_with") or ""), actor_id=ACTOR_ID, reason=str(proposal.get("reason") or "agent")
            )
            block_ids = list(outcome.block_ids)
            packets.extend(getattr(outcome.fork, "retranslate_packet_ids", []) or [])
            new_type = str(proposal.get("second_part_type") or "")
            if new_type:
                relabel = self.structure.relabel_block(
                    self.ctx.document_id, block_ids[-1], new_type, actor_id=ACTOR_ID, reason=str(proposal.get("reason") or "agent")
                )
                packets.extend(getattr(relabel.fork, "retranslate_packet_ids", []) or [])
        elif kind == "relabel":
            outcome = self.structure.relabel_block(
                self.ctx.document_id, target.id, str(proposal.get("block_type") or ""), actor_id=ACTOR_ID, reason=str(proposal.get("reason") or "agent")
            )
            packets.extend(getattr(outcome.fork, "retranslate_packet_ids", []) or [])
        elif kind == "hide":
            self._hide(target, str(proposal.get("reason") or "agent"))
        else:
            raise NotApplicable(f"unknown proposal kind {kind!r}")
        done = self.retranslate(list(dict.fromkeys(packets)), [])
        return ToolResult(params={"proposal": proposal}, block_ids=block_ids, packet_ids=done, decided_by="agent")

    # --- figure_region_override -----------------------------------------------------------------

    def _figure_region_override__fit_to_figure(self, item: WorkItem) -> ToolResult:
        if self.ctx.figure_problems is None:
            raise NotApplicable("no PDF page to fit the figure to")
        block = self._finding_block(item.findings[0])
        span = dict(block.source_span_json or {})
        bbox_json = dict(span.get("source_bbox_json") or {})
        regions = [dict(region) for region in bbox_json.get("regions") or [] if isinstance(region, dict)]
        if not regions or not isinstance(regions[0].get("page_number"), int) or len(regions[0].get("bbox") or []) != 4:
            raise NotApplicable("the figure has no region")
        page = int(regions[0]["page_number"])
        original = [float(value) for value in regions[0]["bbox"]]
        furniture = self._furniture(block.chapter_id, page)
        region, problems_before, problems_after = fit_region(original, lambda box: self.ctx.figure_problems(page, box, furniture))
        if region == original:
            raise NotApplicable("no better region found")
        regions[0]["bbox"] = [round(value, 2) for value in region]
        updates = {
            "source_bbox_json": {**bbox_json, "regions": regions},
            "figure_region": FIGURE_REGION_CONTENT_EXTENT,
            "figure_region_override": {"previous_bbox": original, "problems_before": problems_before, "problems_after": problems_after},
        }
        self.structure.override_metadata(self.ctx.document_id, block.id, updates, actor_id=ACTOR_ID, reason="图片区域贴合图本身")
        # The cached crop is keyed by the block, not the region: crop again at the next export.
        for image in self.session.scalars(select(DocumentImage).where(DocumentImage.block_id == block.id)).all():
            image.metadata_json = {**(image.metadata_json or {}), "storage_status": "stale", "stale_reason": "figure_region_override"}
        self.session.flush()
        return ToolResult(
            params={"block_id": block.id, "page": page, "from": original, "to": regions[0]["bbox"], "problems_before": problems_before, "problems_after": problems_after},
            block_ids=[block.id],
        )

    def _furniture(self, chapter_id: str, page: int) -> list[list[float]]:
        boxes = []
        for block in self.session.scalars(
            select(Block).where(Block.chapter_id.in_(list(self.ctx.chapter_ids) + [chapter_id]), Block.status == ArtifactStatus.ACTIVE)
        ).all():
            health = HealthBlock(
                id=block.id,
                chapter_id=block.chapter_id,
                ordinal=block.ordinal,
                block_type=block.block_type.value,
                source_text=block.source_text or "",
                source_span_json=dict(block.source_span_json or {}),
            )
            if health.role in {"header", "footer"} and health.region_page == page and health.bbox is not None:
                boxes.append(list(health.bbox))
        return boxes

    # --- resplit_chapters -------------------------------------------------------------------------

    def _resplit_chapters__split_at_chapter_heading(self, item: WorkItem) -> ToolResult:
        block = self._finding_block(item.findings[0])
        return self._split_chapter(block, title=" ".join((block.source_text or "").split()))

    def _resplit_chapters__split_at_outline_entry(self, item: WorkItem) -> ToolResult:
        finding = item.findings[0]
        evidence = finding.get("evidence") or {}
        if evidence.get("detail") != "missing_chapter":
            raise NotApplicable("only a chapter the outline has and the split lacks can be split out")
        title = str(evidence.get("source") or "")
        location = finding.get("location") or {}
        chapter_id, page = location.get("chapter_id"), location.get("page")
        if not chapter_id or not isinstance(page, int):
            raise NotApplicable("the finding has no chapter or page")
        blocks = self._chapter_blocks(chapter_id)
        on_page = [b for b in blocks if (b.source_span_json or {}).get("source_page_start") == page]
        if not on_page:
            raise NotApplicable(f"no block of the chapter starts on page {page}")
        wanted = _norm(title)
        start = next((b for b in on_page if b.block_type == BlockType.HEADING and _norm(b.source_text) == wanted), None)
        start = start or next(
            (b for b in on_page if b.block_type == BlockType.HEADING and wanted and (wanted in _norm(b.source_text) or _norm(b.source_text) in wanted)),
            None,
        )
        start = start or on_page[0]
        return self._split_chapter(start, title=title)

    def _split_chapter(self, block: Block, *, title: str) -> ToolResult:
        """A new chapter from ``block`` to the end of its chapter; everything translated moves with it."""
        chapter = self.session.get(Chapter, block.chapter_id)
        blocks = self._chapter_blocks(chapter.id)
        if not blocks or blocks[0].id == block.id:
            raise NotApplicable("the heading already starts its chapter")
        moved = [b for b in blocks if b.ordinal >= block.ordinal]
        moved_ids = {b.id for b in moved}
        # Blocks parked by structure edits (split-offs waiting for replay) of the moved range move too.
        later = list(
            self.session.scalars(
                select(Chapter).where(Chapter.document_id == chapter.document_id, Chapter.ordinal > chapter.ordinal).order_by(Chapter.ordinal.desc())
            ).all()
        )
        for other in later:
            other.ordinal += 1_000
        self.session.flush()
        for other in later:
            other.ordinal = other.ordinal - 1_000 + 1
        self.session.flush()
        page_start = (block.source_span_json or {}).get("source_page_start")
        metadata = dict(chapter.metadata_json or {})
        new_metadata = {**metadata, "split_from_chapter_id": chapter.id, "split_by": ACTOR_ID}
        if isinstance(page_start, int):
            new_metadata["source_page_start"] = page_start
            if isinstance(metadata.get("source_page_end"), int):
                metadata["source_page_end"] = max(int(metadata.get("source_page_start") or page_start), page_start - 1)
        heading_target = self._heading_translation(block)
        new_chapter = Chapter(
            id=str(uuid.uuid4()),
            document_id=chapter.document_id,
            ordinal=chapter.ordinal + 1,
            title_src=title,
            title_tgt=heading_target,
            anchor_start=block.source_anchor,
            anchor_end=chapter.anchor_end,
            status=chapter.status,
            risk_level=chapter.risk_level,
            metadata_json=new_metadata,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        chapter.metadata_json = metadata
        chapter.anchor_end = block.source_anchor
        self.session.add(new_chapter)
        self.session.flush()
        for moving in moved:
            moving.chapter_id = new_chapter.id
        self.session.flush()
        sentences = list(self.session.scalars(select(Sentence).where(Sentence.block_id.in_(list(moved_ids)))).all())
        for sentence in sentences:
            sentence.chapter_id = new_chapter.id
        sentence_ids = {sentence.id for sentence in sentences}
        # The chapter brief the packets are built against.
        brief = self.session.scalars(
            select(MemorySnapshot)
            .where(
                MemorySnapshot.document_id == chapter.document_id,
                MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                MemorySnapshot.scope_id == chapter.id,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                MemorySnapshot.status == MemoryStatus.ACTIVE,
            )
            .order_by(MemorySnapshot.version.desc())
            .limit(1)
        ).first()
        if brief is not None:
            self.session.add(
                MemorySnapshot(
                    id=str(uuid.uuid4()),
                    document_id=brief.document_id,
                    scope_type=brief.scope_type,
                    scope_id=new_chapter.id,
                    snapshot_type=brief.snapshot_type,
                    version=brief.version,
                    content_json={**(brief.content_json or {}), "chapter_id": new_chapter.id, "title": title},
                    status=brief.status,
                )
            )
        straddling: list[str] = []
        for packet in self.session.scalars(select(TranslationPacket).where(TranslationPacket.chapter_id == chapter.id)).all():
            members = set(
                self.session.scalars(
                    select(PacketSentenceMap.sentence_id).where(
                        PacketSentenceMap.packet_id == packet.id, PacketSentenceMap.role == PacketSentenceRole.CURRENT
                    )
                ).all()
            )
            if not members or not members & sentence_ids:
                continue
            if members <= sentence_ids:
                packet.chapter_id = new_chapter.id
                packet.packet_json = {**(packet.packet_json or {}), "chapter_id": new_chapter.id}
                run_ids = self.session.scalars(select(TranslationRun.id).where(TranslationRun.packet_id == packet.id)).all()
                for segment in self.session.scalars(select(TargetSegment).where(TargetSegment.translation_run_id.in_(list(run_ids) or ["-"]))).all():
                    segment.chapter_id = new_chapter.id
            else:
                straddling.append(packet.id)
        for issue in self.session.scalars(
            select(ReviewIssue).where(ReviewIssue.chapter_id == chapter.id, ReviewIssue.sentence_id.in_(list(sentence_ids) or ["-"]))
        ).all():
            issue.chapter_id = new_chapter.id
        self.session.flush()
        retranslate: list[str] = []
        if straddling:
            retranslate = self._split_straddling_packets(straddling, chapter, new_chapter, sentence_ids)
        if self.ctx.chapter_ids and chapter.id in self.ctx.chapter_ids and new_chapter.id not in self.ctx.chapter_ids:
            self.ctx.chapter_ids.insert(self.ctx.chapter_ids.index(chapter.id) + 1, new_chapter.id)
        done = self.retranslate(retranslate, []) if retranslate else []
        return ToolResult(
            params={
                "from_chapter_id": chapter.id,
                "new_chapter_id": new_chapter.id,
                "title": title,
                "first_block_id": block.id,
                "moved_blocks": len(moved),
                "straddling_packets": straddling,
            },
            block_ids=[block.id],
            packet_ids=done,
        )

    def _split_straddling_packets(self, packet_ids: list[str], chapter: Chapter, new_chapter: Chapter, moved: set[str]) -> list[str]:
        """A packet across the new boundary keeps its first part; the rest becomes a packet of the new chapter, retranslated."""
        workflow = self.ctx.workflow()
        rebuild = workflow.targeted_rebuild_service
        new_packets: list[str] = []
        for packet_id in packet_ids:
            packet = self.session.get(TranslationPacket, packet_id)
            old_blocks = [b for b in self._chapter_blocks(chapter.id)]
            new_blocks = self._chapter_blocks(new_chapter.id)
            if not new_blocks:
                continue
            end_block = self.session.get(Block, packet.block_end_id) if packet.block_end_id else None
            packet.block_end_id = old_blocks[-1].id if old_blocks else packet.block_start_id
            new_id = str(uuid.uuid4())
            self.session.add(
                TranslationPacket(
                    id=new_id,
                    chapter_id=new_chapter.id,
                    block_start_id=new_blocks[0].id,
                    block_end_id=end_block.id if end_block is not None and end_block.chapter_id == new_chapter.id else new_blocks[0].id,
                    packet_type=packet.packet_type,
                    book_profile_version=packet.book_profile_version,
                    chapter_brief_version=packet.chapter_brief_version,
                    termbase_version=packet.termbase_version,
                    entity_snapshot_version=packet.entity_snapshot_version,
                    style_snapshot_version=packet.style_snapshot_version,
                    packet_json={},
                    status=PacketStatus.BUILT,
                )
            )
            self.session.flush()
            rebuild.rebuild_packets(self.ctx.document_id, chapter.id, [packet.id])
            rebuild.rebuild_packets(self.ctx.document_id, new_chapter.id, [new_id])
            new_packets.append(new_id)
        return new_packets

    def _heading_translation(self, block: Block) -> str | None:
        sentence_ids = list(
            self.session.scalars(
                select(Sentence.id).where(Sentence.block_id == block.id, Sentence.retired_by_revision_id.is_(None)).order_by(Sentence.ordinal_in_block)
            ).all()
        )
        targets = active_target_texts(self.session, sentence_ids)
        text = " ".join(targets.get(sentence_id, "") for sentence_id in sentence_ids).strip()
        return text or None


_RUN_IN_MAX_WORDS = pilot_qa.RUN_IN_MAX_WORDS


def _run_in_heading_line(text: str) -> tuple[int, int] | None:
    """(start, end) of the first run-in heading line of a block's text, or None."""
    spans = pilot_qa.run_in_heading_spans(text)
    return spans[0] if spans else None


def _glued_heading(text: str) -> tuple[int, int] | None:
    """A heading glued to the sentence before it ("...considering.cost optimization When ...")."""
    for match in pilot_qa._GLUED_HEADING_RE.finditer(text):
        if match.group(3) in pilot_qa._NOT_GLUED:
            continue
        start = match.start(3)
        for index, word in enumerate(list(re.finditer(r"\S+", text[start:]))[: _RUN_IN_MAX_WORDS - 1]):
            if index > 0 and word.group(0)[:1].isupper():
                return start, start + word.start()
    return None


def _marker(text: str, at: int) -> str:
    """The shortest text from ``at`` that occurs once in ``text`` (a split's second-part marker)."""
    for length in (24, 40, 80, 160, len(text)):
        marker = text[at : at + length]
        if len(marker.strip()) >= 3 and text.count(marker) == 1:
            return marker
    return text[at:]


# A sparse table this close to a figure (points) is the figure's text set beside it.
ADJACENT_GAP = 24.0
# Clear of the check's touch gap (2pt): an edge this close to a line counts as cutting it.
EDGE_MARGIN = 3.0


def fit_region(
    region: Sequence[float], problems_for: Callable[[list[float]], list[Any]], *, max_steps: int = 6
) -> tuple[list[float], list[str], list[str]]:
    """A figure region moved off the page furniture and its caption, and out to lines it cuts.

    Returns (region, problem kinds before, problem kinds after). Each step fixes every problem the
    check reports; the region with the fewest problems wins.
    """
    box = [float(value) for value in region]
    problems = problems_for(box)
    before = sorted(str(getattr(problem, "kind", problem)) for problem in problems)
    best, best_count = list(box), len(problems)
    for _step in range(max_steps):
        if not problems:
            break
        next_box = list(box)
        for problem in problems:
            kind = str(getattr(problem, "kind", ""))
            p = [float(value) for value in getattr(problem, "bbox", ())] if getattr(problem, "bbox", None) else None
            if p is None or len(p) != 4:
                continue
            if kind in {"page_furniture", "caption"}:
                center = (p[1] + p[3]) / 2
                if center <= (next_box[1] + next_box[3]) / 2:
                    next_box[1] = max(next_box[1], p[3] + EDGE_MARGIN)
                else:
                    next_box[3] = min(next_box[3], p[1] - EDGE_MARGIN)
            elif kind in {"cut_path", "cut_text"}:
                next_box = [min(next_box[0], p[0] - 1.0), min(next_box[1], p[1] - 1.0), max(next_box[2], p[2] + 1.0), max(next_box[3], p[3] + 1.0)]
        if next_box == box or next_box[2] <= next_box[0] or next_box[3] <= next_box[1]:
            break
        box = next_box
        problems = problems_for(box)
        if len(problems) < best_count:
            best, best_count = list(box), len(problems)
    after = sorted(str(getattr(problem, "kind", problem)) for problem in problems_for(best)) if best != list(region) else before
    return best, before, after
