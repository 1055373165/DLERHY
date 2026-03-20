from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from book_agent.core.ids import stable_id
from book_agent.domain.models import Sentence
from book_agent.workers.contracts import (
    AlignmentSuggestion,
    ContextPacket,
    TranslationTargetSegment,
    TranslationUsage,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)


def _format_section(title: str, lines: list[str]) -> list[str]:
    return [title, *lines]


def _extend_section(sections: list[str], title: str, lines: list[str]) -> None:
    if not lines:
        return
    sections.extend(_format_section(title, lines))


def _sorted_term_lines(packet: ContextPacket) -> list[str]:
    if not packet.relevant_terms:
        return []
    ordered = sorted(
        packet.relevant_terms,
        key=lambda term: (
            {"locked": 0, "preferred": 1, "suggested": 2}.get(term.lock_level, 99),
            term.source_term.lower(),
            term.target_term.lower(),
        ),
    )
    return [f"- {term.source_term} => {term.target_term} ({term.lock_level})" for term in ordered]


def _sorted_entity_lines(packet: ContextPacket) -> list[str]:
    if not packet.relevant_entities:
        return []
    ordered = sorted(
        packet.relevant_entities,
        key=lambda entity: (entity.entity_type.lower(), entity.name.lower()),
    )
    return [
        f"- {entity.name} [{entity.entity_type}] => {entity.canonical_zh or '(unset)'}"
        for entity in ordered
    ]


def _concept_lines(packet: ContextPacket) -> list[str]:
    if not packet.chapter_concepts:
        return []
    ordered = sorted(
        packet.chapter_concepts,
        key=lambda concept: (
            0 if concept.canonical_zh else 1,
            -(concept.times_seen or 0),
            concept.source_term.lower(),
        ),
    )
    lines: list[str] = []
    for concept in ordered:
        canonical = concept.canonical_zh or "(translation not locked yet)"
        details = [concept.status]
        if concept.times_seen:
            details.append(f"seen={concept.times_seen}")
        lines.append(f"- {concept.source_term} => {canonical} ({', '.join(details)})")
    return lines


def _trim_prompt_excerpt(text: str, *, max_chars: int) -> str:
    normalized = " ".join((text or "").split()).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _prompt_concept_lines(packet: ContextPacket, *, minimal: bool) -> list[str]:
    if not packet.chapter_concepts:
        return []
    ordered = sorted(
        packet.chapter_concepts,
        key=lambda concept: (
            0 if concept.canonical_zh else 1,
            -(concept.times_seen or 0),
            concept.source_term.lower(),
        ),
    )
    if minimal:
        ordered = [
            concept
            for concept in ordered
            if concept.canonical_zh or concept.status in {"locked", "preferred"}
        ][:2]
    lines: list[str] = []
    for concept in ordered:
        canonical = concept.canonical_zh or "(translation not locked yet)"
        details = [concept.status]
        if concept.times_seen:
            details.append(f"seen={concept.times_seen}")
        lines.append(f"- {concept.source_term} => {canonical} ({', '.join(details)})")
    return lines


def _prompt_previous_translation_lines(packet: ContextPacket, *, minimal: bool) -> list[str]:
    if not packet.prev_translated_blocks:
        return []
    if not minimal:
        return [
            f"- {item.source_excerpt} => {item.target_excerpt}"
            for item in packet.prev_translated_blocks
        ]
    narrowed = packet.prev_translated_blocks[-2:]
    return [
        f"- {_trim_prompt_excerpt(item.source_excerpt, max_chars=140)} => {_trim_prompt_excerpt(item.target_excerpt, max_chars=140)}"
        for item in narrowed
    ]


def _packet_block_lines(blocks: list[PacketBlock]) -> list[str]:
    if not blocks:
        return []
    lines: list[str] = []
    for index, block in enumerate(blocks, start=1):
        excerpt = " ".join((block.text or "").split())
        lines.append(f"- B{index} [{block.block_type}] {excerpt}")
    return lines


def _current_paragraph_lines(packet: ContextPacket) -> list[str]:
    if not packet.current_blocks:
        return []
    lines: list[str] = []
    for index, block in enumerate(packet.current_blocks, start=1):
        lines.append(f"- P{index} [{block.block_type}] {block.text}")
    return lines


