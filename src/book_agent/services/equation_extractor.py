"""Equation modality (PDF v2 M3.3).

Two layers:

  1. **Adapter Protocol** for swapping in a real ML-backed LaTeX
     recognizer (pix2tex / texify / Mathpix). Default `NoOpEquationLatexAdapter`
     returns None for everything, which means "we cannot recover LaTeX
     for this equation — fall back to the image-anchor protocol."

  2. **Pure post-processor** `enhance_block_for_equation(block, ...)`
     that always stamps `translatability=translate_none` on equation
     blocks (regardless of whether LaTeX was recovered) and stashes
     the LaTeX (when available) or a flag requesting image-anchor
     export (when not) into block metadata.

The two-layer split mirrors M2.3a/M2.3b: ship the protocol now,
swap the implementation later when the team is ready to onboard the
runtime dependency. `block.metadata["equation_render_mode"]` tells
the export layer how to lay out the block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Protocol

from book_agent.domain.structure.models import TRANSLATE_NONE, ParsedBlock


# --- Adapter ---


@dataclass(slots=True, frozen=True)
class EquationLatexResult:
    """Output of a successful LaTeX recovery."""

    latex: str
    is_display: bool = True
    confidence: float = 1.0


class EquationLatexAdapter(Protocol):
    """Contract for an LaTeX recognizer.

    `extract` accepts the equation's text representation (or, in
    future, an image bbox) and returns either a recovered LaTeX result
    or `None`. Returning `None` is the legitimate "not confident"
    signal — callers fall back to image-anchor display with the
    original text preserved verbatim.
    """

    def extract(self, equation_text: str) -> EquationLatexResult | None:
        ...


class NoOpEquationLatexAdapter:
    """Default adapter — never recovers LaTeX.

    Ships as the default so the equation modality protocol is wired
    end-to-end without forcing a vision-model dependency on every
    install. Replace with a real adapter (pix2tex / texify) when the
    team is ready.
    """

    def extract(self, equation_text: str) -> EquationLatexResult | None:
        return None


# --- Heuristic helpers ---


# Render modes the export layer reads from `metadata["equation_render_mode"]`.
EQUATION_RENDER_LATEX: Final[str] = "latex"
EQUATION_RENDER_IMAGE_ANCHOR: Final[str] = "image_anchor"
EQUATION_RENDER_VERBATIM_TEXT: Final[str] = "verbatim_text"

# Operators / glyphs that strongly suggest mathematical content. Used by
# `looks_like_equation` as a confidence boost when the block_type was
# only a guess.
_MATH_OPERATOR_RE: Final[re.Pattern[str]] = re.compile(
    r"[=≠≈≤≥<>±∓×÷·∑∏∫∮∂∇√∞∝∈∉⊂⊃∪∩∀∃∅⇒⇐⇔→←↔]"
)


def looks_like_equation(text: str) -> bool:
    """Quick heuristic — does this text plausibly contain an equation?

    Used by callers that want to *promote* a paragraph block to
    equation classification. Conservative: only fires on dense
    operator presence to avoid false-positives on prose that happens
    to mention "<" or "=".
    """
    if not text:
        return False
    operator_hits = len(_MATH_OPERATOR_RE.findall(text))
    if operator_hits == 0:
        return False
    word_count = len(re.findall(r"\b\w+\b", text))
    # Equations have high operator density. Pure prose pages with one
    # "=" in a sentence have density ≪ 0.05.
    density = operator_hits / max(word_count, 1)
    return density >= 0.15


# --- Block-level convenience ---


def enhance_block_for_equation(
    block: ParsedBlock,
    *,
    adapter: EquationLatexAdapter | None = None,
) -> ParsedBlock:
    """Always-correct, never-translate post-processor for equation blocks.

    Outcomes:
      * `translatability` always set to `translate_none`.
      * If the adapter recovers LaTeX, `metadata["equation_latex"]` is
        set and `metadata["equation_render_mode"]` becomes "latex".
      * If recovery fails AND the block has an attached image asset,
        `equation_render_mode` becomes "image_anchor" and exporters
        render the image with no Chinese gloss.
      * If recovery fails AND no image is attached, `equation_render_mode`
        becomes "verbatim_text" — display the original text as-is in a
        monospace block, untranslated.

    `block.block_type` is left untouched; downstream classification
    decisions (e.g., promoting paragraph → equation) belong to the
    caller, not to this enhancer.
    """
    from dataclasses import replace as _replace

    if block.block_type != "equation":
        return block

    new_metadata = dict(block.metadata)
    latex_result: EquationLatexResult | None = None
    extractor = adapter or NoOpEquationLatexAdapter()
    try:
        latex_result = extractor.extract(block.text)
    except Exception:  # pragma: no cover - defensive
        latex_result = None

    if latex_result is not None and latex_result.latex.strip():
        new_metadata["equation_latex"] = latex_result.latex
        new_metadata["equation_is_display"] = bool(latex_result.is_display)
        new_metadata["equation_confidence"] = float(latex_result.confidence)
        new_metadata["equation_render_mode"] = EQUATION_RENDER_LATEX
    else:
        # Image-anchor only when there's an image to anchor on. Otherwise
        # verbatim text is the safer default — render the original
        # equation characters in a monospace block.
        has_image = bool(
            new_metadata.get("image_path")
            or new_metadata.get("storage_path")
            or new_metadata.get("image_xref")
        )
        new_metadata["equation_render_mode"] = (
            EQUATION_RENDER_IMAGE_ANCHOR if has_image else EQUATION_RENDER_VERBATIM_TEXT
        )

    return _replace(
        block,
        translatability=TRANSLATE_NONE,
        metadata=new_metadata,
    )
