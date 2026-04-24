"""OCR re-extraction adapter (PDF v2 M2.3a).

When the text-layer sanity gate (M1.2 / M2.1) condemns a page, the extraction
router (M2.2) emits `ESCALATE_OCR` decisions for the affected blocks. This
module defines the *adapter interface* a real OCR engine must satisfy and
ships a default no-op implementation so the plumbing is green end-to-end
even before Surya integration (M2.3b).

Design notes:

  * The adapter is stateless and side-effect free from the pipeline's
    perspective; it does not mutate incoming blocks. It accepts a list of
    `OcrReextractionRequest` records and returns a flat dict of anchor
    → new text. Missing anchors mean "no replacement" — callers preserve
    the original text, which matters when OCR legitimately declines to
    rewrite (e.g., fully-figural region).

  * The protocol carries enough context for a real OCR engine to decide
    whether to OCR the exact bbox or widen to the full page: page number,
    bbox, current text length, and the sanity gate's reason code.

  * The caller (`PdfStructureRecoveryService`) is responsible for mapping
    per-block replacements back onto the `ParsedBlock` instances and for
    stamping the updated provenance and sanity_ok fields. The adapter has
    no knowledge of DocIR.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True, frozen=True)
class OcrReextractionRequest:
    """A single block's request for OCR re-extraction.

    Attributes:
        block_anchor: DocIR `ParsedBlock.anchor`; stable identifier the
            adapter echoes back in its reply dict.
        page_number: 1-indexed PDF page the block belongs to.
        bbox: (x0, y0, x1, y1) in PDF user-space units. Adapter may OCR
            just this rectangle or widen to the full page as it sees fit.
        current_text: the block text produced by the (mistrusted) text
            layer — useful for adapters that want to sanity-check against
            OCR output or diff to decide whether replacement is worth it.
        failure_reason: the `SanityReport.reason` code (e.g., "pua_high",
            "entropy_low"). Lets adapters choose a model suited to the
            failure class (a PUA failure often benefits from a vision
            model trained on glyph rasterization, whereas an entropy
            failure may want a language-model re-read).
    """

    block_anchor: str
    page_number: int
    bbox: tuple[float, float, float, float]
    current_text: str
    failure_reason: str


class OcrReextractionAdapter(Protocol):
    """Contract for swapping in a real OCR engine.

    Implementations MUST be pure from the caller's perspective: they may
    fork subprocesses and read the PDF, but they must not mutate any DocIR
    object. Returning an empty dict is the legitimate "no replacement"
    signal and MUST keep callers' fallbacks safe.
    """

    def reextract_blocks(
        self,
        pdf_path: str,
        requests: list[OcrReextractionRequest],
    ) -> dict[str, str]:
        """Map block_anchor → replacement_text for the subset of requests
        the adapter is confident about. Missing keys mean "keep original"."""
        ...


class NoOpOcrReextractionAdapter:
    """Default adapter — returns no replacements.

    Ships as the default so M2.3a lands without triggering Surya on every
    production run. Tests and M2.3b can substitute a real adapter.
    """

    def reextract_blocks(
        self,
        pdf_path: str,
        requests: list[OcrReextractionRequest],
    ) -> dict[str, str]:
        return {}
