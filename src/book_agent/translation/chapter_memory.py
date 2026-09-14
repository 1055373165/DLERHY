"""Typed chapter translation memory (the CHAPTER_TRANSLATION_MEMORY snapshot payload).

Bootstrap seeding, backfill, packet translation, proposal approval and
concept locking used to assemble and merge this JSON payload independently,
each with slightly different defaults and merge rules. They now share this
model: ``from_content`` tolerates older or partial payloads, ``to_content``
writes the canonical shape, and the merge helpers carry the single commit
policy (a newer-or-equal chapter brief wins, recent translations are keyed by
packet and capped, concepts are upserted by source term).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

SCHEMA_VERSION = 1
RECENT_TRANSLATION_LIMIT = 4

_KNOWN_KEYS = (
    "schema_version",
    "chapter_id",
    "chapter_title",
    "heading_path",
    "chapter_brief",
    "chapter_brief_version",
    "active_concepts",
    "recent_accepted_translations",
    "last_packet_id",
    "last_translation_run_id",
)


def _nonnegative_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


@dataclass(frozen=True, slots=True)
class ChapterMemory:
    chapter_id: str | None
    chapter_title: str | None = None
    heading_path: list[str] | None = field(default_factory=list)
    chapter_brief: str | None = None
    chapter_brief_version: int | None = None
    active_concepts: list[dict[str, Any]] = field(default_factory=list)
    recent_accepted_translations: list[dict[str, Any]] = field(default_factory=list)
    last_packet_id: str | None = None
    last_translation_run_id: str | None = None
    # Keys written by older code that this model does not interpret.
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def seed(
        cls,
        *,
        chapter_id: str,
        chapter_title: str | None,
        brief_content: Mapping[str, Any] | None = None,
        brief_version: int | None = None,
    ) -> ChapterMemory:
        """Initial memory for a chapter, optionally carrying its chapter brief."""
        brief = dict(brief_content or {})
        default_heading_path = [chapter_title] if chapter_title else []
        return cls(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            heading_path=brief.get("heading_path", default_heading_path),
            chapter_brief=brief.get("summary"),
            chapter_brief_version=brief_version,
        )

    @classmethod
    def from_content(cls, content: Mapping[str, Any] | None, *, chapter_id: str | None = None) -> ChapterMemory:
        data = dict(content or {})
        return cls(
            chapter_id=data.get("chapter_id", chapter_id),
            chapter_title=data.get("chapter_title"),
            heading_path=data.get("heading_path") if "heading_path" in data else [],
            chapter_brief=data.get("chapter_brief"),
            chapter_brief_version=data.get("chapter_brief_version"),
            active_concepts=_dict_items(data.get("active_concepts")),
            recent_accepted_translations=_dict_items(data.get("recent_accepted_translations")),
            last_packet_id=data.get("last_packet_id"),
            last_translation_run_id=data.get("last_translation_run_id"),
            extras={key: value for key, value in data.items() if key not in _KNOWN_KEYS},
        )

    def to_content(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
            "heading_path": self.heading_path,
            "chapter_brief": self.chapter_brief,
            "chapter_brief_version": self.chapter_brief_version,
            "active_concepts": [dict(item) for item in self.active_concepts],
            "recent_accepted_translations": [dict(item) for item in self.recent_accepted_translations],
            "last_packet_id": self.last_packet_id,
            "last_translation_run_id": self.last_translation_run_id,
            **self.extras,
        }

    def with_brief(
        self,
        chapter_brief: str | None,
        *,
        version: int | None,
        heading_path: list[str] | None,
    ) -> ChapterMemory:
        """Adopt an incoming brief when there is none yet or its version is not older."""
        current_version = _nonnegative_int(self.chapter_brief_version)
        incoming_version = _nonnegative_int(version)
        if chapter_brief and (self.chapter_brief is None or incoming_version >= current_version):
            return replace(
                self,
                chapter_brief=chapter_brief,
                chapter_brief_version=incoming_version or None,
                heading_path=heading_path,
            )
        return replace(
            self,
            chapter_brief_version=current_version or None,
            heading_path=self.heading_path or heading_path,
        )

    def with_recent_translation(self, entry: Mapping[str, Any]) -> ChapterMemory:
        """Record an accepted packet translation, replacing that packet's older entry."""
        packet_id = entry.get("packet_id")
        recent = [item for item in self.recent_accepted_translations if item.get("packet_id") != packet_id]
        recent.append(dict(entry))
        return replace(self, recent_accepted_translations=recent[-RECENT_TRANSLATION_LIMIT:])

    def recent_translation_for_packet(self, packet_id: str) -> dict[str, Any] | None:
        return next(
            (dict(item) for item in self.recent_accepted_translations if item.get("packet_id") == packet_id),
            None,
        )

    def with_concepts_upserted(self, concepts: list[dict[str, Any]]) -> ChapterMemory:
        """Upsert concepts by source term; non-empty incoming fields win, times_seen keeps the max."""
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for raw_item in [*self.active_concepts, *concepts]:
            if not isinstance(raw_item, dict):
                continue
            source_term = str(raw_item.get("source_term") or "").strip()
            if not source_term:
                continue
            key = source_term.lower()
            item = dict(raw_item)
            if key not in merged:
                merged[key] = item
                order.append(key)
                continue
            current = merged[key]
            for name, value in item.items():
                if name == "times_seen":
                    current[name] = max(_nonnegative_int(current.get(name)), _nonnegative_int(value))
                    continue
                if value not in (None, "", [], {}):
                    current[name] = value
            current.setdefault("source_term", source_term)
        return replace(self, active_concepts=[merged[key] for key in order])

    def merge_approved_proposal(
        self,
        proposal: ChapterMemory,
        *,
        packet_id: str,
        translation_run_id: str | None,
    ) -> ChapterMemory:
        """Commit a reviewed proposal for ``packet_id`` onto this (current) memory."""
        merged = replace(self, chapter_title=self.chapter_title or proposal.chapter_title)
        proposal_entry = proposal.recent_translation_for_packet(packet_id)
        if proposal_entry is not None:
            merged = merged.with_recent_translation(proposal_entry)
        else:
            merged = replace(
                merged,
                recent_accepted_translations=[
                    item for item in merged.recent_accepted_translations if item.get("packet_id") != packet_id
                ][-RECENT_TRANSLATION_LIMIT:],
            )
        merged = merged.with_concepts_upserted(proposal.active_concepts)
        merged = merged.with_brief(
            proposal.chapter_brief,
            version=proposal.chapter_brief_version,
            heading_path=proposal.heading_path,
        )
        return replace(
            merged,
            chapter_id=proposal.chapter_id or merged.chapter_id,
            last_packet_id=packet_id,
            last_translation_run_id=translation_run_id,
        )