def _paragraph_intent_lines(packet: ContextPacket) -> list[str]:
    intent = str(packet.style_constraints.get("paragraph_intent") or "").strip()
    hint = str(packet.style_constraints.get("paragraph_intent_hint") or "").strip()
    if not intent and not hint:
        return []
    lines: list[str] = []
    if intent:
        lines.append(f"- Intent: {intent}")
    if hint:
        lines.append(f"- Hint: {hint}")
    return lines


def _literalism_guardrail_lines(packet: ContextPacket) -> list[str]:
    raw = str(packet.style_constraints.get("literalism_guardrails") or "").strip()
    if not raw:
        return []
    lines = [part.strip() for part in raw.split("||") if part.strip()]
    if not lines:
        return []
    return [f"- {line}" for line in lines]


def _chapter_brief_visible(packet: ContextPacket) -> bool:
    if not packet.chapter_brief:
        return False
    return not bool(packet.style_constraints.get("suppress_chapter_brief_in_prompt"))


def _compact_prompt_candidate(packet: ContextPacket, *, current_sentence_count: int, prompt_profile: PromptProfile) -> bool:
    if prompt_profile not in COMPACT_PROMPT_PROFILES:
        return False
    if not packet.current_blocks:
        return False
    if any(block.block_type != "paragraph" for block in packet.current_blocks):
        return False
    if len(packet.current_blocks) > 3:
        return False
    current_text = " ".join((block.text or "").strip() for block in packet.current_blocks).strip()
    if not current_text or len(current_text) > 720:
        return False
    if current_sentence_count == 0 or current_sentence_count > 4:
        return False
    if packet.relevant_entities or packet.open_questions:
        return False
    if any(
        str(packet.style_constraints.get(key) or "").strip()
        for key in ("paragraph_intent", "paragraph_intent_hint", "literalism_guardrails")
    ):
        return False
    return True


@dataclass(frozen=True, slots=True)
class TranslationWorkerMetadata:
    worker_name: str
    model_name: str
    prompt_version: str
    runtime_config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TranslationTask:
    context_packet: ContextPacket
    current_sentences: list[Sentence]


class TranslationWorker(Protocol):
    def metadata(self) -> TranslationWorkerMetadata:
        ...

    def translate(self, task: TranslationTask) -> TranslationWorkerResult | TranslationWorkerOutput:
        ...


@dataclass(frozen=True, slots=True)
class TranslationPromptRequest:
    packet_id: str
    model_name: str
    prompt_version: str
    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any]
    sentence_alias_map: dict[str, str] = field(default_factory=dict)


PromptLayout = Literal["paragraph-led", "sentence-led"]
PromptProfile = Literal[
    "current",
    "role-style-v2",
    "role-style-memory-v2",
    "role-style-brief-v3",
    "material-aware-v1",
    "material-aware-minimal-v1",
]
TranslationMaterial = Literal[
    "general_nonfiction",
    "technical_book",
    "academic_paper",
    "technical_blog",
    "business_document",
]

MATERIAL_AWARE_PROMPT_PROFILES = frozenset({"material-aware-v1", "material-aware-minimal-v1"})
COMPACT_PROMPT_PROFILES = frozenset({"role-style-v2", "material-aware-minimal-v1"})


def _resolve_translation_material(packet: ContextPacket) -> TranslationMaterial:
    raw_material = (
        packet.style_constraints.get("translation_material")
        or packet.style_constraints.get("material_profile")
        or packet.style_constraints.get("translation_mode")
    )
    normalized = str(raw_material or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "academic": "academic_paper",
        "academic_paper": "academic_paper",
        "paper": "academic_paper",
        "tech_book": "technical_book",
        "technical_book": "technical_book",
        "book_technical": "technical_book",
        "technical_blog": "technical_blog",
        "tech_blog": "technical_blog",
        "blog": "technical_blog",
        "business": "business_document",
        "business_document": "business_document",
        "business_doc": "business_document",
        "general": "general_nonfiction",
        "general_nonfiction": "general_nonfiction",
        "nonfiction": "general_nonfiction",
    }
    return aliases.get(normalized, "general_nonfiction")  # type: ignore[return-value]


