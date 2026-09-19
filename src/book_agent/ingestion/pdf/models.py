"""Data types for PDF extraction, profiling and structure recovery."""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from book_agent.domain.enums import BlockType


@dataclass(slots=True, frozen=True)
class _PageLayoutAssessment:
    risk: str = "low"
    reasons: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class _PageAssistPlan:
    bucket: str
    status: str
    scope: str
    trigger_reasons: tuple[str, ...]
    page_number: int
    parse_confidence: float | None
    page_layout_risk: str
    page_layout_reasons: tuple[str, ...]
    anchor_source_anchors: tuple[str, ...] = ()
    region_candidates: tuple[dict[str, Any], ...] = ()


@dataclass(slots=True, frozen=True)
class _PageExtractionPlan:
    intent: str
    reasons: tuple[str, ...]
    scope: str = "page"


@dataclass(slots=True, frozen=True)
class PdfFileProfile:
    pdf_kind: str
    page_count: int
    has_extractable_text: bool
    outline_present: bool
    layout_risk: str
    ocr_required: bool
    extractor_kind: str | None = None
    average_text_density: float = 0.0
    average_span_count: float = 0.0
    multi_column_page_count: int = 0
    fragment_page_count: int = 0
    suspicious_page_numbers: list[int] = field(default_factory=list)
    recovery_lane: str | None = None
    trailing_reference_page_count: int = 0
    academic_paper_candidate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_kind": self.pdf_kind,
            "page_count": self.page_count,
            "has_extractable_text": self.has_extractable_text,
            "outline_present": self.outline_present,
            "layout_risk": self.layout_risk,
            "ocr_required": self.ocr_required,
            "extractor_kind": self.extractor_kind,
            "average_text_density": round(self.average_text_density, 3),
            "average_span_count": round(self.average_span_count, 3),
            "multi_column_page_count": self.multi_column_page_count,
            "fragment_page_count": self.fragment_page_count,
            "suspicious_page_numbers": self.suspicious_page_numbers,
            "recovery_lane": self.recovery_lane,
            "trailing_reference_page_count": self.trailing_reference_page_count,
            "academic_paper_candidate": self.academic_paper_candidate,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PdfFileProfile":
        return cls(
            pdf_kind=str(payload.get("pdf_kind", "text_pdf")),
            page_count=int(payload.get("page_count", 0)),
            has_extractable_text=bool(payload.get("has_extractable_text", False)),
            outline_present=bool(payload.get("outline_present", False)),
            layout_risk=str(payload.get("layout_risk", "low")),
            ocr_required=bool(payload.get("ocr_required", False)),
            extractor_kind=str(payload["extractor_kind"]) if payload.get("extractor_kind") else None,
            average_text_density=float(payload.get("average_text_density", 0.0) or 0.0),
            average_span_count=float(payload.get("average_span_count", 0.0) or 0.0),
            multi_column_page_count=int(payload.get("multi_column_page_count", 0) or 0),
            fragment_page_count=int(payload.get("fragment_page_count", 0) or 0),
            suspicious_page_numbers=[int(page) for page in payload.get("suspicious_page_numbers", [])],
            recovery_lane=str(payload["recovery_lane"]) if payload.get("recovery_lane") else None,
            trailing_reference_page_count=int(payload.get("trailing_reference_page_count", 0) or 0),
            academic_paper_candidate=bool(payload.get("academic_paper_candidate", False)),
        )


@dataclass(slots=True, frozen=True)
class PdfOutlineEntry:
    level: int
    title: str
    page_number: int


@dataclass(slots=True, frozen=True)
class PdfTextBlock:
    page_number: int
    block_number: int
    text: str
    bbox: tuple[float, float, float, float]
    line_texts: list[str]
    span_count: int
    line_count: int
    font_size_min: float
    font_size_max: float
    font_size_avg: float
    font_names: frozenset[str] = frozenset()
    raw_text: str | None = None
    # Per entry of ``line_texts``: (dominant font size, every span bold).
    # Empty when the extractor has no span-level font information.
    line_styles: tuple[tuple[float, bool], ...] = ()
    # Text is "| cell | cell |" rows recovered from a ruled (vector-lined) table grid.
    ruled_table: bool = False


@dataclass(slots=True, frozen=True)
class PdfImageBlock:
    page_number: int
    block_number: int
    bbox: tuple[float, float, float, float]
    width_px: int | None = None
    height_px: int | None = None
    image_ext: str | None = None
    image_type: str = "embedded_image"
    materialized_path: str | None = None
    xref: int | None = None


@dataclass(slots=True, frozen=True)
class PdfPage:
    page_number: int
    width: float
    height: float
    blocks: list[PdfTextBlock]
    image_blocks: list[PdfImageBlock] = field(default_factory=list)
    # Text-layer sanity verdict (PDF v2 M1.2). Default empty = not assessed /
    # assume trustworthy. Populated by PyMuPDFTextExtractor per page and read
    # by downstream routing to decide whether to fall back to OCR.
    text_layer_sanity: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class PdfExtraction:
    title: str | None
    author: str | None
    metadata: dict[str, Any]
    pages: list[PdfPage]
    outline_entries: list[PdfOutlineEntry]

    def sanity_failed_pages(self) -> list[int]:
        """Page numbers (1-indexed) whose text-layer sanity gate rejected them.

        Downstream routers (M2) consult this to redirect affected pages to
        OCR/VLM instead of trusting the PyMuPDF text extraction.
        """
        return [
            page.page_number
            for page in self.pages
            if page.text_layer_sanity.get("ok") is False
        ]


@dataclass(slots=True)
class _RecoveredBlock:
    role: str
    block_type: BlockType
    text: str
    page_start: int
    page_end: int
    bbox_regions: list[dict[str, Any]]
    reading_order_index: int
    parse_confidence: float
    flags: list[str]
    font_size_avg: float
    source_path: str
    anchor: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class _TocEntryCandidate:
    title: str
    page_number: int | None


@dataclass(slots=True, frozen=True)
class _PageRecoveryContext:
    is_toc_page: bool
    page_family: str = "body"
    family_source: str = "body"
    family_heading: str | None = None
    content_family: str | None = None
    backmatter_cue: str | None = None
    backmatter_cue_source: str | None = None
    has_strong_heading: bool = False
    toc_entries_by_text: dict[str, _TocEntryCandidate] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class _ChapterStartCandidate:
    page_number: int
    title: str
    source: str
    section_family: str | None = None
