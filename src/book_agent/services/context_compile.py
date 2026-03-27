from __future__ import annotations

from dataclasses import dataclass, field
import re

from book_agent.domain.models import MemorySnapshot
from book_agent.services.style_drift import STYLE_DRIFT_RULES, source_aware_literalism_guardrail_lines
from book_agent.services.term_normalization import (
    normalize_concept_candidate,
    normalize_relevant_term,
)
from book_agent.workers.contracts import (
    CompiledTranslationContext,
    ConceptCandidate,
    ContextPacket,
    DiscourseBridge,
    PacketBlock,
    RelevantTerm,
    TranslatedContextBlock,
)

MAX_CHAPTER_MEMORY_TRANSLATIONS = 4
PROMOTED_PARAGRAPH_INTENTS = frozenset({"definition", "evidence"})
MAX_CONTEXT_SOURCE_BLOCKS_PER_SIDE = 1
MAX_CONTEXT_SOURCE_CHARS_PER_SIDE = 320
MAX_PREVIOUS_TRANSLATED_BLOCKS = 2
MAX_CONTEXT_BRIEF_CHARS = 220
MAX_SECTION_BRIEF_CHARS = 160
SHORT_CONTEXT_CURRENT_CHARS = 220
VERY_SHORT_CONTEXT_CURRENT_CHARS = 120
CONTEXT_BRIDGE_PREFIXES = (
    "however",
    "but",
    "instead",
    "therefore",
    "thus",
    "moreover",
    "meanwhile",
    "likewise",
    "for example",
    "for instance",
    "in other words",
    "by contrast",
    "as a result",
    "this",
    "these",
    "those",
    "such",
    "it",
    "they",
    "he",
    "she",
    "we",
)
CONTEXT_CONTINUATION_SUFFIXES = (":", ";", ",", "(", "[", "-", "/")
CONTEXT_TERMINAL_SUFFIXES = (".", "!", "?", ")", "]", "\"", "'", "”", "’")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
CONCEPT_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
SHIFT_STATEMENT_RE = re.compile(r"\bshift from\b", re.IGNORECASE)
CONCEPT_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
MAX_RELEVANT_CHAPTER_CONCEPTS = 4
MAX_ACTIVE_REFERENTS = 3

PARAGRAPH_ROLE_LABELS = {
    "analogy": "analogy",
    "definition": "concept definition",
    "evidence": "evidence-based reasoning",
    "exposition": "technical explanation",
    "summary": "summary",
    "transition": "transition",
}


@dataclass(frozen=True, slots=True)
class ChapterContextCompileOptions:
    include_memory_blocks: bool = True
    include_chapter_concepts: bool = True
    prefer_memory_chapter_brief: bool = True
    prefer_previous_translations_over_source_context: bool = True
    include_paragraph_intent: bool = True
    include_literalism_guardrails: bool = True
    trim_source_context: bool = True
    trim_chapter_brief: bool = True
    concept_overrides: tuple[ConceptCandidate, ...] = field(default_factory=tuple)


def _normalize_text(text: str | None) -> str:
    return " ".join(str(text or "").split()).strip()


def _coerce_nonnegative_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _unique_nonempty_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _packet_current_text(packet: ContextPacket) -> str:
    return _normalize_text(" ".join(block.text for block in packet.current_blocks if block.text))


def _concept_token_variants(token: str) -> set[str]:
    lowered = token.casefold()
    variants = {lowered}
    if lowered.endswith("ies") and len(lowered) > 4:
        variants.add(lowered[:-3] + "y")
    if lowered.endswith("es") and len(lowered) > 4:
        variants.add(lowered[:-2])
    if lowered.endswith("s") and len(lowered) > 3:
        variants.add(lowered[:-1])
    return {item for item in variants if item}


def _concept_relevance_haystack(packet: ContextPacket) -> tuple[str, set[str]]:
    parts = [
        *(block.text for block in packet.current_blocks if block.text),
        *(block.text for block in packet.prev_blocks if block.text),
        *(block.source_excerpt for block in packet.prev_translated_blocks if block.source_excerpt),
    ]
    haystack = _normalize_text(" ".join(parts)).casefold()
    token_set: set[str] = set()
    for raw_token in CONCEPT_TOKEN_RE.findall(haystack):
        token_set.update(_concept_token_variants(raw_token))
    return haystack, token_set