def _has_memory_context(packet: ContextPacket) -> bool:
    return bool(
        packet.relevant_terms
        or packet.chapter_concepts
        or packet.prev_translated_blocks
        or _chapter_brief_visible(packet)
    )


def _material_specific_style_lines(
    material: TranslationMaterial,
    *,
    minimal: bool,
) -> list[str]:
    if material == "academic_paper":
        if minimal:
            return [
                "- Use formal, rigorous Chinese academic prose and keep argument structure explicit.",
                "- Stay faithful to claims, scope, evidence, and limitations; do not popularize or embellish.",
            ]
        return [
            "- Write in formal Chinese academic prose suitable for a computer science paper.",
            "- Preserve claim structure, scope, evidence, and logical qualifiers without adding commentary.",
            "- Keep terminology precise, consistent, and aligned with common Chinese academic usage.",
            "- Avoid conversational phrasing and avoid literal English-shaped sentence shells when cleaner academic Chinese exists.",
        ]
    if material == "technical_book":
        if minimal:
            return [
                "- Write like a native Chinese technical author: concise, plain, and publication-ready.",
                "- Avoid translationese, abstract noun-chain calques, and overly literary wording.",
                "- Prefer natural Chinese technical-book patterns for shift statements and perspective phrases instead of stiff literal mirroring.",
                "- Prefer plain technical-book wording when faithful, not inflated or literary diction.",
            ]
        return [
            "- Write like a native Chinese computer-science book author, not a sentence-by-sentence converter.",
            "- Prefer concise, natural Chinese technical prose over English-shaped long sentences.",
            "- Keep terminology stable and professional, but avoid inflated literary diction or dramatic rhetoric.",
            "- When English perspective phrases sound heavy in Chinese, simplify them into natural technical-book phrasing without changing meaning.",
            "- For shift statements, prefer natural Chinese structures when they preserve source meaning better than literal mirroring.",
        ]
    if material == "technical_blog":
        if minimal:
            return [
                "- Keep the tone direct, technically faithful, and easy for Chinese engineers to scan.",
                "- Prefer clear practitioner language over academic or literary phrasing.",
            ]
        return [
            "- Write like a strong Chinese engineering blog editor: direct, clear, and technically faithful.",
            "- Prefer short, readable Chinese sentences when the source uses conversational technical exposition.",
            "- Keep examples, caveats, and practical takeaways explicit, but do not add new explanation.",
        ]
    if material == "business_document":
        if minimal:
            return [
                "- Keep the prose professional, restrained, and executive-readable.",
                "- Preserve risk, responsibility, and decision-impact language with clean Chinese wording.",
            ]
        return [
            "- Write in professional Chinese suitable for executive or enterprise readers.",
            "- Preserve risk, governance, and operational nuance while keeping the prose restrained and clear.",
            "- Avoid literary embellishment and avoid translating corporate perspective phrases too literally.",
        ]
    if minimal:
        return [
            "- Keep the Chinese fluent, faithful, and publication-ready.",
            "- Prefer natural Chinese syntax over literal English mirroring.",
        ]
    return [
        "- Write in fluent, publication-ready Chinese rather than sentence-by-sentence translationese.",
        "- Preserve technical meaning and discourse flow while avoiding literal English syntax carryover.",
        "- Keep terminology stable across the packet and choose concise Chinese phrasing when multiple faithful options exist.",
    ]


def _material_memory_handling_lines(
    packet: ContextPacket,
    *,
    material: TranslationMaterial,
    minimal: bool,
) -> list[str]:
    if not _has_memory_context(packet):
        return []
    lines = [
        "- Treat locked terms and established concept renderings as authoritative whenever they match the source.",
    ]
    if packet.prev_translated_blocks:
        lines.append(
            "- Use Previous Accepted Translations to preserve local discourse continuity and avoid term drift across neighboring paragraphs."
        )
    if packet.chapter_concepts:
        lines.append(
            "- Reuse chapter concept memory for recurring concepts unless the current packet clearly redefines them."
        )
    if _chapter_brief_visible(packet) and not minimal:
        if material == "academic_paper":
            lines.append(
                "- Use Chapter Brief only to recover the paragraph's rhetorical role in the paper, not to paraphrase beyond the source."
            )
        else:
            lines.append(
                "- Use Chapter Brief to understand why the paragraph exists in the chapter, then express that intent in natural Chinese."
            )
    lines.append(
        "- Keep uncertainty out of the translated body; report it only through structured low_confidence_flags or notes."
    )
    return lines


