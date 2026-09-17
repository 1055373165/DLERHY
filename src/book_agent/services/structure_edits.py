"""Block-level structure edits: relabel, split, merge two adjacent blocks, link a caption to its artifact.

Edits change the active blocks in place and then run the parse-revision fork
on the blocks involved, so sentences are re-segmented with lineage, packets
are rebuilt and translations of unchanged sentences are carried over. Each
edit is logged in ``structure_edits`` (append-only) with fingerprints of the
blocks before the edit.

Structure refresh re-applies the parser's view of every block. ``replay``
runs after a refresh and before its fork: an edit whose blocks came back
exactly as they were when it was first applied is applied again (so the
fork sees no change and keeps translations); an edit whose blocks changed is
logged as stale and left for a person or the Structure Agent.

A split inserts the second part as a new block right after the original and
shifts the later ordinals of the chapter by one. EPUB refresh matches blocks
by ordinal, so ``prepare_for_refresh`` parks split-off blocks outside the
ordinal range, restores the original text and compacts the ordinals before a
refresh; ``replay`` then splits again.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.block_rules import protected_policy_for_block
from book_agent.domain.enums import ArtifactStatus, BlockType
from book_agent.domain.models import Block, Chapter, StructureEdit
from book_agent.domain.models.translation import TranslationPacket
from book_agent.domain.structure.sentence_alignment import normalize_sentence
from book_agent.services.parse_revision_fork import ForkResult, ParseRevisionForkService

RELABEL = "relabel_block"
SPLIT = "split_block"
MERGE = "merge_blocks"
# Split-off blocks waiting for replay live above every real ordinal.
PARKED_ORDINAL_BASE = 1_000_000
_ORDINAL_SHIFT_OFFSET = 2_000_000
SPLIT_OF_KEY = "structure_split_of"
MIN_SPLIT_MARKER = 3
LINK_CAPTION = "link_caption"

# Types an edit may give or take away. Tables, figures and images carry extracted artifacts
# (cells, image files) that a relabel cannot create or remove.
RELABEL_TYPES = frozenset(
    {
        BlockType.HEADING,
        BlockType.PARAGRAPH,
        BlockType.QUOTE,
        BlockType.FOOTNOTE,
        BlockType.CAPTION,
        BlockType.CODE,
        BlockType.LIST_ITEM,
        BlockType.EQUATION,
    }
)
MERGEABLE_TYPES = frozenset(
    {BlockType.PARAGRAPH, BlockType.QUOTE, BlockType.FOOTNOTE, BlockType.LIST_ITEM, BlockType.CODE, BlockType.CAPTION}
)
CAPTIONED_TYPES = frozenset({BlockType.FIGURE, BlockType.IMAGE, BlockType.TABLE, BlockType.EQUATION, BlockType.CODE})


class StructureEditRejected(ValueError):
    """The edit does not apply; the message says why (safe to show to a model or a person)."""


@dataclass(slots=True)
class EditOutcome:
    edit_id: str
    kind: str
    block_ids: list[str]
    fork: ForkResult | None = None

    def to_json(self) -> dict[str, Any]:
        fork = self.fork
        return {
            "edit_id": self.edit_id,
            "kind": self.kind,
            "block_ids": list(self.block_ids),
            "parse_revision_version": fork.parse_revision_version if fork else None,
            "retranslate_packet_count": len(fork.retranslate_packet_ids) if fork else 0,
            "carried_packet_count": len(fork.carried_packet_ids) if fork else 0,
        }


@dataclass(slots=True)
class ReplayResult:
    reapplied_edit_ids: list[str] = field(default_factory=list)
    stale_edit_ids: list[str] = field(default_factory=list)
    # Blocks the replay changed; the caller's fork must include them.
    block_ids: list[str] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprint(text: str | None) -> str:
    return hashlib.sha1(normalize_sentence(text or "").encode("utf-8")).hexdigest()


def _snapshot(block: Block) -> dict[str, Any]:
    return {
        "block_id": block.id,
        "chapter_id": block.chapter_id,
        "ordinal": block.ordinal,
        "block_type": block.block_type.value,
        "status": block.status.value,
        "text_sha1": _fingerprint(block.source_text),
    }


def _join_text(first: str, second: str, block_type: BlockType) -> str:
    first, second = (first or "").rstrip(), (second or "").lstrip()
    if block_type == BlockType.CODE:
        return f"{first}\n{second}"
    if first.endswith("-") and second[:1].islower():
        # A word hyphenated across the break.
        return first[:-1] + second
    return f"{first} {second}"


class StructureEditService:
    def __init__(self, session: Session, *, fork_service: ParseRevisionForkService | None = None) -> None:
        self.session = session
        self.fork_service = fork_service or ParseRevisionForkService(session)

    # --- edits ------------------------------------------------------------------------

    def relabel_block(
        self,
        document_id: str,
        block_id: str,
        block_type: str,
        *,
        heading_level: int | None = None,
        actor_id: str,
        reason: str,
        turn_id: str | None = None,
    ) -> EditOutcome:
        block = self._active_block(document_id, block_id)
        try:
            new_type = BlockType(block_type)
        except ValueError as exc:
            raise StructureEditRejected(f"unknown block type {block_type!r}") from exc
        if block.block_type not in RELABEL_TYPES or new_type not in RELABEL_TYPES:
            raise StructureEditRejected(
                f"cannot relabel {block.block_type.value} as {new_type.value}; allowed types: "
                + ", ".join(sorted(t.value for t in RELABEL_TYPES))
            )
        if heading_level is not None and new_type != BlockType.HEADING:
            raise StructureEditRejected("heading_level only applies to headings")
        current_level = (block.source_span_json or {}).get("heading_level")
        if block.block_type == new_type and (heading_level is None or heading_level == current_level):
            raise StructureEditRejected(f"block is already a {new_type.value}")
        snapshot = [_snapshot(block)]
        self._apply_relabel(block, new_type, heading_level)
        fork = self._fork(document_id, [block.id], reason=f"structure edit: relabel {block.id} as {new_type.value}", actor_id=actor_id)
        edit = self._log(
            document_id,
            RELABEL,
            {"block_id": block.id, "block_type": new_type.value, "heading_level": heading_level},
            snapshot,
            fork=fork,
            actor_id=actor_id,
            reason=reason,
            turn_id=turn_id,
        )
        return EditOutcome(edit_id=edit.id, kind=RELABEL, block_ids=[block.id], fork=fork)

    def split_block(
        self,
        document_id: str,
        block_id: str,
        second_part_starts_with: str,
        *,
        actor_id: str,
        reason: str,
        turn_id: str | None = None,
    ) -> EditOutcome:
        block = self._active_block(document_id, block_id)
        if block.block_type not in MERGEABLE_TYPES:
            raise StructureEditRejected(
                "only " + ", ".join(sorted(t.value for t in MERGEABLE_TYPES)) + f" blocks can be split, not {block.block_type.value}"
            )
        self._split_point(block.source_text, second_part_starts_with)
        snapshot = [_snapshot(block)]
        text_before = block.source_text
        new_block = self._apply_split(block, second_part_starts_with, str(uuid.uuid4()))
        fork = self._fork(document_id, [block.id, new_block.id], reason=f"structure edit: split {block.id}", actor_id=actor_id)
        edit = self._log(
            document_id,
            SPLIT,
            {
                "block_id": block.id,
                "second_part_starts_with": second_part_starts_with,
                "new_block_id": new_block.id,
                "text_before": text_before,
            },
            snapshot,
            fork=fork,
            actor_id=actor_id,
            reason=reason,
            turn_id=turn_id,
        )
        return EditOutcome(edit_id=edit.id, kind=SPLIT, block_ids=[block.id, new_block.id], fork=fork)

    def merge_blocks(
        self,
        document_id: str,
        first_block_id: str,
        second_block_id: str,
        *,
        actor_id: str,
        reason: str,
        turn_id: str | None = None,
    ) -> EditOutcome:
        first = self._active_block(document_id, first_block_id)
        second = self._active_block(document_id, second_block_id)
        self._check_mergeable(first, second)
        snapshot = [_snapshot(first), _snapshot(second)]
        self._apply_merge(first, second)
        fork = self._fork(
            document_id, [first.id, second.id], reason=f"structure edit: merge {second.id} into {first.id}", actor_id=actor_id
        )
        edit = self._log(
            document_id,
            MERGE,
            {"first_block_id": first.id, "second_block_id": second.id},
            snapshot,
            fork=fork,
            actor_id=actor_id,
            reason=reason,
            turn_id=turn_id,
        )
        return EditOutcome(edit_id=edit.id, kind=MERGE, block_ids=[first.id, second.id], fork=fork)

    def link_caption(
        self,
        document_id: str,
        caption_block_id: str,
        artifact_block_id: str,
        *,
        actor_id: str,
        reason: str,
        turn_id: str | None = None,
    ) -> EditOutcome:
        caption = self._active_block(document_id, caption_block_id)
        artifact = self._active_block(document_id, artifact_block_id)
        if caption.block_type != BlockType.CAPTION:
            raise StructureEditRejected("the caption block must be a caption (relabel it first)")
        if artifact.block_type not in CAPTIONED_TYPES:
            raise StructureEditRejected(
                "captions attach to " + ", ".join(sorted(t.value for t in CAPTIONED_TYPES)) + f", not {artifact.block_type.value}"
            )
        if caption.chapter_id != artifact.chapter_id:
            raise StructureEditRejected("caption and artifact must be in the same chapter")
        if (artifact.source_span_json or {}).get("linked_caption_block_id") == caption.id:
            raise StructureEditRejected("the caption is already linked to this artifact")
        snapshot = [_snapshot(caption), _snapshot(artifact)]
        self._apply_link(caption, artifact)
        edit = self._log(
            document_id,
            LINK_CAPTION,
            {"caption_block_id": caption.id, "artifact_block_id": artifact.id},
            snapshot,
            fork=None,
            actor_id=actor_id,
            reason=reason,
            turn_id=turn_id,
        )
        return EditOutcome(edit_id=edit.id, kind=LINK_CAPTION, block_ids=[caption.id, artifact.id])

    # --- replay -----------------------------------------------------------------------

    def replay(self, document_id: str, *, actor_id: str = "services.structure_edits") -> ReplayResult:
        """Re-apply logged edits after a structure refresh; run before the refresh's fork."""
        result = ReplayResult()
        rows = list(
            self.session.scalars(
                select(StructureEdit)
                .where(StructureEdit.document_id == document_id)
                .order_by(StructureEdit.created_at, StructureEdit.id)
            ).all()
        )
        latest_status: dict[str, str] = {}
        originals: list[StructureEdit] = []
        for row in rows:
            original_id = row.replay_of_edit_id or row.id
            latest_status[original_id] = row.status
            if row.replay_of_edit_id is None:
                originals.append(row)
        for edit in originals:
            if latest_status.get(edit.id) == "stale":
                continue
            outcome = self._replay_one(document_id, edit)
            if outcome is None:
                continue
            status, block_ids = outcome
            self.session.add(
                StructureEdit(
                    document_id=document_id,
                    kind=edit.kind,
                    status=status,
                    args_json=dict(edit.args_json or {}),
                    blocks_json=list(edit.blocks_json or []),
                    replay_of_edit_id=edit.id,
                    actor_id=actor_id,
                    reason="replayed after structure refresh" if status == "reapplied" else "blocks changed since the edit",
                    created_at=_utcnow(),
                )
            )
            if status == "reapplied":
                result.reapplied_edit_ids.append(edit.id)
                result.block_ids.extend(block_ids)
            else:
                result.stale_edit_ids.append(edit.id)
        self.session.flush()
        result.block_ids = list(dict.fromkeys(result.block_ids))
        return result

    def _replay_one(self, document_id: str, edit: StructureEdit) -> tuple[str, list[str]] | None:
        """("reapplied", blocks) / ("stale", []) / None when the edit is still in effect."""
        args = edit.args_json or {}
        before = {item["block_id"]: item for item in edit.blocks_json or []}
        blocks = {block_id: self.session.get(Block, block_id) for block_id in before}
        if any(block is None for block in blocks.values()):
            return "stale", []
        if edit.kind == RELABEL:
            block = blocks[args["block_id"]]
            new_type = BlockType(args["block_type"])
            level = args.get("heading_level")
            if block.status == ArtifactStatus.ACTIVE and block.block_type == new_type and (
                level is None or (block.source_span_json or {}).get("heading_level") == level
            ):
                return None
            if block.status != ArtifactStatus.ACTIVE or _fingerprint(block.source_text) != before[block.id]["text_sha1"]:
                return "stale", []
            if block.block_type.value != before[block.id]["block_type"]:
                # The parser changed its mind about this block; do not override a new classification.
                return "stale", []
            self._apply_relabel(block, new_type, level)
            return "reapplied", [block.id]
        if edit.kind == SPLIT:
            block = blocks[args["block_id"]]
            split_off = self.session.get(Block, args["new_block_id"])
            if split_off is not None and split_off.status == ArtifactStatus.ACTIVE and split_off.ordinal < PARKED_ORDINAL_BASE:
                return None
            if (
                split_off is None
                or block.status != ArtifactStatus.ACTIVE
                or _fingerprint(block.source_text) != before[block.id]["text_sha1"]
            ):
                return "stale", []
            try:
                self._split_point(block.source_text, args["second_part_starts_with"])
            except StructureEditRejected:
                return "stale", []
            self._apply_split(block, args["second_part_starts_with"], split_off.id)
            return "reapplied", [block.id, split_off.id]
        if edit.kind == MERGE:
            first, second = blocks[args["first_block_id"]], blocks[args["second_block_id"]]
            if (
                second.status == ArtifactStatus.INVALIDATED
                and (second.source_span_json or {}).get("structure_merged_into") == first.id
                and first.status == ArtifactStatus.ACTIVE
            ):
                return None
            if (
                first.status != ArtifactStatus.ACTIVE
                or second.status != ArtifactStatus.ACTIVE
                or _fingerprint(first.source_text) != before[first.id]["text_sha1"]
                or _fingerprint(second.source_text) != before[second.id]["text_sha1"]
            ):
                return "stale", []
            try:
                self._check_mergeable(first, second)
            except StructureEditRejected:
                return "stale", []
            self._apply_merge(first, second)
            return "reapplied", [first.id, second.id]
        if edit.kind == LINK_CAPTION:
            caption, artifact = blocks[args["caption_block_id"]], blocks[args["artifact_block_id"]]
            if (artifact.source_span_json or {}).get("linked_caption_block_id") == caption.id and (
                caption.source_span_json or {}
            ).get("caption_for_block_id") == artifact.id:
                return None
            if (
                caption.status != ArtifactStatus.ACTIVE
                or artifact.status != ArtifactStatus.ACTIVE
                or caption.block_type != BlockType.CAPTION
                or artifact.block_type not in CAPTIONED_TYPES
            ):
                return "stale", []
            self._apply_link(caption, artifact)
            return "reapplied", []
        return "stale", []

    def prepare_for_refresh(self, document_id: str) -> list[str]:
        """Undo splits before a structure refresh so blocks line up with the parser's ordinals.

        Split-off blocks are parked (invalidated, ordinal above every real
        one), originals get their pre-split text back and ordinals are
        compacted. ``replay`` after the refresh splits again when the parser
        returns the same text. Returns the chapters touched.
        """
        split_edits = list(
            self.session.scalars(
                select(StructureEdit)
                .where(
                    StructureEdit.document_id == document_id,
                    StructureEdit.kind == SPLIT,
                    StructureEdit.replay_of_edit_id.is_(None),
                )
                .order_by(StructureEdit.created_at, StructureEdit.id)
            ).all()
        )
        touched: list[str] = []
        now = _utcnow()
        # Newest splits first, so a block split twice gets its text back in the right order.
        for edit in reversed(split_edits):
            args = edit.args_json or {}
            split_off = self.session.get(Block, args.get("new_block_id")) if args.get("new_block_id") else None
            if split_off is None or split_off.status != ArtifactStatus.ACTIVE or split_off.ordinal >= PARKED_ORDINAL_BASE:
                continue
            original = self.session.get(Block, args.get("block_id"))
            if original is not None and original.status == ArtifactStatus.ACTIVE:
                expected_first = self._split_parts(args["text_before"], args["second_part_starts_with"])[0]
                if normalize_sentence(original.source_text) == normalize_sentence(expected_first):
                    original.source_text = args["text_before"]
                    original.normalized_text = " ".join(args["text_before"].split())
                    original.updated_at = now
                self._move_packet_bounds(split_off, original)
            parked = self.session.scalar(
                select(func.count(Block.id)).where(
                    Block.chapter_id == split_off.chapter_id, Block.ordinal >= PARKED_ORDINAL_BASE
                )
            ) or 0
            split_off.ordinal = PARKED_ORDINAL_BASE + int(parked) + 1
            split_off.status = ArtifactStatus.INVALIDATED
            split_off.updated_at = now
            self.session.flush()
            touched.append(split_off.chapter_id)
        for chapter_id in dict.fromkeys(touched):
            self._compact_ordinals(chapter_id)
        return list(dict.fromkeys(touched))

    # --- helpers ----------------------------------------------------------------------

    @staticmethod
    def _split_parts(text: str, marker: str) -> tuple[str, str]:
        index = (text or "").find(marker)
        return (text[:index].rstrip(), text[index:].lstrip()) if index > 0 else (text, "")

    def _split_point(self, text: str, marker: str) -> int:
        if len((marker or "").strip()) < MIN_SPLIT_MARKER:
            raise StructureEditRejected(f"second_part_starts_with needs at least {MIN_SPLIT_MARKER} characters")
        index = (text or "").find(marker)
        if index <= 0:
            raise StructureEditRejected("second_part_starts_with must occur in the block text after its start")
        if text.find(marker, index + 1) != -1:
            raise StructureEditRejected("second_part_starts_with occurs more than once; quote a longer piece")
        first, second = self._split_parts(text, marker)
        if not first or not second:
            raise StructureEditRejected("both parts of the split must have text")
        return index

    def _set_ordinals(self, moves: list[tuple[Block, int]]) -> None:
        """Renumber blocks without tripping the (chapter, ordinal) unique constraint."""
        if not moves:
            return
        for block, _ in moves:
            block.ordinal = block.ordinal + _ORDINAL_SHIFT_OFFSET
        self.session.flush()
        for block, ordinal in moves:
            block.ordinal = ordinal
        self.session.flush()

    def _compact_ordinals(self, chapter_id: str) -> None:
        blocks = list(
            self.session.scalars(
                select(Block)
                .where(Block.chapter_id == chapter_id, Block.ordinal < PARKED_ORDINAL_BASE)
                .order_by(Block.ordinal)
            ).all()
        )
        self._set_ordinals([(block, index) for index, block in enumerate(blocks, start=1) if block.ordinal != index])

    def _move_packet_bounds(self, from_block: Block, to_block: Block) -> None:
        for packet in self.session.scalars(
            select(TranslationPacket).where(
                TranslationPacket.chapter_id == from_block.chapter_id,
                (TranslationPacket.block_start_id == from_block.id) | (TranslationPacket.block_end_id == from_block.id),
            )
        ).all():
            if packet.block_start_id == from_block.id:
                packet.block_start_id = to_block.id
            if packet.block_end_id == from_block.id:
                packet.block_end_id = to_block.id
            packet.updated_at = _utcnow()

    def _apply_split(self, block: Block, marker: str, new_block_id: str) -> Block:
        now = _utcnow()
        first, second = self._split_parts(block.source_text, marker)
        later = list(
            self.session.scalars(
                select(Block)
                .where(
                    Block.chapter_id == block.chapter_id,
                    Block.ordinal > block.ordinal,
                    Block.ordinal < PARKED_ORDINAL_BASE,
                )
                .order_by(Block.ordinal)
            ).all()
        )
        self._set_ordinals([(item, item.ordinal + 1) for item in later])
        span = dict(block.source_span_json or {})
        split_off = self.session.get(Block, new_block_id)
        if split_off is None:
            kept = {"source_path", "source_page_start", "source_page_end", "source_bbox_json", "pdf_page_family", "heading_level"}
            split_off = Block(
                id=new_block_id,
                chapter_id=block.chapter_id,
                ordinal=block.ordinal + 1,
                block_type=block.block_type,
                parse_revision_id=block.parse_revision_id,
                source_text=second,
                normalized_text=" ".join(second.split()),
                source_anchor=f"{block.source_anchor or block.id}::split",
                source_span_json={key: value for key, value in span.items() if key in kept} | {SPLIT_OF_KEY: block.id},
                parse_confidence=block.parse_confidence,
                protected_policy=block.protected_policy,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            self.session.add(split_off)
        else:
            split_off.ordinal = block.ordinal + 1
            split_off.block_type = block.block_type
            split_off.source_text = second
            split_off.normalized_text = " ".join(second.split())
            split_off.protected_policy = block.protected_policy
            split_off.status = ArtifactStatus.ACTIVE
            split_off.updated_at = now
        block.source_text = first
        block.normalized_text = " ".join(first.split())
        block.source_span_json = {
            **span,
            "structure_split_block_ids": list(dict.fromkeys([*span.get("structure_split_block_ids", []), new_block_id])),
        }
        block.updated_at = now
        self.session.flush()
        # The new block belongs to the packet that ended with the original.
        for packet in self.session.scalars(
            select(TranslationPacket).where(
                TranslationPacket.chapter_id == block.chapter_id, TranslationPacket.block_end_id == block.id
            )
        ).all():
            packet.block_end_id = split_off.id
            packet.updated_at = now
        self.session.flush()
        return split_off

    def _active_block(self, document_id: str, block_id: str) -> Block:
        block = self.session.get(Block, block_id)
        chapter = self.session.get(Chapter, block.chapter_id) if block is not None else None
        if block is None or chapter is None or chapter.document_id != document_id:
            raise StructureEditRejected(f"block {block_id} is not in this document")
        if block.status != ArtifactStatus.ACTIVE:
            raise StructureEditRejected(f"block {block_id} is not active")
        return block

    def _check_mergeable(self, first: Block, second: Block) -> None:
        if first.id == second.id:
            raise StructureEditRejected("cannot merge a block with itself")
        if first.chapter_id != second.chapter_id:
            raise StructureEditRejected("blocks must be in the same chapter")
        if first.block_type != second.block_type or first.block_type not in MERGEABLE_TYPES:
            raise StructureEditRejected(
                "only two blocks of the same type can be merged ("
                + ", ".join(sorted(t.value for t in MERGEABLE_TYPES))
                + f"); got {first.block_type.value} and {second.block_type.value}"
            )
        next_active = self.session.scalars(
            select(Block)
            .where(
                Block.chapter_id == first.chapter_id,
                Block.ordinal > first.ordinal,
                Block.status == ArtifactStatus.ACTIVE,
            )
            .order_by(Block.ordinal)
            .limit(1)
        ).first()
        if next_active is None or next_active.id != second.id:
            raise StructureEditRejected("the second block must directly follow the first")

    def _apply_relabel(self, block: Block, new_type: BlockType, heading_level: int | None) -> None:
        now = _utcnow()
        span = dict(block.source_span_json or {})
        span["structure_edit_previous_type"] = block.block_type.value
        if new_type == BlockType.HEADING:
            span["heading_level"] = heading_level or span.get("heading_level") or 2
        block.block_type = new_type
        block.source_span_json = span
        block.protected_policy = protected_policy_for_block(new_type, span)
        block.updated_at = now
        self.session.flush()

    def _apply_merge(self, first: Block, second: Block) -> None:
        now = _utcnow()
        first.source_text = _join_text(first.source_text, second.source_text, first.block_type)
        first.normalized_text = _join_text(
            first.normalized_text or first.source_text, second.normalized_text or second.source_text, first.block_type
        ) if first.normalized_text is not None else None
        first_span, second_span = dict(first.source_span_json or {}), dict(second.source_span_json or {})
        if isinstance(second_span.get("source_page_end"), int):
            first_span["source_page_end"] = max(int(first_span.get("source_page_end") or 0), second_span["source_page_end"])
        first_regions = ((first_span.get("source_bbox_json") or {}).get("regions")) or []
        second_regions = ((second_span.get("source_bbox_json") or {}).get("regions")) or []
        if second_regions:
            first_span["source_bbox_json"] = {**(first_span.get("source_bbox_json") or {}), "regions": [*first_regions, *second_regions]}
        first_span["structure_merged_block_ids"] = [*first_span.get("structure_merged_block_ids", []), second.id]
        first.source_span_json = first_span
        first.updated_at = now
        second.status = ArtifactStatus.INVALIDATED
        second.source_span_json = {**second_span, "structure_merged_into": first.id}
        second.updated_at = now
        # Packet ranges are block ids: keep them pointing at active blocks.
        for packet in self.session.scalars(
            select(TranslationPacket).where(
                TranslationPacket.chapter_id == first.chapter_id,
                (TranslationPacket.block_start_id == second.id) | (TranslationPacket.block_end_id == second.id),
            )
        ).all():
            if packet.block_end_id == second.id and packet.block_start_id != second.id:
                packet.block_end_id = first.id
            elif packet.block_start_id == second.id and packet.block_end_id not in (None, second.id):
                following = self.session.scalars(
                    select(Block)
                    .where(Block.chapter_id == second.chapter_id, Block.ordinal > second.ordinal, Block.status == ArtifactStatus.ACTIVE)
                    .order_by(Block.ordinal)
                    .limit(1)
                ).first()
                if following is not None:
                    packet.block_start_id = following.id
            packet.updated_at = now
        self.session.flush()

    def _apply_link(self, caption: Block, artifact: Block) -> None:
        now = _utcnow()
        chapter_blocks = self.session.scalars(select(Block).where(Block.chapter_id == caption.chapter_id)).all()
        for block in chapter_blocks:
            span = dict(block.source_span_json or {})
            changed = False
            # Drop links that would contradict the new one.
            if block.id != artifact.id and span.get("linked_caption_block_id") == caption.id:
                span.pop("linked_caption_block_id")
                changed = True
            if block.id != caption.id and span.get("caption_for_block_id") == artifact.id:
                span.pop("caption_for_block_id")
                changed = True
            if changed:
                block.source_span_json = span
                block.updated_at = now
        artifact.source_span_json = {**(artifact.source_span_json or {}), "linked_caption_block_id": caption.id}
        caption.source_span_json = {**(caption.source_span_json or {}), "caption_for_block_id": artifact.id}
        artifact.updated_at = now
        caption.updated_at = now
        self.session.flush()

    def _fork(self, document_id: str, block_ids: list[str], *, reason: str, actor_id: str) -> ForkResult:
        return self.fork_service.resegment_blocks(
            document_id, block_ids=block_ids, reason=reason, actor_id=actor_id, align_across_blocks=True
        )

    def _log(
        self,
        document_id: str,
        kind: str,
        args: dict[str, Any],
        blocks: list[dict[str, Any]],
        *,
        fork: ForkResult | None,
        actor_id: str,
        reason: str,
        turn_id: str | None,
    ) -> StructureEdit:
        edit = StructureEdit(
            document_id=document_id,
            kind=kind,
            status="applied",
            args_json=args,
            blocks_json=blocks,
            parse_revision_id=fork.parse_revision_id if fork is not None else None,
            turn_id=turn_id,
            actor_id=actor_id,
            reason=reason,
            created_at=_utcnow(),
        )
        self.session.add(edit)
        self.session.flush()
        return edit
