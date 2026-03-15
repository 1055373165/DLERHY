from __future__ import annotations

from typing import Any

from book_agent.domain.enums import BlockType, ProtectedPolicy, SentenceStatus
from book_agent.domain.models import Block

_NONTRANSLATABLE_PDF_ROLES = {"header", "footer", "toc_entry"}
_NONTRANSLATABLE_PDF_PAGE_FAMILIES = {"backmatter"}


def _forced_translatability(
    source_span_json: dict[str, Any] | None,
) -> tuple[bool | None, str | None]:
    source_span_json = source_span_json or {}
    if "translatable" in source_span_json:
        return bool(source_span_json["translatable"]), source_span_json.get("nontranslatable_reason")

    pdf_block_role = source_span_json.get("pdf_block_role")
    if isinstance(pdf_block_role, str) and pdf_block_role in _NONTRANSLATABLE_PDF_ROLES:
        return False, f"pdf_{pdf_block_role}"
    pdf_page_family = source_span_json.get("pdf_page_family")
    if isinstance(pdf_page_family, str) and pdf_page_family in _NONTRANSLATABLE_PDF_PAGE_FAMILIES:
        return False, f"pdf_{pdf_page_family}"
    return None, None


def protected_policy_for_block(
    block_type: str | BlockType,
    source_span_json: dict[str, Any] | None = None,
) -> ProtectedPolicy:
    normalized_type = block_type.value if isinstance(block_type, BlockType) else block_type
    forced_translatable, _reason = _forced_translatability(source_span_json)
    if forced_translatable is False or normalized_type in {BlockType.CODE.value, BlockType.TABLE.value}:
        return ProtectedPolicy.PROTECT
    return ProtectedPolicy.TRANSLATE


def translatability_for_block(
    block_type: BlockType,
    source_span_json: dict[str, Any] | None = None,
) -> tuple[bool, str | None, SentenceStatus]:
    forced_translatable, reason = _forced_translatability(source_span_json)
    if forced_translatable is False:
        return False, reason or "nontranslatable_block", SentenceStatus.BLOCKED
    if block_type in {BlockType.CODE, BlockType.TABLE}:
        return False, f"{block_type.value}_protected", SentenceStatus.PROTECTED
    return True, None, SentenceStatus.PENDING


def block_is_context_translatable(block: Block) -> bool:
    translatable, _reason, _status = translatability_for_block(block.block_type, block.source_span_json)
    return translatable