def _concept_matches_local_context(
    source_term: str,
    *,
    haystack: str,
    haystack_tokens: set[str],
) -> bool:
    normalized_term = _normalize_text(source_term).casefold()
    if not normalized_term:
        return False
    if normalized_term in haystack:
        return True
    term_tokens = [
        token.casefold()
        for token in CONCEPT_TOKEN_RE.findall(normalized_term)
        if token.casefold() not in CONCEPT_STOPWORDS
    ]
    if not term_tokens:
        return False
    return all(any(variant in haystack_tokens for variant in _concept_token_variants(token)) for token in term_tokens)


def _filter_relevant_concepts(packet: ContextPacket, concepts: list[ConceptCandidate]) -> list[ConceptCandidate]:
    if not concepts:
        return []
    haystack, haystack_tokens = _concept_relevance_haystack(packet)
    if not haystack and not haystack_tokens:
        return []
    filtered = [
        concept
        for concept in concepts
        if concept.source_term
        and _concept_matches_local_context(
            concept.source_term,
            haystack=haystack,
            haystack_tokens=haystack_tokens,
        )
    ]
    return filtered[:MAX_RELEVANT_CHAPTER_CONCEPTS]


def _filter_relevant_terms(packet: ContextPacket, terms: list[RelevantTerm]) -> list[RelevantTerm]:
    if not terms:
        return []
    haystack, haystack_tokens = _concept_relevance_haystack(packet)
    if not haystack and not haystack_tokens:
        return []
    return [
        term
        for term in terms
        if term.source_term
        and _concept_matches_local_context(
            term.source_term,
            haystack=haystack,
            haystack_tokens=haystack_tokens,
        )
    ]


def _starts_with_context_bridge(text: str) -> bool:
    lowered = _normalize_text(text).casefold()
    if not lowered:
        return False
    return any(lowered == marker or lowered.startswith(f"{marker} ") for marker in CONTEXT_BRIDGE_PREFIXES)


def _ends_like_continuation(text: str) -> bool:
    stripped = _normalize_text(text)
    if not stripped:
        return False
    if stripped.endswith(CONTEXT_CONTINUATION_SUFFIXES):
        return True
    return not stripped.endswith(CONTEXT_TERMINAL_SUFFIXES)


