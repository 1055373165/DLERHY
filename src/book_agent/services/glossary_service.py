"""Document-level glossary service (PDF v2 M2.6).

Bridges M2.5 mined `TermCandidate` output and the existing `TermEntry`
table so a reviewer (or an automation) can promote candidates into
document-wide locked terminology that M2.7 Pass B translation will
honour.

Why the existing model is enough:

  * `TermEntry` already carries document_id, source_term, target_term,
    lock_level (SUGGESTED / PREFERRED / LOCKED), and status
    (ACTIVE / SUPERSEDED / REJECTED).
  * Scope is `(scope_type=GLOBAL, scope_id=None)` for document-wide
    locks — distinct from chapter-local concept locks handled by
    `chapter_concept_lock`.
  * Versioning via `version` + `status=SUPERSEDED` is already the
    convention used by chapter-level concept locks; we reuse it.

Service responsibilities are narrow:

  * `upsert_candidates(document_id, candidates)` — idempotent insert
    of SUGGESTED rows for mined candidates. Does NOT overwrite rows
    already at PREFERRED or LOCKED.
  * `lock_term(document_id, source_term, target_zh)` — promote / create
    a LOCKED row. If a LOCKED row already exists with a different
    target, supersede it and version up.
  * `unlock_term(document_id, source_term)` — demote to SUGGESTED
    (keeps history: supersede the locked row, create a new SUGGESTED
    entry preserving the target for audit).
  * `get_locked_terms(document_id)` — returns `{source: target}` for
    consumption by M2.7 Pass B.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.enums import LockLevel, MemoryScopeType, TermStatus, TermType
from book_agent.domain.models.translation import TermEntry
from book_agent.services.terminology_miner import TermCandidate
from book_agent.core.ids import stable_id


@dataclass(slots=True, frozen=True)
class UpsertResult:
    inserted: int
    skipped: int
    total_candidates: int


class GlossaryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- Queries ---

    def get_locked_terms(self, document_id: str) -> dict[str, str]:
        """Return `{source_term: target_term}` for every ACTIVE LOCKED
        document-scope entry.

        Chapter-scope entries are intentionally excluded; they are
        handled by the existing chapter-concept-lock flow and have
        narrower visibility.
        """
        rows = self.session.scalars(
            select(TermEntry).where(
                TermEntry.document_id == document_id,
                TermEntry.scope_type == MemoryScopeType.GLOBAL,
                TermEntry.lock_level == LockLevel.LOCKED,
                TermEntry.status == TermStatus.ACTIVE,
            )
        ).all()
        return {row.source_term: row.target_term for row in rows}

    def list_document_entries(
        self,
        document_id: str,
        *,
        include_superseded: bool = False,
    ) -> list[TermEntry]:
        stmt = select(TermEntry).where(
            TermEntry.document_id == document_id,
            TermEntry.scope_type == MemoryScopeType.GLOBAL,
        )
        if not include_superseded:
            stmt = stmt.where(TermEntry.status == TermStatus.ACTIVE)
        return list(self.session.scalars(stmt).all())

    # --- Mutations ---

    def upsert_candidates(
        self,
        document_id: str,
        candidates: Iterable[TermCandidate],
    ) -> UpsertResult:
        """Insert mined candidates as SUGGESTED rows, skipping any term
        that already has a row at PREFERRED or LOCKED (reviewer intent
        dominates automation).
        """
        existing = self._active_by_source(document_id)
        inserted = 0
        skipped = 0
        total = 0
        for cand in candidates:
            total += 1
            key = cand.term.casefold()
            if key in existing and existing[key].lock_level in {
                LockLevel.PREFERRED,
                LockLevel.LOCKED,
            }:
                skipped += 1
                continue
            if key in existing:
                # Already SUGGESTED — no-op idempotently.
                skipped += 1
                continue
            entry = TermEntry(
                id=stable_id(
                    "term-entry",
                    document_id,
                    "global",
                    key,
                    1,
                ),
                document_id=document_id,
                scope_type=MemoryScopeType.GLOBAL,
                scope_id=None,
                source_term=cand.term,
                target_term="",  # Empty — reviewer or M2.7 upstream fills later.
                term_type=TermType.CONCEPT if not cand.is_proper_noun else TermType.OTHER,
                lock_level=LockLevel.SUGGESTED,
                status=TermStatus.ACTIVE,
                version=1,
            )
            self.session.merge(entry)
            existing[key] = entry
            inserted += 1
        if inserted:
            self.session.flush()
        return UpsertResult(
            inserted=inserted, skipped=skipped, total_candidates=total
        )

    def lock_term(
        self,
        document_id: str,
        source_term: str,
        target_term: str,
        *,
        term_type: TermType = TermType.CONCEPT,
    ) -> TermEntry:
        """Promote or create a LOCKED entry. Existing ACTIVE entries for
        the same source are SUPERSEDED and version-incremented.
        """
        if not source_term.strip() or not target_term.strip():
            raise ValueError("source_term and target_term must be non-empty")
        source_clean = source_term.strip()
        target_clean = target_term.strip()

        existing = self._matching_active_entries(document_id, source_clean)
        latest_version = max((e.version for e in existing), default=0)

        if existing:
            latest = max(existing, key=lambda e: e.version)
            if (
                latest.lock_level == LockLevel.LOCKED
                and latest.target_term == target_clean
            ):
                return latest  # idempotent
            for entry in existing:
                entry.status = TermStatus.SUPERSEDED
                self.session.merge(entry)

        next_version = latest_version + 1
        new_entry = TermEntry(
            id=stable_id(
                "term-entry",
                document_id,
                "global",
                source_clean.casefold(),
                next_version,
            ),
            document_id=document_id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            source_term=source_clean,
            target_term=target_clean,
            term_type=term_type,
            lock_level=LockLevel.LOCKED,
            status=TermStatus.ACTIVE,
            version=next_version,
        )
        self.session.merge(new_entry)
        self.session.flush()
        return new_entry

    def unlock_term(self, document_id: str, source_term: str) -> TermEntry | None:
        """Demote the current LOCKED entry back to SUGGESTED, preserving
        history (the locked row becomes SUPERSEDED). Returns the new
        SUGGESTED entry, or None if nothing was locked.
        """
        source_clean = source_term.strip()
        existing = self._matching_active_entries(document_id, source_clean)
        if not existing:
            return None
        locked_entries = [e for e in existing if e.lock_level == LockLevel.LOCKED]
        if not locked_entries:
            return None
        latest = max(locked_entries, key=lambda e: e.version)
        preserved_target = latest.target_term
        preserved_type = latest.term_type
        for entry in existing:
            entry.status = TermStatus.SUPERSEDED
            self.session.merge(entry)
        next_version = max(e.version for e in existing) + 1
        demoted = TermEntry(
            id=stable_id(
                "term-entry",
                document_id,
                "global",
                source_clean.casefold(),
                next_version,
            ),
            document_id=document_id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            source_term=source_clean,
            target_term=preserved_target,
            term_type=preserved_type,
            lock_level=LockLevel.SUGGESTED,
            status=TermStatus.ACTIVE,
            version=next_version,
        )
        self.session.merge(demoted)
        self.session.flush()
        return demoted

    # --- Internals ---

    def _active_by_source(self, document_id: str) -> dict[str, TermEntry]:
        rows = self.list_document_entries(document_id, include_superseded=False)
        out: dict[str, TermEntry] = {}
        for row in rows:
            key = row.source_term.casefold()
            prior = out.get(key)
            if prior is None or row.version > prior.version:
                out[key] = row
        return out

    def _matching_active_entries(
        self,
        document_id: str,
        source_term: str,
    ) -> list[TermEntry]:
        key = source_term.casefold()
        rows = self.list_document_entries(document_id, include_superseded=False)
        return [r for r in rows if r.source_term.casefold() == key]