def _material_contract_lines(
    *,
    material: TranslationMaterial,
    prompt_layout: PromptLayout,
    minimal: bool,
) -> list[str]:
    if prompt_layout == "sentence-led":
        opening = "- Translate every current sentence faithfully into Chinese while keeping the packet coherent as one paragraph."
    elif material == "academic_paper":
        opening = "- Translate the current passage into formal, high-fidelity Chinese academic prose."
    elif material == "technical_book":
        opening = "- Translate the current paragraph into native, publication-ready Chinese technical-book prose."
    elif material == "technical_blog":
        opening = "- Translate the current paragraph into direct, natural Chinese technical writing for practitioners."
    else:
        opening = "- Translate the current paragraph into natural, publication-ready Chinese."

    common = [
        opening,
        "- Preserve logic, causality, constraints, and technical detail; do not add, omit, or soften meaning.",
        "- Keep alignment coverage complete and use only the provided sentence aliases in source_sentence_ids and low_confidence_flags.sentence_id.",
        "- Return JSON that matches the provided response schema.",
    ]
    if minimal:
        return common
    common.insert(
        2,
        "- Reuse canonical Chinese renderings for locked terms or established concepts unless the source explicitly introduces a new definition.",
    )
    if material == "academic_paper":
        common.insert(
            3,
            "- Keep the register formal and precise enough for introduction, method, related work, result, or conclusion sections.",
        )
    elif material == "technical_book":
        common.insert(
            3,
            "- When English long sentences sound stiff in Chinese, split or reshape them into smoother technical-book prose without losing structure.",
        )
    elif material == "technical_blog":
        common.insert(
            3,
            "- Favor concise, engineer-friendly phrasing over academic or literary wording while staying fully faithful to the source.",
        )
    return common


def _material_system_prompt(material: TranslationMaterial, *, minimal: bool) -> str:
    if material == "academic_paper":
        if minimal:
            return (
                "You are a specialist English-to-Chinese translator for computer science papers. "
                "Produce formal, rigorous Chinese academic prose, preserve meaning exactly, and keep alignment coverage complete."
            )
        return (
            "You are a specialist English-to-Chinese translator for computer science and machine-learning papers. "
            "Translate into formal, rigorous, publication-ready Chinese academic prose. Preserve terminology, logic, qualifiers, scope, and evidence precisely. "
            "Avoid literal English-shaped syntax when cleaner academic Chinese conveys the same meaning. Alignment coverage must remain complete."
        )
    if material == "technical_book":
        if minimal:
            return (
                "You are a senior translator of English technical books into Chinese. "
                "Produce native, fluent Chinese technical prose for mainland-China readers while preserving meaning and alignment coverage."
            )
        return (
            "You are a senior translator of English computer-science and software-engineering books into Chinese. "
            "Produce native, publication-ready Chinese technical prose that reads like it was originally written by a strong Chinese technical author. "
            "Preserve logic, technical detail, and terminology consistency, but avoid translationese, rigid English sentence mirroring, and overly literary wording. "
            "Alignment coverage must remain complete."
        )
    if material == "technical_blog":
        return (
            "You are a senior English-to-Chinese translator for technical blog posts and engineering articles. "
            "Keep the translation technically faithful, direct, and readable for Chinese engineers, with complete alignment coverage."
        )
    if material == "business_document":
        return (
            "You are a senior English-to-Chinese translator for business and enterprise technical writing. "
            "Produce accurate, restrained, professional Chinese that preserves operational nuance and keeps alignment coverage complete."
        )
    return (
        "You are a senior English-to-Chinese translator for professional nonfiction and technical writing. "
        "Produce accurate, fluent, publication-ready Chinese, preserve terminology and logic, and keep alignment coverage complete."
    )


