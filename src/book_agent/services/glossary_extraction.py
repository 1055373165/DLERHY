"""Book-wide glossary extraction: propose one Chinese rendering per recurring term.

Terminology consistency needs a locked glossary before (or after) translation,
but the heuristic chapter-concept path only recognises the tech-book keywords
in its heuristics pack, so a trading or medical book ends up with an empty
termbase and the same concept translated several ways. This service asks the
translation provider to read the book chapter by chapter and propose terms
with renderings, then merges the proposals and grounds them in the book:

* terms that never occur in the source text are dropped (model inventions),
* occurrences are counted locally, and single mentions are kept only for
  names (people, organisations, titles),
* when the book is already translated, each term reports how many aligned
  sentences lack the proposed rendering, so a reviewer sees where the current
  translation is inconsistent.

Nothing is written to the glossary here. A reviewer confirms the proposals
(for example through the CSV the CLI writes) and locks them with
``GlossaryService.lock_term``; the review pass then flags locked-term conflicts
and its packet follow-ups retranslate them with the locked terms in context.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.workers.llm_calls import CALL_KIND_GLOSSARY_EXTRACT, observed_llm_call

from book_agent.domain.enums import BlockType, TargetSegmentStatus, TermType
from book_agent.domain.models import Block, Chapter, Sentence
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment
from book_agent.domain.terminology.matching import (
    SourceTermIndex,
    source_term_key,
    target_has_rendering,
)

DEFAULT_MAX_CHUNK_CHARS = 12000
_NAME_TYPES = {TermType.PERSON, TermType.ORG, TermType.TITLE}
_PROSE_BLOCK_TYPES = {
    BlockType.HEADING.value,
    BlockType.PARAGRAPH.value,
    BlockType.LIST_ITEM.value,
    BlockType.QUOTE.value,
    BlockType.CAPTION.value,
    BlockType.FOOTNOTE.value,
}

GLOSSARY_EXTRACTION_SYSTEM_PROMPT = (
    "You build the glossary for a professional English-to-Simplified-Chinese book translation. "
    "From the excerpt, list the terms whose translation must stay consistent across the whole book: "
    "domain concepts and jargon, named indicators, patterns, signals, strategies and methods, "
    "abbreviations, and the names of people, organisations, websites and books. "
    "Skip ordinary vocabulary, and skip everyday words whose correct Chinese depends on the sentence "
    "(a word that is sometimes a technical term and sometimes plain English is not a glossary entry). "
    "For each term give the rendering a Chinese professional publication in this field would use. "
    "Keep widely used abbreviations (for example RSI, MACD, SMA) unchanged, keep person, company and "
    "website names in their original form unless a standard Chinese name exists, and translate book titles. "
    "Use the term's base form exactly as it is spelled in the excerpt (singular for countable nouns). "
    'Return one JSON object: {"terms": [{"source_term": str, "target_term": str, '
    '"term_type": "concept" | "abbr" | "person" | "org" | "title" | "place" | "other", '
    '"required": bool, "note": str}]}. '
    "Set required to true only when every occurrence in the book must use exactly this rendering. "
    "The note is a few Chinese words explaining the term when that helps a reviewer, otherwise an empty string."
)

GLOSSARY_EXTRACTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_term": {"type": "string"},
                    "target_term": {"type": "string"},
                    "term_type": {"type": "string"},
                    "required": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["source_term", "target_term"],
            },
        }
    },
    "required": ["terms"],
}


class StructuredObjectClient(Protocol):
    def generate_structured_object(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        schema_name: str = ...,
    ) -> tuple[dict[str, Any], Any]: ...


@dataclass(slots=True)
class GlossarySuggestion:
    source_term: str
    target_term: str
    term_type: TermType
    occurrences: int
    current_mismatches: int | None = None
    note: str = ""
    alternative_targets: list[str] = field(default_factory=list)
    # Proposed for locking: the model marked it required, or it is a name/abbreviation.
    recommended_lock: bool = False


@dataclass(slots=True)
class GlossaryExtractionResult:
    document_id: str
    suggestions: list[GlossarySuggestion]
    chunk_count: int
    proposed_term_count: int
    dropped_absent_terms: list[str]
    token_in: int
    token_out: int


@dataclass(slots=True)
class _Proposal:
    source_term: str
    target_term: str
    term_type: TermType
    note: str
    required: bool = False


def _normalize_space(text: str | None) -> str:
    return " ".join(str(text or "").split())


def _coerce_term_type(value: object) -> TermType:
    try:
        return TermType(str(value or "").strip().lower())
    except ValueError:
        return TermType.OTHER


def chunk_texts(texts: Iterable[str], *, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for text in texts:
        text = _normalize_space(text)
        if not text:
            continue
        if current and size + len(text) + 1 > max_chars:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def merge_proposals(
    proposals: Sequence[_Proposal],
    source_texts: Sequence[str],
    *,
    aligned_targets: Sequence[tuple[str, str]] = (),
) -> tuple[list[GlossarySuggestion], list[str]]:
    """Merge per-chunk proposals into one suggestion per term, grounded in the source text.

    Proposals are grouped by source-term key, so case, plural, hyphen/space and
    "&"/"and" spellings of one term become one suggestion. Occurrences use
    whole-token, longest-match counting. ``aligned_targets`` pairs each
    translated sentence's source with its aligned Chinese text; when given,
    suggestions report ``current_mismatches``.
    """
    grouped: dict[str, list[_Proposal]] = defaultdict(list)
    spelling: dict[str, str] = {}
    for proposal in proposals:
        source = _normalize_space(proposal.source_term)
        target = _normalize_space(proposal.target_term)
        key = source_term_key(source)
        if not key or not target:
            continue
        grouped[key].append(proposal)
        spelling.setdefault(key, source)

    index = SourceTermIndex(spelling.values())
    occurrences_by_key = index.count(source_texts)
    sentence_keys = [(index.keys_in(source), zh) for source, zh in aligned_targets]

    suggestions: list[GlossarySuggestion] = []
    dropped: list[str] = []
    for key, items in grouped.items():
        source = spelling[key]
        occurrences = occurrences_by_key.get(key, 0)
        term_type = Counter(item.term_type for item in items).most_common(1)[0][0]
        if occurrences == 0:
            dropped.append(source)
            continue
        if occurrences < 2 and term_type not in _NAME_TYPES:
            continue
        target_counts = Counter(_normalize_space(item.target_term) for item in items)
        target = target_counts.most_common(1)[0][0]
        note = next((item.note for item in items if _normalize_space(item.note)), "")
        mismatches: int | None = None
        if aligned_targets:
            mismatches = sum(1 for keys, zh in sentence_keys if key in keys and not target_has_rendering(zh, [target]))
        suggestions.append(
            GlossarySuggestion(
                source_term=source,
                target_term=target,
                term_type=term_type,
                occurrences=occurrences,
                current_mismatches=mismatches,
                note=_normalize_space(note),
                alternative_targets=[candidate for candidate in target_counts if candidate != target],
                recommended_lock=term_type in _NAME_TYPES | {TermType.ABBR} or any(item.required for item in items),
            )
        )
    suggestions.sort(key=lambda item: (not item.recommended_lock, -item.occurrences, item.source_term.casefold()))
    return suggestions, sorted(dropped, key=str.casefold)


class GlossaryExtractionService:
    def __init__(self, session: Session, client: StructuredObjectClient, *, model_name: str) -> None:
        self.session = session
        self.client = client
        self.model_name = model_name

    def extract(self, document_id: str, *, max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> GlossaryExtractionResult:
        chapters = list(
            self.session.scalars(select(Chapter).where(Chapter.document_id == document_id).order_by(Chapter.ordinal))
        )
        proposals: list[_Proposal] = []
        source_texts: list[str] = []
        chunk_count = token_in = token_out = 0
        for chapter in chapters:
            chapter_texts = self._chapter_source_texts(chapter.id)
            source_texts.extend(chapter_texts)
            for chunk in chunk_texts(chapter_texts, max_chars=max_chunk_chars):
                chunk_count += 1
                payload, usage = self._extract_chunk(
                    document_id, chunk, chapter_title=chapter.title_src, chapter_id=chapter.id, chunk_index=chunk_count
                )
                token_in += int(getattr(usage, "token_in", 0) or 0)
                token_out += int(getattr(usage, "token_out", 0) or 0)
                proposals.extend(self._proposals_from_payload(payload))
        suggestions, dropped = merge_proposals(
            proposals, source_texts, aligned_targets=self._aligned_translations(document_id)
        )
        return GlossaryExtractionResult(
            document_id=document_id,
            suggestions=suggestions,
            chunk_count=chunk_count,
            proposed_term_count=len(proposals),
            dropped_absent_terms=dropped,
            token_in=token_in,
            token_out=token_out,
        )

    def extract_from_texts(
        self, document_id: str, texts: list[str], *, max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS
    ) -> list[GlossarySuggestion]:
        """Extract from caller-chosen texts (a sample) but count occurrences over the whole book."""
        proposals: list[_Proposal] = []
        for index, chunk in enumerate(chunk_texts(texts, max_chars=max_chunk_chars), start=1):
            payload, _usage = self._extract_chunk(document_id, chunk, chapter_title=None, chapter_id=None, chunk_index=index)
            proposals.extend(self._proposals_from_payload(payload))
        source_texts = [text for (text,) in self.session.execute(
            select(Sentence.source_text).where(Sentence.document_id == document_id, Sentence.translatable.is_(True))
        ) if _normalize_space(text)]
        suggestions, _dropped = merge_proposals(
            proposals, source_texts, aligned_targets=self._aligned_translations(document_id)
        )
        return suggestions

    def _extract_chunk(self, document_id: str, chunk: str, *, chapter_title: str | None, chapter_id: str | None, chunk_index: int):
        with observed_llm_call(
            self.session,
            call_kind=CALL_KIND_GLOSSARY_EXTRACT,
            model=self.model_name,
            chapter_id=chapter_id,
            payload={"document_id": document_id, "chunk_index": chunk_index},
        ) as call:
            payload, usage = self.client.generate_structured_object(
                model_name=self.model_name,
                system_prompt=GLOSSARY_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=f"Chapter: {chapter_title or ''}\n\nExcerpt:\n{chunk}",
                response_schema=GLOSSARY_EXTRACTION_RESPONSE_SCHEMA,
                schema_name="glossary_extraction",
            )
            call.complete(usage)
        return payload, usage

    @staticmethod
    def _proposals_from_payload(payload: Any) -> list[_Proposal]:
        proposals: list[_Proposal] = []
        for item in (payload or {}).get("terms") or []:
            if not isinstance(item, dict):
                continue
            proposals.append(
                _Proposal(
                    source_term=str(item.get("source_term") or ""),
                    target_term=str(item.get("target_term") or ""),
                    term_type=_coerce_term_type(item.get("term_type")),
                    note=str(item.get("note") or ""),
                    required=item.get("required") is True,
                )
            )
        return proposals

    def _chapter_source_texts(self, chapter_id: str) -> list[str]:
        rows = self.session.execute(
            select(Sentence.source_text)
            .join(Block, Block.id == Sentence.block_id)
            .where(
                Block.chapter_id == chapter_id,
                Block.block_type.in_(_PROSE_BLOCK_TYPES),
                Sentence.translatable.is_(True),
            )
            .order_by(Block.ordinal, Sentence.ordinal_in_block)
        )
        return [text for (text,) in rows if _normalize_space(text)]

    def _aligned_translations(self, document_id: str) -> list[tuple[str, str]]:
        rows = self.session.execute(
            select(Sentence.id, Sentence.source_text, TargetSegment.text_zh)
            .join(AlignmentEdge, AlignmentEdge.sentence_id == Sentence.id)
            .join(TargetSegment, TargetSegment.id == AlignmentEdge.target_segment_id)
            .where(
                Sentence.document_id == document_id,
                TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED,
            )
        )
        by_sentence: dict[str, list[str]] = defaultdict(list)
        sources: dict[str, str] = {}
        for sentence_id, source_text, text_zh in rows:
            sources[sentence_id] = source_text
            by_sentence[sentence_id].append(text_zh or "")
        return [(sources[sentence_id], "".join(parts)) for sentence_id, parts in by_sentence.items()]


GLOSSARY_CSV_FIELDS = (
    "lock",
    "source_term",
    "target_term",
    "term_type",
    "occurrences",
    "current_mismatches",
    "alternative_targets",
    "note",
)
_TRUTHY = {"y", "yes", "1", "true", "x", "是", "锁定"}


def write_glossary_csv(path: str | Path, suggestions: Sequence[GlossarySuggestion]) -> None:
    """Write suggestions for review: edit ``target_term``; only rows with ``lock`` set are locked.

    Rows the model marked required, and names and abbreviations, start marked.
    """
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GLOSSARY_CSV_FIELDS)
        writer.writeheader()
        for item in suggestions:
            writer.writerow(
                {
                    "lock": "y" if item.recommended_lock else "",
                    "source_term": item.source_term,
                    "target_term": item.target_term,
                    "term_type": item.term_type.value,
                    "occurrences": item.occurrences,
                    "current_mismatches": "" if item.current_mismatches is None else item.current_mismatches,
                    "alternative_targets": " / ".join(item.alternative_targets),
                    "note": item.note,
                }
            )


@dataclass(slots=True, frozen=True)
class GlossaryLockRow:
    source_term: str
    target_term: str
    term_type: TermType


def read_glossary_csv(path: str | Path) -> list[GlossaryLockRow]:
    """Rows marked for locking, with the (possibly edited) target rendering."""
    rows: list[GlossaryLockRow] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            if _normalize_space(record.get("lock")).casefold() not in _TRUTHY:
                continue
            source = _normalize_space(record.get("source_term"))
            target = _normalize_space(record.get("target_term"))
            if source and target:
                rows.append(GlossaryLockRow(source, target, _coerce_term_type(record.get("term_type"))))
    return rows
