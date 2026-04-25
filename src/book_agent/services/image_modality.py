"""Image / Figure modality (PDF v2 M3.4).

The bootstrap layer already extracts image assets and pairs figure
blocks with caption blocks. What the existing code does NOT enforce
consistently is the **DocIR-level translatability contract** for the
image-side artifacts:

  * `image` / `figure` blocks must carry `translatability=translate_none`.
    Their "text" is alt-text, citation labels, or "[Image]" placeholders
    and must never be sinified.
  * Their paired captions stay `translate_all` — readers want the
    Chinese rendition of "Figure 1.1 Data flow…".
  * If a figure block carries `image_alt` metadata, surface it as the
    canonical short description (used for accessibility + alt attribute
    in HTML export). Empty alt → fall back to "[Image]" placeholder.

This module provides:
  - `enhance_block_for_image(block)`: contract-enforcing post-processor.
  - `enhance_caption_block(block, paired_figure)`: ensures captions
    keep `translate_all` even when defensive code elsewhere flipped
    them to TRANSLATE_NONE by mistake.
  - `enhance_document_image_modality(parsed_document)`: walks the
    document and applies the rules in one pass.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as _replace
from typing import Final

from book_agent.domain.structure.models import (
    TRANSLATE_ALL,
    TRANSLATE_NONE,
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
)


_IMAGE_BLOCK_TYPES: Final[frozenset[str]] = frozenset({"image", "figure"})
_CAPTION_BLOCK_TYPES: Final[frozenset[str]] = frozenset({"caption"})

_IMAGE_PLACEHOLDER_TEXT: Final[str] = "[Image]"


@dataclass(slots=True, frozen=True)
class ImageEnhancementSummary:
    """Aggregate stats for the document-level enhancement pass."""

    image_blocks_protected: int = 0
    captions_re_enabled: int = 0
    alt_text_filled: int = 0


def enhance_block_for_image(block: ParsedBlock) -> ParsedBlock:
    """Stamp `translatability=translate_none` on image/figure blocks
    and surface metadata `image_alt` as the canonical short text.
    """
    if block.block_type not in _IMAGE_BLOCK_TYPES:
        return block

    new_metadata = dict(block.metadata)
    new_text = block.text
    alt = new_metadata.get("image_alt")
    if isinstance(alt, str) and alt.strip():
        # Normalise canonical alt-text into metadata under a stable key
        # the export layer already reads.
        new_metadata.setdefault("image_canonical_alt", alt.strip())
        if not new_text or new_text.strip() in {"", _IMAGE_PLACEHOLDER_TEXT}:
            new_text = alt.strip()
    elif not new_text or not new_text.strip():
        new_text = _IMAGE_PLACEHOLDER_TEXT

    return _replace(
        block,
        text=new_text,
        translatability=TRANSLATE_NONE,
        metadata=new_metadata,
    )


def enhance_caption_block(block: ParsedBlock) -> ParsedBlock:
    """Restore `translate_all` on caption blocks.

    Captions are the *only* user-visible Chinese text that names a
    figure ("Figure 1.1 数据流…"); the translation pipeline must
    process them. A defensive caller earlier in the pipeline may have
    flipped them to TRANSLATE_NONE; this enforcer pulls them back.
    """
    if block.block_type not in _CAPTION_BLOCK_TYPES:
        return block
    if block.translatability == TRANSLATE_ALL:
        return block
    return _replace(block, translatability=TRANSLATE_ALL)


def enhance_document_image_modality(
    document: ParsedDocument,
) -> tuple[ParsedDocument, ImageEnhancementSummary]:
    """Apply image+caption contract across the entire document.

    Returns the rewritten document and a summary suitable for emission
    to telemetry / review UI.
    """
    summary = ImageEnhancementSummary()
    images_protected = 0
    captions_re_enabled = 0
    alt_filled = 0

    new_chapters: list[ParsedChapter] = []
    for chapter in document.chapters:
        new_blocks: list[ParsedBlock] = []
        for block in chapter.blocks:
            if block.block_type in _IMAGE_BLOCK_TYPES:
                enhanced = enhance_block_for_image(block)
                if enhanced is not block:
                    if block.translatability != TRANSLATE_NONE:
                        images_protected += 1
                    if (
                        "image_canonical_alt" in enhanced.metadata
                        and "image_canonical_alt" not in block.metadata
                    ):
                        alt_filled += 1
                new_blocks.append(enhanced)
                continue
            if block.block_type in _CAPTION_BLOCK_TYPES:
                enhanced = enhance_caption_block(block)
                if enhanced is not block:
                    captions_re_enabled += 1
                new_blocks.append(enhanced)
                continue
            new_blocks.append(block)
        new_chapters.append(_replace(chapter, blocks=new_blocks))

    rewritten = ParsedDocument(
        title=document.title,
        author=document.author,
        language=document.language,
        chapters=new_chapters,
        metadata=dict(document.metadata),
    )
    return (
        rewritten,
        ImageEnhancementSummary(
            image_blocks_protected=images_protected,
            captions_re_enabled=captions_re_enabled,
            alt_text_filled=alt_filled,
        ),
    )