class TranslationModelClient(Protocol):
    def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerResult | TranslationWorkerOutput:
        ...


def build_translation_prompt_request(
    task: TranslationTask,
    *,
    model_name: str,
    prompt_version: str,
    prompt_layout: PromptLayout = "paragraph-led",
    prompt_profile: PromptProfile = "current",
    allow_compact_prompt: bool = True,
) -> TranslationPromptRequest:
    packet = task.context_packet
    translation_material = _resolve_translation_material(packet)
    material_aware_prompt = prompt_profile in MATERIAL_AWARE_PROMPT_PROFILES
    minimal_material_prompt = prompt_profile == "material-aware-minimal-v1"
    heading_path = " > ".join(packet.heading_path) if packet.heading_path else "(root)"
    term_lines = _sorted_term_lines(packet)
    entity_lines = _sorted_entity_lines(packet)
    concept_lines = _prompt_concept_lines(packet, minimal=minimal_material_prompt)
    compact_prompt = allow_compact_prompt and _compact_prompt_candidate(
        packet,
        current_sentence_count=len(task.current_sentences),
        prompt_profile=prompt_profile,
    )
    sentence_alias_map = {
        f"S{index}": sentence.id for index, sentence in enumerate(task.current_sentences, start=1)
    }
    if prompt_layout == "paragraph-led" and len(task.current_sentences) == 1:
        only_alias = next(iter(sentence_alias_map.keys()), "S1")
        sentence_lines = [f"- [{only_alias}] This is the only sentence in the current paragraph."]
    else:
        sentence_lines = [
            f"{index}. [{alias}] {sentence.source_text}"
            for index, (alias, sentence) in enumerate(
                zip(sentence_alias_map.keys(), task.current_sentences, strict=False),
                start=1,
            )
        ]
    previous_translation_lines = _prompt_previous_translation_lines(
        packet,
        minimal=minimal_material_prompt,
    )
    prev_block_lines = _packet_block_lines(packet.prev_blocks)
    next_block_lines = _packet_block_lines(packet.next_blocks)
    current_paragraph_lines = _current_paragraph_lines(packet)
    paragraph_intent_lines = _paragraph_intent_lines(packet)
    literalism_guardrail_lines = _literalism_guardrail_lines(packet)
    if minimal_material_prompt and prompt_layout == "paragraph-led":
        alias_order = list(sentence_alias_map.keys())
        if len(alias_order) == 1 and current_paragraph_lines:
            current_paragraph_lines[0] = current_paragraph_lines[0].replace(
                "- P1 [paragraph] ",
                f"- P1 [paragraph] [{alias_order[0]}] ",
                1,
            )
            sentence_lines = []
        elif alias_order:
            sentence_lines = [f"- Use sentence aliases in order: {', '.join(alias_order)}."]
    if material_aware_prompt:
        contract_lines = _material_contract_lines(
            material=translation_material,
            prompt_layout=prompt_layout,
            minimal=minimal_material_prompt,
        )
    elif compact_prompt:
        contract_lines = [
            "- Translate the current paragraph into natural, publication-ready Chinese.",
            "- Preserve meaning, keep wording consistent within the packet, and maintain complete alignment coverage.",
            "- Use only the provided sentence aliases in source_sentence_ids and low_confidence_flags.sentence_id.",
            "- Return JSON that matches the provided response schema.",
        ]
        if prompt_layout == "sentence-led":
            contract_lines[0] = (
                "- Translate every current sentence faithfully into Chinese while keeping the paragraph fluent as a whole."
            )
    else:
        contract_lines = [
            "- Translate the current paragraph into natural Chinese at paragraph level first, then ensure sentence-level coverage is complete.",
            "- Reuse the canonical Chinese rendering of any locked or previously established concept.",
            "- If a concept already appears in Previous Accepted Translations, keep the same Chinese term unless the current packet explicitly redefines it.",
            "- Preserve meaning, preserve protected spans, and maintain complete alignment coverage.",
            "- Use the sentence ledger only for alignment, coverage, and low-confidence flags.",
            "- Use only the sentence aliases shown below (for example: S1, S2) in source_sentence_ids and low_confidence_flags.sentence_id.",
            "- Return JSON that matches the provided response schema.",
        ]
        if prompt_layout == "sentence-led":
            contract_lines[0] = (
                "- Translate every current sentence faithfully into Chinese, but keep paragraph-level coherence across the full packet."
            )
            contract_lines[4] = "- Use the current sentences as the primary translation ledger and keep paragraph context coherent."

    style_lines: list[str] = []
    memory_handling_lines: list[str] = []
    if material_aware_prompt:
        style_lines = _material_specific_style_lines(
            translation_material,
            minimal=minimal_material_prompt,
        )
        memory_handling_lines = _material_memory_handling_lines(
            packet,
            material=translation_material,
            minimal=minimal_material_prompt,
        )
    elif not compact_prompt and prompt_profile in {"role-style-v2", "role-style-memory-v2", "role-style-brief-v3"}:
        style_lines = [
            "- Write like a polished Chinese technical translator, not a sentence-by-sentence converter.",
            "- Prefer established Chinese technical phrasing and avoid literal calques of English abstract noun chains.",
            "- Keep terminology stable across the packet and maintain a professional, readable register.",
            "- Preserve rhetorical emphasis, but do not over-fragment paragraphs unless the source clearly intends it.",
        ]
    if not compact_prompt and prompt_profile == "role-style-memory-v2":
        memory_handling_lines = [
            "- Treat Locked and Relevant Terms as authoritative whenever they match the source.",
            "- Treat locked Chapter Concept Memory as the default rendering for recurring concepts unless the current packet explicitly redefines them.",
            "- Use Previous Accepted Translations to continue local discourse and terminology continuity across paragraphs.",
            "- If wording remains ambiguous or risky, keep the translated body clean and report the uncertainty only via structured low_confidence_flags or notes.",
        ]
    elif not compact_prompt and prompt_profile == "role-style-brief-v3":
        memory_handling_lines = [
            "- Read Chapter Brief as the purpose summary of this section: use it to infer why the current paragraph exists, not just what words appear nearby.",
            "- Treat Locked and Relevant Terms as authoritative whenever they match the source.",
            "- Treat locked Chapter Concept Memory as the default rendering for recurring concepts unless the current packet explicitly redefines them.",
            "- If a high-signal concept is still unlocked, choose the most publication-ready Chinese rendering that fits the current chapter brief and keep it stable across the packet.",
            "- Use Previous Accepted Translations to preserve discourse continuity, reference chains, and recently established wording across neighboring paragraphs.",
            "- Keep the translated body clean and publication-ready; never insert inline translator notes. Put uncertainty only into structured low_confidence_flags or notes.",
        ]

    sections = [
        *_format_section("Core Translation Contract:", contract_lines),
    ]
    if style_lines:
        style_title = "Chinese Style Priorities:"
        if material_aware_prompt:
            style_title = "Material-Specific Style Target:"
        _extend_section(sections, style_title, style_lines)
    if (
        (not compact_prompt and prompt_profile in {"role-style-v2", "role-style-memory-v2", "role-style-brief-v3"})
        or prompt_profile in {"material-aware-v1", "material-aware-minimal-v1"}
    ):
        _extend_section(sections, "Paragraph Intent Signal:", paragraph_intent_lines)
        _extend_section(sections, "Source-Aware Literalism Guardrails:", literalism_guardrail_lines)
    if memory_handling_lines:
        _extend_section(sections, "Memory and Ambiguity Handling:", memory_handling_lines)
    if not compact_prompt and prompt_profile == "role-style-brief-v3":
        _extend_section(
            sections,
            "Paragraph Intent Priorities:",
            [
                "- Understand the paragraph's role in the chapter before translating: definition, analogy, transition, argument, caution, or summary.",
                "- Prefer a connected Chinese paragraph that reads as if written by a professional translator, not as sentence fragments stitched together.",
                "- Keep core concepts concise and reusable so the same rendering can survive later packets and reviews.",
            ],
        )
        _extend_section(
            sections,
            "Literalism Guardrails:",
            [
                "- Do not calque English evidential phrases into awkward weight metaphors; prefer natural Chinese forms such as '大量证据表明' or '现有证据表明'.",
                "- For contextual fit, prefer natural Chinese expressions such as '更符合上下文' or '更贴合语境', not literal forms like '上下文更准确'.",
                "- For phrases like 'contextually accurate outputs', rewrite them as '更符合上下文的输出' or an equally natural Chinese expression, not '上下文更准确的输出'.",
                "- When an English noun phrase names a field, discipline, or methodology, prefer an established Chinese concept name over a word-for-word rendering.",
            ],
        )
    if packet.open_questions:
        _extend_section(
            sections,
            "Open Questions and Rerun Hints:",
            [f"- {question}" for question in packet.open_questions],
        )
    section_context_lines: list[str] = []
    chapter_brief_visible = _chapter_brief_visible(packet)
    if not minimal_material_prompt or not chapter_brief_visible:
        section_context_lines.append(f"- Heading Path: {heading_path}")
    if chapter_brief_visible:
        section_context_lines.append(f"- Chapter Brief: {packet.chapter_brief}")
    if material_aware_prompt and not minimal_material_prompt:
        section_context_lines.append(f"- Translation Material: {translation_material}")
    _extend_section(sections, "Section Context:", section_context_lines)
    _extend_section(sections, "Locked and Relevant Terms:", term_lines)
    _extend_section(sections, "Relevant Entities:", entity_lines)
    _extend_section(sections, "Chapter Concept Memory:", concept_lines)
    _extend_section(sections, "Previous Accepted Translations (same local context):", previous_translation_lines)
    _extend_section(sections, "Previous Source Context:", prev_block_lines)
    _extend_section(sections, "Upcoming Source Context:", next_block_lines)
    if prompt_layout == "sentence-led":
        _extend_section(sections, "Current Sentences:", sentence_lines)
        _extend_section(sections, "Current Paragraph:", current_paragraph_lines)
    else:
        _extend_section(sections, "Current Paragraph:", current_paragraph_lines)
        _extend_section(sections, "Sentence Ledger:", sentence_lines)
    user_prompt = "\n".join(sections)
    if material_aware_prompt:
        system_prompt = _material_system_prompt(
            translation_material,
            minimal=minimal_material_prompt,
        )
    elif compact_prompt:
        system_prompt = (
            "You are a professional English-to-Chinese technical translator. "
            "Produce accurate, natural Chinese for the current paragraph and keep alignment coverage complete."
        )
    elif prompt_profile == "current":
        system_prompt = (
            "You are a high-fidelity book translation worker. "
            "Translate English book content into natural Chinese with paragraph-level coherence, "
            "preserve meaning, respect locked terms, and do not translate protected spans. "
            "You may reorganize sentence structure, but alignment coverage must remain complete."
        )
    elif prompt_profile == "role-style-v2":
        system_prompt = (
            "You are a senior technical translator and localizer for English-to-Chinese books, papers, and business documents. "
            "Produce accurate, professional, publication-grade Chinese that preserves structure and terminology consistency. "
            "Prefer natural Chinese technical prose over literal sentence mirroring, while keeping alignment coverage complete."
        )
    elif prompt_profile == "role-style-brief-v3":
        system_prompt = (
            "You are a publication-grade English-to-Chinese translator and localizer for technical books, papers, and business writing. "
            "Translate each packet as connected Chinese prose that reflects chapter intent, concept continuity, and professional publishing style. "
            "Prefer natural Chinese technical expression over literal mirroring, use chapter brief and concept memory actively, and keep alignment coverage complete."
        )
    else:
        system_prompt = (
            "You are a senior English-to-Chinese technical translator working inside a structured translation system. "
            "Translate with paragraph-first coherence, authoritative use of locked terms and chapter concept memory, and clean professional Chinese. "
            "Keep the translated body free of inline translator notes; report uncertainty only through structured notes or low-confidence flags. "
            "Alignment coverage must remain complete."
        )
    return TranslationPromptRequest(
        packet_id=packet.packet_id,
        model_name=model_name,
        prompt_version=prompt_version,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=TranslationWorkerOutput.model_json_schema(),
        sentence_alias_map=sentence_alias_map,
    )


