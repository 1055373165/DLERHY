from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from book_agent.domain.enums import MemoryProposalStatus
from book_agent.domain.models import ChapterMemoryProposal, MemorySnapshot
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.services.context_compile import ChapterContextCompileOptions, ChapterContextCompiler
from book_agent.translation.contracts import CompiledTranslationContext, ContextPacket


@dataclass(slots=True)
class CompiledContextLoadResult:
    context: CompiledTranslationContext
    chapter_memory_snapshot: MemorySnapshot | None


def _coerce_nonnegative_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


@dataclass(slots=True)
class MemoryService:
    chapter_memory_repository: ChapterTranslationMemoryRepository
    context_compiler: ChapterContextCompiler

    def load_latest_chapter_memory(self, *, document_id: str, chapter_id: str) -> MemorySnapshot | None:
        return self.chapter_memory_repository.load_latest(
            document_id=document_id,
            chapter_id=chapter_id,
        )

    def load_compiled_context(
        self,
        *,
        packet: ContextPacket,
        options: ChapterContextCompileOptions | None = None,
        rerun_hints: tuple[str, ...] = (),
    ) -> CompiledContextLoadResult:
        chapter_memory_snapshot = self.load_latest_chapter_memory(
            document_id=packet.document_id,
            chapter_id=packet.chapter_id,
        )
        compiled_packet = self.context_compiler.compile(
            packet,
            chapter_memory_snapshot=chapter_memory_snapshot,
            options=options,
        )
        merged_open_questions = list(compiled_packet.open_questions)
        for hint in rerun_hints:
            if hint and hint not in merged_open_questions:
                merged_open_questions.append(hint)
        if merged_open_questions != list(compiled_packet.open_questions):
            compiled_packet = compiled_packet.model_copy(update={"open_questions": merged_open_questions})

        compiled_context = compiled_packet.model_copy(
            update={
                "compile_metadata": {
                    **dict(compiled_packet.compile_metadata),
                    "chapter_memory_available": chapter_memory_snapshot is not None,
                    "rerun_hint_count": len([hint for hint in rerun_hints if hint]),
                }
            }
        )
        return CompiledContextLoadResult(
            context=compiled_context,
            chapter_memory_snapshot=chapter_memory_snapshot,
        )

    def record_translation_proposals(
        self,
        *,
        document_id: str,
        chapter_id: str,
        packet_id: str,
        translation_run_id: str,
        current_snapshot: MemorySnapshot | None,
        proposed_content_json: dict[str, Any],
    ) -> ChapterMemoryProposal:
        return self.chapter_memory_repository.create_or_replace_proposal(
            current_snapshot=current_snapshot,
            document_id=document_id,
            chapter_id=chapter_id,
            packet_id=packet_id,
            translation_run_id=translation_run_id,
            proposed_content_json=proposed_content_json,
        )

    def reject_pending_packet_proposals(
        self,
        *,
        packet_ids: Iterable[str],
    ) -> list[ChapterMemoryProposal]:
        retired: list[ChapterMemoryProposal] = []
        seen_packet_ids: set[str] = set()
        for packet_id in packet_ids:
            normalized_packet_id = str(packet_id or "").strip()
            if not normalized_packet_id or normalized_packet_id in seen_packet_ids:
                continue
            seen_packet_ids.add(normalized_packet_id)
            retired.extend(
                self.chapter_memory_repository.retire_pending_proposals_for_packet(
                    packet_id=normalized_packet_id,
                )
            )
        return retired

    def list_chapter_proposals(
        self,
        *,
        document_id: str,
        chapter_id: str,
        status: "MemoryProposalStatus | None" = None,
    ) -> list[ChapterMemoryProposal]:
        proposals = self.chapter_memory_repository.list_proposals_for_chapter(
            chapter_id=chapter_id,
            status=status,
        )
        return [proposal for proposal in proposals if proposal.document_id == document_id]

    def approve_proposal(
        self,
        *,
        document_id: str,
        chapter_id: str,
        proposal_id: str,
    ) -> MemorySnapshot:
        proposal = self.chapter_memory_repository.load_proposal(proposal_id=proposal_id)
        if proposal is None:
            raise ValueError(f"Chapter memory proposal not found: {proposal_id}")
        if proposal.document_id != document_id or proposal.chapter_id != chapter_id:
            raise ValueError(f"Chapter memory proposal does not belong to {document_id}/{chapter_id}: {proposal_id}")
        if proposal.status == MemoryProposalStatus.COMMITTED:
            if proposal.committed_snapshot_id is None:
                raise ValueError(f"Committed chapter memory proposal is missing snapshot id: {proposal_id}")
            committed_snapshot = self.chapter_memory_repository.session.get(MemorySnapshot, proposal.committed_snapshot_id)
            if committed_snapshot is None:
                raise ValueError(f"Committed chapter memory snapshot not found: {proposal.committed_snapshot_id}")
            return committed_snapshot
        if proposal.status != MemoryProposalStatus.PROPOSED:
            raise ValueError(f"Chapter memory proposal is not pending approval: {proposal_id}")
        return self.commit_approved_packet_memory(
            document_id=document_id,
            chapter_id=chapter_id,
            translation_run_id=proposal.translation_run_id,
        )

    def reject_proposal(
        self,
        *,
        document_id: str,
        chapter_id: str,
        proposal_id: str,
    ) -> ChapterMemoryProposal:
        proposal = self.chapter_memory_repository.load_proposal(proposal_id=proposal_id)
        if proposal is None:
            raise ValueError(f"Chapter memory proposal not found: {proposal_id}")
        if proposal.document_id != document_id or proposal.chapter_id != chapter_id:
            raise ValueError(f"Chapter memory proposal does not belong to {document_id}/{chapter_id}: {proposal_id}")
        if proposal.status == MemoryProposalStatus.COMMITTED:
            raise ValueError(f"Committed chapter memory proposal cannot be rejected: {proposal_id}")
        return self.chapter_memory_repository.mark_proposal_rejected(proposal)

    def commit_approved_packet_memory(
        self,
        *,
        document_id: str,
        chapter_id: str,
        translation_run_id: str,
    ) -> MemorySnapshot:
        proposal = self.chapter_memory_repository.load_proposal_by_translation_run(
            translation_run_id=translation_run_id
        )
        if proposal is None:
            raise ValueError(f"No chapter memory proposal found for translation run {translation_run_id}")
        self.chapter_memory_repository.retire_pending_proposals_for_packet(
            packet_id=proposal.packet_id,
            keep_translation_run_id=proposal.translation_run_id,
        )

        latest_snapshot = self.load_latest_chapter_memory(document_id=document_id, chapter_id=chapter_id)
        if (
            proposal.base_snapshot_version is not None
            and latest_snapshot is not None
            and latest_snapshot.version != proposal.base_snapshot_version
        ):
            raise ValueError(
                "Chapter memory drifted before proposal commit: "
                f"expected version {proposal.base_snapshot_version}, got {latest_snapshot.version}"
            )

        committed_snapshot = self.chapter_memory_repository.supersede_and_create_next(
            current_snapshot=latest_snapshot,
            document_id=document_id,
            chapter_id=chapter_id,
            content_json=dict(proposal.proposed_content_json or {}),
        )
        self.chapter_memory_repository.mark_proposal_committed(
            proposal,
            committed_snapshot=committed_snapshot,
        )
        return committed_snapshot

    def commit_review_approved_chapter_memory(
        self,
        *,
        document_id: str,
        chapter_id: str,
        approved_translation_run_ids: Iterable[str],
    ) -> list[MemorySnapshot]:
        proposals = self.chapter_memory_repository.list_proposals_for_translation_runs(
            translation_run_ids=approved_translation_run_ids
        )
        if not proposals:
            return []

        current_snapshot = self.load_latest_chapter_memory(document_id=document_id, chapter_id=chapter_id)
        committed_snapshots: list[MemorySnapshot] = []
        for proposal in proposals:
            self.chapter_memory_repository.retire_pending_proposals_for_packet(
                packet_id=proposal.packet_id,
                keep_translation_run_id=proposal.translation_run_id,
            )
            merged_content_json = self._merge_review_approved_proposal(
                base_content_json=current_snapshot.content_json if current_snapshot is not None else {},
                proposal=proposal,
            )
            current_snapshot = self.chapter_memory_repository.supersede_and_create_next(
                current_snapshot=current_snapshot,
                document_id=document_id,
                chapter_id=chapter_id,
                content_json=merged_content_json,
            )
            self.chapter_memory_repository.mark_proposal_committed(
                proposal,
                committed_snapshot=current_snapshot,
            )
            committed_snapshots.append(current_snapshot)
        return committed_snapshots

    def _merge_review_approved_proposal(
        self,
        *,
        base_content_json: dict[str, Any],
        proposal: ChapterMemoryProposal,
    ) -> dict[str, Any]:
        proposal_content = dict(proposal.proposed_content_json or {})
        base_content = dict(base_content_json or {})

        base_recent = base_content.get("recent_accepted_translations", [])
        if not isinstance(base_recent, list):
            base_recent = []
        proposal_recent = proposal_content.get("recent_accepted_translations", [])
        if not isinstance(proposal_recent, list):
            proposal_recent = []

        proposal_entry = next(
            (
                dict(item)
                for item in proposal_recent
                if isinstance(item, dict) and item.get("packet_id") == proposal.packet_id
            ),
            None,
        )
        merged_recent = [
            dict(item)
            for item in base_recent
            if isinstance(item, dict) and item.get("packet_id") != proposal.packet_id
        ]
        if proposal_entry is not None:
            merged_recent.append(proposal_entry)
        merged_recent = merged_recent[-4:]

        base_concepts = base_content.get("active_concepts", [])
        if not isinstance(base_concepts, list):
            base_concepts = []
        proposal_concepts = proposal_content.get("active_concepts", [])
        if not isinstance(proposal_concepts, list):
            proposal_concepts = []
        merged_concepts = self._merge_active_concept_payloads(
            existing_concepts=base_concepts,
            proposal_concepts=proposal_concepts,
        )

        merged_brief_version = _coerce_nonnegative_int(base_content.get("chapter_brief_version"))
        proposal_brief_version = _coerce_nonnegative_int(proposal_content.get("chapter_brief_version"))
        chapter_brief = base_content.get("chapter_brief")
        heading_path = base_content.get("heading_path")
        if proposal_content.get("chapter_brief") and proposal_brief_version >= merged_brief_version:
            chapter_brief = proposal_content.get("chapter_brief")
            heading_path = proposal_content.get("heading_path")
            merged_brief_version = proposal_brief_version

        return {
            "schema_version": 1,
            "chapter_id": proposal.chapter_id,
            "chapter_title": base_content.get("chapter_title") or proposal_content.get("chapter_title"),
            "heading_path": heading_path,
            "chapter_brief": chapter_brief,
            "chapter_brief_version": merged_brief_version or None,
            "active_concepts": merged_concepts,
            "recent_accepted_translations": merged_recent,
            "last_packet_id": proposal.packet_id,
            "last_translation_run_id": proposal.translation_run_id,
        }

    def _merge_active_concept_payloads(
        self,
        *,
        existing_concepts: list[dict[str, Any]],
        proposal_concepts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        def _upsert(raw_item: Any) -> None:
            if not isinstance(raw_item, dict):
                return
            source_term = str(raw_item.get("source_term") or "").strip()
            if not source_term:
                return
            key = source_term.lower()
            normalized_item = dict(raw_item)
            if key not in merged:
                merged[key] = normalized_item
                order.append(key)
                return
            current = merged[key]
            for field, value in normalized_item.items():
                if field == "times_seen":
                    current[field] = max(
                        _coerce_nonnegative_int(current.get(field)),
                        _coerce_nonnegative_int(value),
                    )
                    continue
                if value not in (None, "", [], {}):
                    current[field] = value
            current.setdefault("source_term", source_term)

        for item in existing_concepts:
            _upsert(item)
        for item in proposal_concepts:
            _upsert(item)
        return [merged[key] for key in order]
