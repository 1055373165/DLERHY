"""Block-level Extraction Router (PDF v2 M2.2).

Per spec §3.2.3 this is the pure decision layer — given a `ParsedBlock`
whose provenance + confidence breakdown were stamped by the parser
frontend (M1.2 / M2.1), decide whether to TRUST the current text,
ESCALATE to OCR / VLM, or SKIP the block entirely (e.g., pure figures
with no textual content to translate).

This module is deliberately side-effect free:

  - no network calls
  - no LLM invocations
  - no disk IO

The actual re-extraction machinery (invoking Surya for ESCALATE_OCR,
calling a VLM for ESCALATE_VLM) lands in M2.3+. Keeping the router
pure lets us:

  1. Exhaustively unit-test the decision table without fixtures.
  2. Drive routing from offline data (replay telemetry, cost modelling).
  3. Swap the underlying extractor later without touching the policy.

The decision table intentionally prefers *conservative* choices:

  - Unknown sanity status → KEEP (default-trust, matches M1.2 posture).
  - VLM only fires when the caller explicitly opts in via
    `RouterContext.vlm_enabled`; the default path stays on OCR because
    OCR is already integrated and deterministic in cost.
  - Non-translatable blocks (translatability=TRANSLATE_NONE) are routed
    separately (NOT_APPLICABLE) so callers don't waste cycles
    re-extracting content the translator will skip anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from book_agent.domain.structure.models import (
    PROVENANCE_OCR,
    PROVENANCE_TEXT_LAYER,
    PROVENANCE_VLM,
    TRANSLATE_NONE,
    ParsedBlock,
)


class RouterDecision(str, Enum):
    """What the router recommends for a single block.

    - **KEEP**: trust the current text; send downstream as-is.
    - **ESCALATE_OCR**: re-extract this block's region via OCR (Surya).
    - **ESCALATE_VLM**: re-extract via a vision LLM; only valid when
      `RouterContext.vlm_enabled` is True.
    - **SKIP**: drop this block from translatable content (e.g., pure
      image/figure blocks whose semantics are captured elsewhere).
    - **NOT_APPLICABLE**: no routing needed because the block is already
      non-translatable (translate_none) — callers should bypass re-extraction.
    """

    KEEP = "keep"
    ESCALATE_OCR = "escalate_ocr"
    ESCALATE_VLM = "escalate_vlm"
    SKIP = "skip"
    NOT_APPLICABLE = "not_applicable"


@dataclass(slots=True, frozen=True)
class RouterContext:
    """Knobs controlling escalation policy.

    vlm_enabled        — master switch for VLM escalation. Default False
                         because VLM is not yet wired end-to-end (M2.3).
    vlm_block_ratio_cap — max fraction of blocks in a document that may
                         be routed to VLM; the router itself does not
                         enforce this (stateless), but callers consult
                         it when building escalation batches.
    """

    vlm_enabled: bool = False
    vlm_block_ratio_cap: float = 0.15


# Block classes where the router has nothing useful to do because the
# block is already definitionally non-text (figure/image without
# extractable prose). These SKIP by default and the downstream renderer
# handles them via image anchors.
_NON_TEXTUAL_BLOCK_TYPES: Final[frozenset[str]] = frozenset({"image", "figure"})


@dataclass(slots=True, frozen=True)
class RouterDecisionRecord:
    """Structured outcome — decision + reason code — for audit / telemetry.

    Reason codes are stable strings that downstream observability can
    group on without parsing free-form messages.
    """

    decision: RouterDecision
    reason: str


def route(block: ParsedBlock, ctx: RouterContext | None = None) -> RouterDecisionRecord:
    """Decide what to do with a single block.

    Pure function of `block` and `ctx`. No side effects.
    """
    context = ctx or RouterContext()

    # 1. Non-translatable content — router has no job to do here. The
    #    translator will bypass this block regardless of extraction path.
    if block.translatability == TRANSLATE_NONE:
        return RouterDecisionRecord(
            decision=RouterDecision.NOT_APPLICABLE,
            reason="block_non_translatable",
        )

    # 2. Pure non-textual blocks (figure/image) — skip translation.
    if block.block_type in _NON_TEXTUAL_BLOCK_TYPES:
        return RouterDecisionRecord(
            decision=RouterDecision.SKIP,
            reason=f"non_textual_block:{block.block_type}",
        )

    # 3. Already extracted via a trusted high-fidelity path — keep.
    if block.provenance == PROVENANCE_VLM:
        return RouterDecisionRecord(
            decision=RouterDecision.KEEP,
            reason="provenance_vlm",
        )

    # 4. Sanity gate failed on the source page — the text layer can't be
    #    trusted. Escalate to VLM when the caller opted in, else OCR.
    sanity_ok = block.confidence_breakdown.get("sanity_ok")
    if sanity_ok is False:
        if context.vlm_enabled:
            return RouterDecisionRecord(
                decision=RouterDecision.ESCALATE_VLM,
                reason=(
                    f"sanity_failed:{block.confidence_breakdown.get('sanity_reason', 'unknown')}"
                ),
            )
        return RouterDecisionRecord(
            decision=RouterDecision.ESCALATE_OCR,
            reason=(
                f"sanity_failed:{block.confidence_breakdown.get('sanity_reason', 'unknown')}"
            ),
        )

    # 5. Provenance already OCR (advisory or materialized) but sanity did
    #    not fail (sanity_ok is True or unknown): trust and KEEP. The
    #    advisory-OCR case where sanity_ok is False was handled above.
    if block.provenance == PROVENANCE_OCR:
        return RouterDecisionRecord(
            decision=RouterDecision.KEEP,
            reason="provenance_ocr_sanity_ok",
        )

    # 6. Default happy path: text layer + sanity ok (or unknown) → trust.
    return RouterDecisionRecord(
        decision=RouterDecision.KEEP,
        reason="text_layer_trusted",
    )


def summarize(decisions: list[RouterDecisionRecord]) -> dict[str, Any]:
    """Aggregate routing outcomes for telemetry / budget enforcement.

    Returns counts per decision and per reason, plus the VLM/OCR ratios
    callers check against `RouterContext.vlm_block_ratio_cap`.
    """
    total = len(decisions)
    by_decision: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for rec in decisions:
        by_decision[rec.decision.value] = by_decision.get(rec.decision.value, 0) + 1
        by_reason[rec.reason] = by_reason.get(rec.reason, 0) + 1
    vlm_count = by_decision.get(RouterDecision.ESCALATE_VLM.value, 0)
    ocr_count = by_decision.get(RouterDecision.ESCALATE_OCR.value, 0)
    return {
        "total": total,
        "by_decision": by_decision,
        "by_reason": by_reason,
        "vlm_ratio": (vlm_count / total) if total else 0.0,
        "ocr_ratio": (ocr_count / total) if total else 0.0,
    }