def _requires_chapter_brief_despite_previous_translations(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    return bool(SHIFT_STATEMENT_RE.search(normalized))


def _take_tail_blocks(blocks: list[PacketBlock], *, max_blocks: int, max_chars: int) -> list[PacketBlock]:
    selected: list[PacketBlock] = []
    total_chars = 0
    for block in reversed(blocks):
        text_len = len(_normalize_text(block.text))
        if selected and (len(selected) >= max_blocks or total_chars + text_len > max_chars):
            break
        selected.append(block)
        total_chars += text_len
        if len(selected) >= max_blocks:
            break
    return list(reversed(selected))


def _take_head_blocks(blocks: list[PacketBlock], *, max_blocks: int, max_chars: int) -> list[PacketBlock]:
    selected: list[PacketBlock] = []
    total_chars = 0
    for block in blocks:
        text_len = len(_normalize_text(block.text))
        if selected and (len(selected) >= max_blocks or total_chars + text_len > max_chars):
            break
        selected.append(block)
        total_chars += text_len
        if len(selected) >= max_blocks:
            break
    return selected


def _trim_source_context(
    packet: ContextPacket,
    *,
    has_previous_translation_context: bool,
) -> tuple[list[PacketBlock], list[PacketBlock]]:
    current_text = _packet_current_text(packet)
    current_len = len(current_text)
    current_block_type = packet.current_blocks[0].block_type if packet.current_blocks else ""
    keep_prev = bool(packet.prev_blocks) and (
        current_block_type != "paragraph"
        or (
            not has_previous_translation_context
            and (
                current_len <= SHORT_CONTEXT_CURRENT_CHARS
                or _starts_with_context_bridge(current_text)
            )
        )
    )
    keep_next = bool(packet.next_blocks) and (
        current_block_type != "paragraph"
        or _ends_like_continuation(current_text)
    )
    prev_blocks = (
        _take_tail_blocks(
            packet.prev_blocks,
            max_blocks=MAX_CONTEXT_SOURCE_BLOCKS_PER_SIDE,
            max_chars=MAX_CONTEXT_SOURCE_CHARS_PER_SIDE,
        )
        if keep_prev
        else []
    )
    next_blocks = (
        _take_head_blocks(
            packet.next_blocks,
            max_blocks=MAX_CONTEXT_SOURCE_BLOCKS_PER_SIDE,
            max_chars=MAX_CONTEXT_SOURCE_CHARS_PER_SIDE,
        )
        if keep_next
        else []
    )
    return prev_blocks, next_blocks


def _should_keep_previous_translated_blocks(packet: ContextPacket) -> bool:
    current_text = _packet_current_text(packet)
    current_len = len(current_text)
    current_block_type = packet.current_blocks[0].block_type if packet.current_blocks else ""
    if current_block_type != "paragraph":
        return True
    if current_len <= VERY_SHORT_CONTEXT_CURRENT_CHARS:
        return True
    if _starts_with_context_bridge(current_text):
        return True
    if _ends_like_continuation(current_text):
        return True
    if _requires_chapter_brief_despite_previous_translations(current_text):
        return True
    return False


def _select_previous_translated_blocks(
    packet: ContextPacket,
    blocks: list[TranslatedContextBlock],
) -> list[TranslatedContextBlock]:
    if not blocks:
        return []
    if not _should_keep_previous_translated_blocks(packet):
        # When chapter memory has already accumulated several accepted packets,
        # keep that memory slice even if the current paragraph looks locally
        # self-contained. This preserves terminology continuity across
        # nonadjacent packets without depending on hidden dialog state.
        if len(blocks) >= 3 and len(blocks) > len(packet.prev_translated_blocks):
            return blocks[-MAX_CHAPTER_MEMORY_TRANSLATIONS:]
        return []
    return blocks[-MAX_PREVIOUS_TRANSLATED_BLOCKS:]


def _compress_chapter_brief(brief: str) -> str:
    normalized = _normalize_text(brief)
    if len(normalized) <= MAX_CONTEXT_BRIEF_CHARS:
        return normalized
    sentences = [item.strip() for item in SENTENCE_SPLIT_RE.split(normalized) if item.strip()]
    if not sentences:
        return normalized[: MAX_CONTEXT_BRIEF_CHARS - 3].rstrip() + "..."
    selected: list[str] = []
    total_chars = 0
    for sentence in sentences:
        candidate_len = len(sentence) + (1 if selected else 0)
        if selected and total_chars + candidate_len > MAX_CONTEXT_BRIEF_CHARS:
            break
        if not selected and len(sentence) > MAX_CONTEXT_BRIEF_CHARS:
            return sentence[: MAX_CONTEXT_BRIEF_CHARS - 3].rstrip() + "..."
        selected.append(sentence)
        total_chars += candidate_len
    if not selected:
        return normalized[: MAX_CONTEXT_BRIEF_CHARS - 3].rstrip() + "..."
    compressed = " ".join(selected)
    if len(compressed) <= MAX_CONTEXT_BRIEF_CHARS:
        return compressed
    return compressed[: MAX_CONTEXT_BRIEF_CHARS - 3].rstrip() + "..."


def _trim_chapter_brief(
    packet: ContextPacket,
    *,
    prev_blocks: list[PacketBlock],
    next_blocks: list[PacketBlock],
    previous_translated_blocks: list[TranslatedContextBlock],
) -> str | None:
    brief = _normalize_text(packet.chapter_brief)
    if not brief:
        return None
    current_text = _packet_current_text(packet)
    current_len = len(current_text)
    current_block_type = packet.current_blocks[0].block_type if packet.current_blocks else ""
    has_previous_translation_context = bool(previous_translated_blocks)
    should_keep = (
        current_block_type != "paragraph"
        or (
            has_previous_translation_context
            and _requires_chapter_brief_despite_previous_translations(current_text)
        )
        or (
            not has_previous_translation_context
            and (
                current_len <= SHORT_CONTEXT_CURRENT_CHARS
                or (not prev_blocks and not next_blocks)
                or _starts_with_context_bridge(current_text)
            )
        )
        or _ends_like_continuation(current_text)
    )
    if not should_keep:
        return None
    return brief


def _preferred_chapter_brief(
    packet: ContextPacket,
    chapter_memory_snapshot: MemorySnapshot | None,
    *,
    prefer_memory_chapter_brief: bool,
) -> str | None:
    packet_brief = packet.chapter_brief
    if chapter_memory_snapshot is None or not prefer_memory_chapter_brief:
        return packet_brief
    memory_brief = chapter_memory_snapshot.content_json.get("chapter_brief")
    if not memory_brief:
        return packet_brief
    packet_brief_version = _coerce_nonnegative_int(packet.chapter_brief_version)
    memory_brief_version = _coerce_nonnegative_int(
        chapter_memory_snapshot.content_json.get("chapter_brief_version")
    )
    if packet_brief and packet_brief_version > memory_brief_version:
        return packet_brief
    return memory_brief


def _dedupe_translated_blocks(blocks: list[TranslatedContextBlock]) -> list[TranslatedContextBlock]:
    deduped: list[TranslatedContextBlock] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        key = (block.block_id, block.target_excerpt)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(block)
    return deduped


def _has_stale_literalism_memory(block: TranslatedContextBlock) -> bool:
    source_excerpt = str(block.source_excerpt or "").strip()
    target_excerpt = str(block.target_excerpt or "").strip()
    if not source_excerpt or not target_excerpt:
        return False
    for rule in STYLE_DRIFT_RULES:
        if rule.source_pattern.search(source_excerpt) and rule.target_pattern.search(target_excerpt):
            return True
    return False


def _conflicts_with_locked_terms(
    block: TranslatedContextBlock,
    relevant_terms: list[RelevantTerm],
) -> bool:
    source_excerpt = str(block.source_excerpt or "")
    target_excerpt = str(block.target_excerpt or "")
    source_lower = source_excerpt.casefold()
    for term in relevant_terms:
        if term.lock_level != "locked":
            continue
        source_term = str(term.source_term or "").strip()
        target_term = str(term.target_term or "").strip()
        if not source_term or not target_term:
            continue
        if source_term.casefold() not in source_lower:
            continue
        if target_term not in target_excerpt:
            return True
    return False


def _sanitize_previous_translated_blocks(
    blocks: list[TranslatedContextBlock],
    relevant_terms: list[RelevantTerm],
) -> list[TranslatedContextBlock]:
    sanitized: list[TranslatedContextBlock] = []
    for block in blocks:
        if _has_stale_literalism_memory(block):
            continue
        if _conflicts_with_locked_terms(block, relevant_terms):
            continue
        sanitized.append(block)
    return sanitized


def _memory_blocks(snapshot: MemorySnapshot | None) -> list[TranslatedContextBlock]:
    if snapshot is None:
        return []
    items = snapshot.content_json.get("recent_accepted_translations", [])
    if not isinstance(items, list):
        return []
    blocks: list[TranslatedContextBlock] = []
    for item in items[-MAX_CHAPTER_MEMORY_TRANSLATIONS:]:
        if not isinstance(item, dict):
            continue
        source_excerpt = str(item.get("source_excerpt") or "").strip()
        target_excerpt = str(item.get("target_excerpt") or "").strip()
        block_id = str(item.get("block_id") or "").strip()
        if not source_excerpt or not target_excerpt or not block_id:
            continue
        blocks.append(
            TranslatedContextBlock(
                block_id=block_id,
                source_excerpt=source_excerpt,
                target_excerpt=target_excerpt,
                source_sentence_ids=list(item.get("source_sentence_ids") or []),
            )
        )
    return blocks


def _memory_concepts(snapshot: MemorySnapshot | None) -> list[ConceptCandidate]:
    if snapshot is None:
        return []
    items = snapshot.content_json.get("active_concepts", [])
    if not isinstance(items, list):
        return []
    concepts: list[ConceptCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source_term = str(item.get("source_term") or "").strip()
        if not source_term:
            continue
        concepts.append(
            normalize_concept_candidate(
                ConceptCandidate(
                    source_term=source_term,
                    canonical_zh=str(item.get("canonical_zh")) if item.get("canonical_zh") else None,
                    status=str(item.get("status") or "candidate"),
                    confidence=float(item.get("confidence")) if item.get("confidence") is not None else None,
                    first_seen_packet_id=str(item.get("first_seen_packet_id")) if item.get("first_seen_packet_id") else None,
                    last_seen_packet_id=str(item.get("last_seen_packet_id")) if item.get("last_seen_packet_id") else None,
                    times_seen=int(item.get("times_seen") or 1),
                )
            )
        )
    return concepts


def _merge_concepts(
    base: list[ConceptCandidate],
    overrides: tuple[ConceptCandidate, ...],
) -> list[ConceptCandidate]:
    concept_map: dict[str, ConceptCandidate] = {
        concept.source_term.casefold(): concept for concept in base if concept.source_term
    }
    for concept in overrides:
        if not concept.source_term:
            continue
        concept_map[concept.source_term.casefold()] = concept
    concepts = list(concept_map.values())
    concepts.sort(
        key=lambda concept: (
            0 if concept.canonical_zh else 1,
            -(concept.times_seen or 0),
            concept.source_term.lower(),
        )
    )
    return concepts[:12]


def _merge_relevant_terms(
    base: list[RelevantTerm],
    concepts: list[ConceptCandidate],
) -> list[RelevantTerm]:
    merged: dict[str, RelevantTerm] = {}
    for term in base:
        if not term.source_term:
            continue
        merged[term.source_term.casefold()] = normalize_relevant_term(term)

    for concept in concepts:
        if not concept.source_term or not concept.canonical_zh:
            continue
        merged[concept.source_term.casefold()] = normalize_relevant_term(
            RelevantTerm(
                source_term=concept.source_term,
                target_term=concept.canonical_zh,
                lock_level="locked",
            )
        )

    ordered = list(merged.values())
    ordered.sort(
        key=lambda term: (
            {"locked": 0, "preferred": 1, "suggested": 2}.get(term.lock_level, 99),
            term.source_term.lower(),
            term.target_term.lower(),
        )
    )
    return ordered


def _infer_paragraph_intent_from_text(text: str, *, brief: str = "") -> tuple[str, str]:
    normalized = _normalize_text(text)
    if not normalized:
        return "", ""
    lowered = normalized.casefold()
    lowered_brief = _normalize_text(brief).casefold()

    intent = "exposition"
    hint = "Treat this as explanatory technical prose and keep the Chinese paragraph connected, stable, and readable."
    if any(
        marker in lowered
        for marker in (
            "refers to",
            "is what some are beginning to call",
            "in technical terms",
            "is the deliberate design",
        )
    ):
        intent = "definition"
        hint = "Treat this as concept-definition prose: choose concise, reusable terminology and make the definition read like publication-grade Chinese."
    elif any(marker in lowered for marker in ("recipe book", "personal chef", "chef", "pantry", "in other words", "likewise")):
        intent = "analogy"
        hint = "Treat this as an explanatory analogy: keep the imagery natural in Chinese and let the analogy flow before landing the technical point."
    elif any(marker in lowered for marker in ("this is where", "for now", "chapter 2 explores", "by contrast", "however")):
        intent = "transition"
        hint = "Treat this as a transition paragraph: keep the handoff explicit and smooth so the next technical point feels motivated."
    elif any(marker in lowered for marker in ("weight of evidence", "evidence shows", "in essence", "research shows", "studies show")):
        intent = "evidence"
        hint = "Treat this as evidence-based reasoning: prefer natural Chinese evidential phrasing over literal English weight metaphors."
    elif any(marker in lowered for marker in ("in summary", "overall", "in short", "therefore")):
        intent = "summary"
        hint = "Treat this as a summarizing paragraph: tighten the Chinese so the takeaway lands cleanly."
    elif "context engineering" in lowered_brief and "context engineering" in lowered:
        intent = "definition"
        hint = "Treat this as a chapter-defining concept paragraph: keep the core concept rendering concise, stable, and easy to reuse later."
    return intent, hint


def _infer_paragraph_intent(packet: ContextPacket, chapter_memory_snapshot: MemorySnapshot | None) -> dict[str, str]:
    current_text = " ".join(block.text for block in packet.current_blocks if block.text).strip()
    if not current_text:
        return {}
    brief = ""
    if chapter_memory_snapshot is not None:
        brief = str(chapter_memory_snapshot.content_json.get("chapter_brief") or "").casefold()
    if not brief:
        brief = str(packet.chapter_brief or "").casefold()
    intent, hint = _infer_paragraph_intent_from_text(current_text, brief=brief)

    return {
        "paragraph_intent": intent,
        "paragraph_intent_hint": hint,
    }


def _promoted_paragraph_intent(
    packet: ContextPacket,
    chapter_memory_snapshot: MemorySnapshot | None,
) -> dict[str, str]:
    intent_payload = _infer_paragraph_intent(packet, chapter_memory_snapshot)
    intent = str(intent_payload.get("paragraph_intent") or "").strip()
    if intent not in PROMOTED_PARAGRAPH_INTENTS:
        return {}
    return intent_payload


def _source_aware_literalism_guardrails(packet: ContextPacket) -> dict[str, str]:
    current_text = " ".join(block.text for block in packet.current_blocks if block.text).strip()
    if not current_text:
        return {}
    lines = source_aware_literalism_guardrail_lines(current_text)
    if not lines:
        return {}
    return {
        "literalism_guardrails": " || ".join(lines),
        "literalism_guardrail_count": str(len(lines)),
    }


def _paragraph_role_label(intent: str) -> str | None:
    normalized = _normalize_text(intent).casefold()
    if not normalized:
        return None
    return PARAGRAPH_ROLE_LABELS.get(normalized, normalized.replace("_", " "))


def _active_referents(
    *,
    current_text: str,
    relevant_terms: list[RelevantTerm],
    concepts: list[ConceptCandidate],
) -> list[str]:
    locked_terms = [term.source_term for term in relevant_terms if term.lock_level == "locked"]
    concept_terms = [concept.source_term for concept in concepts if concept.source_term]
    candidates = _unique_nonempty_strings([*locked_terms, *concept_terms])
    lowered_current = _normalize_text(current_text).casefold()

    def _referent_key(value: str) -> tuple[int, int, int, str]:
        position = lowered_current.find(value.casefold())
        if position >= 0:
            return (0, position, -len(value), value.casefold())
        return (1, 10_000, -len(value), value.casefold())

    ordered = sorted(candidates, key=_referent_key)
    return ordered[:MAX_ACTIVE_REFERENTS]


def _section_scope_label(packet: ContextPacket) -> str:
    if packet.heading_path:
        leaf = _normalize_text(packet.heading_path[-1])
        if leaf:
            return f"the section '{leaf}'"
    return "the current section"


def _section_anchor_referent(
    *,
    current_text: str,
    relevant_terms: list[RelevantTerm],
    concepts: list[ConceptCandidate],
) -> str | None:
    active_referents = _active_referents(
        current_text=current_text,
        relevant_terms=relevant_terms,
        concepts=concepts,
    )
    if not active_referents:
        return None
    return active_referents[0]


def _build_section_brief(
    packet: ContextPacket,
    *,
    chapter_memory_snapshot: MemorySnapshot | None,
    relevant_terms: list[RelevantTerm],
    concepts: list[ConceptCandidate],
) -> str | None:
    current_text = " ".join(block.text for block in packet.current_blocks if block.text).strip()
    if not current_text:
        return None
    brief = ""
    if chapter_memory_snapshot is not None:
        brief = str(chapter_memory_snapshot.content_json.get("chapter_brief") or "")
    if not brief:
        brief = str(packet.chapter_brief or "")
    intent, _hint = _infer_paragraph_intent_from_text(current_text, brief=brief)
    scope_label = _section_scope_label(packet)
    referent = _section_anchor_referent(
        current_text=current_text,
        relevant_terms=relevant_terms,
        concepts=concepts,
    )

    if intent == "definition" and referent:
        text = f"This part of {scope_label} defines {referent} and clarifies how it should be understood in the chapter."
    elif intent == "evidence" and referent:
        text = f"This part of {scope_label} presents evidence and implications for {referent}."
    elif intent == "analogy" and referent:
        text = f"This part of {scope_label} uses analogy to make {referent} concrete before returning to the technical point."
    elif intent == "transition" and referent:
        text = f"This part of {scope_label} shifts the discussion toward {referent} and sets up the next technical point."
    elif intent == "summary" and referent:
        text = f"This part of {scope_label} consolidates the takeaway about {referent}."
    elif referent:
        text = f"This part of {scope_label} continues the technical explanation about {referent}."
    elif intent == "transition":
        text = f"This part of {scope_label} acts as a local transition and sets up the next technical point."
    elif intent == "summary":
        text = f"This part of {scope_label} consolidates the local takeaway."
    else:
        text = f"This part of {scope_label} continues the chapter's technical explanation."

    return _compress_chapter_brief(text)[:MAX_SECTION_BRIEF_CHARS].rstrip()


def _previous_source_excerpt(packet: ContextPacket) -> str:
    if packet.prev_blocks:
        return _normalize_text(packet.prev_blocks[-1].text)
    if packet.prev_translated_blocks:
        return _normalize_text(packet.prev_translated_blocks[-1].source_excerpt)
    return ""


def _relation_to_previous(
    *,
    previous_role: str | None,
    current_role: str | None,
    current_text: str,
) -> str | None:
    if not current_role:
        return None
    if not previous_role:
        if _starts_with_context_bridge(current_text):
            return f"opens with an explicit bridge and then moves into {current_role}"
        return f"opens a new local point as {current_role}"
    if previous_role == current_role:
        if current_role == "technical explanation":
            return "continues the same technical explanation"
        return f"continues the same {current_role}"

    special_cases = {
        ("analogy", "concept definition"): "moves from analogy into concept definition",
        ("concept definition", "evidence-based reasoning"): "moves from concept definition into evidence and implications",
        ("technical explanation", "evidence-based reasoning"): "moves from explanation into evidence and justification",
        ("technical explanation", "transition"): "shifts from explanation into a local transition",
        ("transition", "concept definition"): "uses a transition to enter a new concept definition",
    }
    if (previous_role, current_role) in special_cases:
        return special_cases[(previous_role, current_role)]
    if _starts_with_context_bridge(current_text):
        return f"uses an explicit bridge to move from {previous_role} into {current_role}"
    return f"moves from {previous_role} into {current_role}"


def _build_discourse_bridge(
    packet: ContextPacket,
    *,
    chapter_memory_snapshot: MemorySnapshot | None,
    relevant_terms: list[RelevantTerm],
    concepts: list[ConceptCandidate],
) -> DiscourseBridge | None:
    current_text = " ".join(block.text for block in packet.current_blocks if block.text).strip()
    if not current_text:
        return None
    brief = ""
    if chapter_memory_snapshot is not None:
        brief = str(chapter_memory_snapshot.content_json.get("chapter_brief") or "")
    if not brief:
        brief = str(packet.chapter_brief or "")
    current_intent, _current_hint = _infer_paragraph_intent_from_text(current_text, brief=brief)
    previous_text = _previous_source_excerpt(packet)
    previous_intent, _previous_hint = _infer_paragraph_intent_from_text(previous_text, brief=brief) if previous_text else ("", "")
    current_role = _paragraph_role_label(current_intent)
    previous_role = _paragraph_role_label(previous_intent)
    relation = _relation_to_previous(
        previous_role=previous_role,
        current_role=current_role,
        current_text=current_text,
    )
    active_referents = _active_referents(
        current_text=current_text,
        relevant_terms=relevant_terms,
        concepts=concepts,
    )
    if not any([previous_role, current_role, relation, active_referents]):
        return None
    return DiscourseBridge(
        previous_paragraph_role=previous_role,
        current_paragraph_role=current_role,
        relation_to_previous=relation,
        active_referents=active_referents,
    )


@dataclass(slots=True)
class ChapterContextCompiler:
    compile_version: str = "v4.section-brief-discourse-bridge"

    def compile(
        self,
        packet: ContextPacket,
        *,
        chapter_memory_snapshot: MemorySnapshot | None,
        options: ChapterContextCompileOptions | None = None,
    ) -> CompiledTranslationContext:
        compile_options = options or ChapterContextCompileOptions()
        memory_blocks = _memory_blocks(chapter_memory_snapshot) if compile_options.include_memory_blocks else []
        memory_concepts = _memory_concepts(chapter_memory_snapshot) if compile_options.include_chapter_concepts else []
        merged_concepts = _filter_relevant_concepts(
            packet,
            _merge_concepts(memory_concepts, compile_options.concept_overrides),
        )
        merged_terms = _filter_relevant_terms(
            packet,
            _merge_relevant_terms(packet.relevant_terms, merged_concepts),
        )
        merged_previous = _dedupe_translated_blocks([*memory_blocks, *packet.prev_translated_blocks])
        sanitized_previous = _sanitize_previous_translated_blocks(merged_previous, merged_terms)
        selected_previous = _select_previous_translated_blocks(packet, sanitized_previous)
        compiled_brief = _preferred_chapter_brief(
            packet,
            chapter_memory_snapshot,
            prefer_memory_chapter_brief=compile_options.prefer_memory_chapter_brief,
        )
        if compile_options.trim_chapter_brief and compiled_brief:
            compiled_brief = _compress_chapter_brief(compiled_brief)
        compiled_packet = packet
        has_previous_translation_context = (
            compile_options.prefer_previous_translations_over_source_context
            and bool(selected_previous)
        )
        if compile_options.trim_source_context:
            trimmed_prev_blocks, trimmed_next_blocks = _trim_source_context(
                packet,
                has_previous_translation_context=has_previous_translation_context,
            )
            compiled_packet = compiled_packet.model_copy(
                update={
                    "prev_blocks": trimmed_prev_blocks,
                    "next_blocks": trimmed_next_blocks,
                }
            )
        else:
            trimmed_prev_blocks = compiled_packet.prev_blocks
            trimmed_next_blocks = compiled_packet.next_blocks
        prompt_brief = compiled_brief
        if compile_options.trim_chapter_brief:
            prompt_brief = _trim_chapter_brief(
                compiled_packet.model_copy(update={"chapter_brief": compiled_brief}),
                prev_blocks=trimmed_prev_blocks,
                next_blocks=trimmed_next_blocks,
                previous_translated_blocks=selected_previous if has_previous_translation_context else [],
            )
        merged_style_constraints = dict(packet.style_constraints)
        if compiled_brief and prompt_brief is None:
            merged_style_constraints["suppress_chapter_brief_in_prompt"] = True
        else:
            merged_style_constraints.pop("suppress_chapter_brief_in_prompt", None)
        if compile_options.include_paragraph_intent:
            merged_style_constraints.update(_promoted_paragraph_intent(packet, chapter_memory_snapshot))
        if compile_options.include_literalism_guardrails:
            merged_style_constraints.update(_source_aware_literalism_guardrails(packet))
        section_brief = _build_section_brief(
            packet,
            chapter_memory_snapshot=chapter_memory_snapshot,
            relevant_terms=merged_terms,
            concepts=merged_concepts,
        )
        discourse_bridge = _build_discourse_bridge(
            packet,
            chapter_memory_snapshot=chapter_memory_snapshot,
            relevant_terms=merged_terms,
            concepts=merged_concepts,
        )
        merged_packet = compiled_packet.model_copy(
            update={
                "chapter_brief": compiled_brief,
                "section_brief": section_brief,
                "discourse_bridge": discourse_bridge,
                "chapter_concepts": merged_concepts,
                "relevant_terms": merged_terms,
                "prev_translated_blocks": selected_previous,
                "style_constraints": merged_style_constraints,
            }
        )
        return CompiledTranslationContext.from_context_packet(
            merged_packet,
            context_compile_version=self.compile_version,
            memory_version_used=chapter_memory_snapshot.version if chapter_memory_snapshot is not None else None,
            compile_metadata={
                "chapter_memory_available": chapter_memory_snapshot is not None,
                "trimmed_prev_block_count": len(trimmed_prev_blocks),
                "trimmed_next_block_count": len(trimmed_next_blocks),
                "selected_prev_translated_block_count": len(selected_previous),
                "section_brief_present": bool(section_brief),
                "discourse_bridge_present": discourse_bridge is not None,
            },
        )