class EchoTranslationWorker:
    """Deterministic placeholder worker for pipeline validation."""

    def __init__(self, model_name: str = "echo-worker", prompt_version: str = "p0.echo.v1"):
        self._metadata = TranslationWorkerMetadata(
            worker_name=self.__class__.__name__,
            model_name=model_name,
            prompt_version=prompt_version,
            runtime_config={"mode": "deterministic"},
        )

    def metadata(self) -> TranslationWorkerMetadata:
        return self._metadata

    def translate(self, task: TranslationTask) -> TranslationWorkerResult:
        target_segments: list[TranslationTargetSegment] = []
        alignments: list[AlignmentSuggestion] = []

        for sentence in task.current_sentences:
            temp_id = stable_id("temp-segment", task.context_packet.packet_id, sentence.id)
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=f"ZH::{sentence.normalized_text or sentence.source_text}",
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.75,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )

        return TranslationWorkerResult(
            output=TranslationWorkerOutput(
                packet_id=task.context_packet.packet_id,
                target_segments=target_segments,
                alignment_suggestions=alignments,
            ),
            usage=TranslationUsage(),
        )


class LLMTranslationWorker:
    """Structured LLM translation worker.

    This keeps prompt construction and provider I/O behind a stable contract so
    real providers can replace the placeholder worker without touching the
    persistence and QA pipeline.
    """

    def __init__(
        self,
        client: TranslationModelClient,
        *,
        model_name: str,
        prompt_version: str = "p0.llm.v1",
        prompt_profile: PromptProfile = "role-style-v2",
        runtime_config: dict[str, Any] | None = None,
    ):
        self.client = client
        self.prompt_profile = prompt_profile
        self._metadata = TranslationWorkerMetadata(
            worker_name=self.__class__.__name__,
            model_name=model_name,
            prompt_version=prompt_version,
            runtime_config={
                "prompt_profile": prompt_profile,
                **(runtime_config or {}),
            },
        )

    def metadata(self) -> TranslationWorkerMetadata:
        return self._metadata

    def translate(self, task: TranslationTask) -> TranslationWorkerResult | TranslationWorkerOutput:
        request = build_translation_prompt_request(
            task,
            model_name=self._metadata.model_name,
            prompt_version=self._metadata.prompt_version,
            prompt_profile=self.prompt_profile,
        )
        payload = self.client.generate_translation(request)
        return self._remap_sentence_aliases(payload, request)

    def _remap_sentence_aliases(
        self,
        payload: TranslationWorkerResult | TranslationWorkerOutput,
        request: TranslationPromptRequest,
    ) -> TranslationWorkerResult | TranslationWorkerOutput:
        alias_map = request.sentence_alias_map or {}
        if not alias_map:
            return payload

        valid_sentence_ids = set(alias_map.values())

        def _normalize_sentence_ids(values: list[str]) -> list[str]:
            normalized: list[str] = []
            for value in values:
                resolved = alias_map.get(value, value)
                if resolved in valid_sentence_ids and resolved not in normalized:
                    normalized.append(resolved)
            return normalized

        def _normalize_output(output: TranslationWorkerOutput) -> TranslationWorkerOutput:
            return output.model_copy(
                update={
                    "target_segments": [
                        segment.model_copy(
                            update={"source_sentence_ids": _normalize_sentence_ids(segment.source_sentence_ids)}
                        )
                        for segment in output.target_segments
                    ],
                    "alignment_suggestions": [
                        suggestion.model_copy(
                            update={"source_sentence_ids": _normalize_sentence_ids(suggestion.source_sentence_ids)}
                        )
                        for suggestion in output.alignment_suggestions
                    ],
                    "low_confidence_flags": [
                        flag.model_copy(
                            update={"sentence_id": alias_map.get(flag.sentence_id, flag.sentence_id)}
                        )
                        for flag in output.low_confidence_flags
                        if alias_map.get(flag.sentence_id, flag.sentence_id) in valid_sentence_ids
                    ],
                }
            )

        if isinstance(payload, TranslationWorkerResult):
            return payload.model_copy(update={"output": _normalize_output(payload.output)})
        return _normalize_output(payload)
