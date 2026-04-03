# ruff: noqa: E402

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.app.main import create_app
from book_agent.domain.enums import (
    ActionType,
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    DocumentStatus,
    ExportType,
    JobScopeType,
    JobStatus,
    JobType,
    ProtectedPolicy,
    RootCauseLayer,
    Severity,
    SourceType,
)
from book_agent.domain.models import Block, Chapter, Document, JobRun
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.domain.structure.pdf import (
    BasicPdfTextExtractor,
    PDFParser,
    PdfExtraction,
    PdfFileProfiler,
    PdfFileProfile,
    PdfImageBlock,
    PdfOutlineEntry,
    PdfPage,
    PdfStructureRecoveryService,
    PdfTextBlock,
    PyMuPDFTextExtractor,
    _RecoveredBlock,
    _detect_backmatter_cue,
    _embedded_academic_abstract_segments,
    _book_heading_level,
    _leading_all_caps_book_heading_and_remainder,
    _leading_numbered_book_heading_and_remainder,
    _leading_reference_heading_and_remainder,
    _infer_appendix_intro_title,
    _infer_appendix_nested_subheading_title,
    _infer_appendix_subheading_title,
    _infer_intro_page_title,
    _next_academic_inline_heading,
    _looks_like_code,
    _looks_like_equation,
    _looks_like_figure_caption,
    _looks_like_code_continuation_line,
    _looks_like_numeric_table_fragment,
    _looks_like_visual_heading,
    _looks_like_reference_entry,
    _looks_like_table,
    _normalize_multiline_text,
)
from book_agent.domain.structure.models import ParsedBlock, ParsedChapter, ParsedDocument
from book_agent.domain.structure.ocr import OcrPdfTextExtractor, UvSuryaOcrRunner
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.export import ExportRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.actions import IssueActionExecutor
from book_agent.services.bootstrap import BootstrapArtifacts, BootstrapPipeline, IngestService, ParseService
from book_agent.services.export import ExportGateError, ExportService, MergedRenderBlock
from book_agent.services.pdf_structure_refresh import PdfStructureRefreshService
from book_agent.services.realign import RealignService
from book_agent.services.rebuild import TargetedRebuildService
from book_agent.services.rerun import RerunService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService


PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_command(x: int, y: int, font_size: int, text: str) -> str:
    escaped = _pdf_escape(text)
    return f"BT /F1 {font_size} Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET"


def _positioned_text_segment(entries: list[tuple[int, int, int, str]]) -> str:
    commands = ["BT"]
    current_font_size: int | None = None
    for x, y, font_size, text in entries:
        if current_font_size != font_size:
            commands.append(f"/F1 {font_size} Tf")
            current_font_size = font_size
        commands.append(f"1 0 0 1 {x} {y} Tm ({_pdf_escape(text)}) Tj")
    commands.append("ET")
    return " ".join(commands)


def _write_pdf(
    path: Path,
    pages: list[list[str]],
    *,
    title: str,
    author: str,
    outline_entries: list[tuple[int, str, int]] | None = None,
) -> None:
    objects: list[str | None] = [None]

    def add_object(content: str) -> int:
        objects.append(content)
        return len(objects) - 1

    font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = add_object("")
    info_id = add_object(f"<< /Title ({_pdf_escape(title)}) /Author ({_pdf_escape(author)}) >>")

    page_ids: list[int] = []
    for commands in pages:
        content_stream = "\n".join(commands)
        content_bytes = content_stream.encode("latin1")
        content_id = add_object(
            f"<< /Length {len(content_bytes)} >>\nstream\n{content_stream}\nendstream"
        )
        page_id = add_object(
            "<< /Type /Page "
            f"/Parent {pages_id} 0 R "
            f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)

    objects[pages_id] = (
        "<< /Type /Pages "
        f"/Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_ids)} >>"
    )
    outlines_id: int | None = None
    if outline_entries:
        outlines_id = add_object("")
        outline_item_ids = [add_object("") for _entry in outline_entries]
        for index, outline_item_id in enumerate(outline_item_ids):
            _level, outline_title, page_number = outline_entries[index]
            prev_ref = f"/Prev {outline_item_ids[index - 1]} 0 R " if index > 0 else ""
            next_ref = f"/Next {outline_item_ids[index + 1]} 0 R " if index + 1 < len(outline_item_ids) else ""
            target_page_id = page_ids[page_number - 1]
            objects[outline_item_id] = (
                "<< "
                f"/Title ({_pdf_escape(outline_title)}) "
                f"/Parent {outlines_id} 0 R "
                f"{prev_ref}{next_ref}"
                f"/Dest [{target_page_id} 0 R /Fit] "
                ">>"
            )
        objects[outlines_id] = (
            "<< /Type /Outlines "
            f"/First {outline_item_ids[0]} 0 R "
            f"/Last {outline_item_ids[-1]} 0 R "
            f"/Count {len(outline_item_ids)} >>"
        )
    catalog = f"<< /Type /Catalog /Pages {pages_id} 0 R"
    if outlines_id is not None:
        catalog += f" /Outlines {outlines_id} 0 R /PageMode /UseOutlines"
    catalog += " >>"
    catalog_id = add_object(catalog)

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * len(objects)
    for object_id in range(1, len(objects)):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n{objects[object_id]}\nendobj\n".encode("latin1"))

    startxref = len(output)
    output.extend(f"xref\n0 {len(objects)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for object_id in range(1, len(objects)):
        output.extend(f"{offsets[object_id]:010d} 00000 n \n".encode("ascii"))

    output.extend(
        (
            f"trailer\n<< /Size {len(objects)} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\n"
            f"startxref\n{startxref}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(output))


def _write_low_risk_text_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 794, 10, "Signal vs Noise"),
            _text_command(72, 734, 22, "Chapter 1 Strategic Moats"),
            _text_command(72, 102, 12, "Pricing pow-"),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 794, 10, "Signal vs Noise"),
            _text_command(72, 714, 12, "er matters because durable advantages compound across cycles."),
            _text_command(72, 658, 12, "Teams that defend margins keep investing in product quality."),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Signal vs Noise", author="Test Author")


def _write_high_risk_two_column_pdf(path: Path) -> None:
    sample_text = "Column text long enough to trigger multi column layout suspicion."
    pages = [
        [
            _text_command(72, 794, 10, "Two Column Sample"),
            _text_command(72, 722, 11, sample_text),
            _text_command(330, 702, 11, sample_text),
            _text_command(72, 582, 11, sample_text),
            _text_command(330, 562, 11, sample_text),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 794, 10, "Two Column Sample"),
            _text_command(72, 722, 11, sample_text),
            _text_command(330, 702, 11, sample_text),
            _text_command(72, 582, 11, sample_text),
            _text_command(330, 562, 11, sample_text),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Two Column Sample", author="Test Author")


def _write_medium_risk_two_column_pdf(path: Path) -> None:
    sample_text = "Column text long enough to trigger multi column layout suspicion."
    pages = [
        [
            _text_command(72, 794, 10, "Medium Risk Sample"),
            _text_command(72, 722, 11, sample_text),
            _text_command(330, 702, 11, sample_text),
            _text_command(72, 582, 11, sample_text),
            _text_command(330, 562, 11, sample_text),
            _text_command(298, 36, 10, "1"),
        ],
    ]
    _write_pdf(path, pages, title="Medium Risk Sample", author="Test Author")


def _write_academic_paper_pdf(path: Path) -> None:
    dense_column_text = "attention " * 520
    reference_one = (
        "[1] Ashish Vaswani, Noam Shazeer, and Niki Parmar. 2017. Attention Is All You Need. "
        "Advances in Neural Information Processing Systems."
    )
    reference_two = (
        "[2] Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua Bengio. 2015. Neural machine translation "
        "by jointly learning to align and translate. ICLR."
    )
    pages = [
        [
            _text_command(178, 760, 20, "Transformer Attention in Practice"),
            _text_command(214, 730, 12, "A. Researcher and B. Scientist"),
            _text_command(
                72,
                684,
                12,
                "Abstract This paper studies how attention-based sequence models behave in compact academic PDFs.",
            ),
            _text_command(
                72,
                654,
                12,
                "We focus on translation-oriented recovery, section preservation, and references as source-side artifacts.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(304, 736, 10, dense_column_text),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(304, 736, 10, dense_column_text),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(244, 748, 16, "References"),
            _text_command(72, 700, 10, f"{reference_one} {reference_two}"),
            _text_command(298, 36, 10, "4"),
        ],
        [
            _text_command(72, 720, 10, f"{reference_two} {reference_one}"),
            _text_command(298, 36, 10, "5"),
        ],
    ]
    _write_pdf(path, pages, title="Transformer Attention in Practice", author="Test Author")


def _write_academic_paper_with_inline_sections_pdf(path: Path) -> None:
    section_one = (
        "1 Introduction We study attention-based sequence models in compact academic PDFs. "
        "The parser must preserve section boundaries even when extractors merge blocks. "
        "2 Model Architecture Our model uses multi-head attention and residual pathways. "
        "The encoder and decoder stacks share a stable hidden dimensionality."
    )
    section_two = (
        "3 Training We optimize the model with Adam and scheduled learning rates. "
        "The training loop tracks convergence across translation benchmarks. "
        "4 Results We compare the recovered structure against a source-side baseline and report qualitative gains."
    )
    filler = " attention" * 90
    reference_one = (
        "[1] A. Researcher and B. Scientist. 2024. Structured recovery for compact academic PDFs. ACL."
    )
    reference_two = (
        "[2] C. Author and D. Writer. 2023. Attention models for translation pipelines. EMNLP."
    )
    pages = [
        [
            _text_command(178, 760, 20, "Transformer Attention in Practice"),
            _text_command(214, 730, 12, "A. Researcher and B. Scientist"),
            _text_command(
                72,
                684,
                12,
                "Abstract This paper studies how section-aware recovery improves translation-oriented parsing for academic PDFs.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(304, 736, 10, f"{section_one}{filler}."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(304, 736, 10, f"{section_two}{filler}."),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(244, 748, 16, "References"),
            _text_command(72, 700, 10, f"{reference_one} {reference_two}"),
            _text_command(298, 36, 10, "4"),
        ],
        [
            _text_command(72, 720, 10, f"{reference_two} {reference_one}"),
            _text_command(298, 36, 10, "5"),
        ],
    ]
    _write_pdf(path, pages, title="Transformer Attention in Practice", author="Test Author")


def _write_academic_paper_with_noisy_inline_sections_pdf(path: Path) -> None:
    section_one = (
        "3 Model Ar chitectur e Most competitive neural sequence transduction models rely on encoder-decoder stacks. "
        "3.1 Encoder and Decoder Stacks Encoder: The encoder is composed of stacked self-attention and feed-forward layers. "
        "3.4 Embeddings and Softmax Similarly to other sequence transduction models, we use learned embeddings and a shared softmax projection."
    )
    section_two = (
        "6 Results We report translation quality and training-cost tradeoffs for attention-only models. "
        "7 Conclusion We conclude that attention-only models remain translation-friendly and structurally recoverable."
    )
    table_like_block = "Model BLEU Training Cost Transformer 27.5 1.0x10^19"
    filler = " attention" * 90
    reference_one = (
        "[1] A. Researcher and B. Scientist. 2024. Structured recovery for compact academic PDFs. ACL."
    )
    reference_two = (
        "[2] C. Author and D. Writer. 2023. Attention models for translation pipelines. EMNLP."
    )
    pages = [
        [
            _text_command(178, 760, 20, "Transformer Attention in Practice"),
            _text_command(214, 730, 12, "A. Researcher and B. Scientist"),
            _text_command(
                72,
                684,
                12,
                "Abstract This paper studies how noisy inline academic headings should be cleaned before export.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(304, 736, 10, f"{section_one}{filler}."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(304, 736, 10, f"{section_two}{filler}."),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(304, 736, 10, table_like_block),
            _text_command(298, 36, 10, "4"),
        ],
        [
            _text_command(244, 748, 16, "References"),
            _text_command(72, 700, 10, f"{reference_one} {reference_two}"),
            _text_command(298, 36, 10, "5"),
        ],
        [
            _text_command(72, 720, 10, f"{reference_two} {reference_one}"),
            _text_command(298, 36, 10, "6"),
        ],
    ]
    _write_pdf(path, pages, title="Transformer Attention in Practice", author="Test Author")


def _write_academic_paper_with_broken_heading_tail_pdf(path: Path) -> None:
    section_one = (
        "3.2.1 Scaled Dot-Pr oduct Attention We call our particular attention scaled dot-product attention "
        "and use it throughout the model. "
        "3.2.2 Multi-Head Attention Multiple heads let the model attend to different subspaces in parallel."
    )
    filler = " attention" * 90
    reference_one = (
        "[1] A. Researcher and B. Scientist. 2024. Structured recovery for compact academic PDFs. ACL."
    )
    reference_two = (
        "[2] C. Author and D. Writer. 2023. Attention models for translation pipelines. EMNLP."
    )
    pages = [
        [
            _text_command(178, 760, 20, "Transformer Attention in Practice"),
            _text_command(214, 730, 12, "A. Researcher and B. Scientist"),
            _text_command(
                72,
                684,
                12,
                "Abstract This paper studies how broken numbered heading tails should be repaired near figure-heavy layouts.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(304, 736, 10, f"{section_one}{filler}."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(304, 736, 10, filler),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(244, 748, 16, "References"),
            _text_command(72, 700, 10, f"{reference_one} {reference_two}"),
            _text_command(298, 36, 10, "4"),
        ],
        [
            _text_command(72, 720, 10, f"{reference_two} {reference_one}"),
            _text_command(298, 36, 10, "5"),
        ],
    ]
    _write_pdf(path, pages, title="Transformer Attention in Practice", author="Test Author")


def _write_positioned_multi_column_academic_paper_pdf(path: Path) -> None:
    left_top = "1 Introduction Left column should stay ahead of the right column in the recovered reading order."
    left_mid = "Left column continues with more detail about attention recovery and translator friendly structure."
    left_low = "Left column ends with a final sentence before the reader should move to the right side."
    right_top = "2 Related Work Right column should appear only after the left column has been fully consumed."
    right_mid = "Right column then discusses related work and parsing baselines for academic papers."
    right_low = "Right column closes the page with additional evidence about dual column extraction."
    reference_one = (
        "[1] A. Researcher and B. Scientist. 2024. Structured recovery for compact academic PDFs. ACL."
    )
    reference_two = (
        "[2] C. Author and D. Writer. 2023. Attention models for translation pipelines. EMNLP."
    )
    pages = [
        [
            _text_command(178, 760, 20, "Transformer Attention in Practice"),
            _text_command(214, 730, 12, "A. Researcher and B. Scientist"),
            _text_command(
                72,
                684,
                12,
                "Abstract This paper studies how positioned text extraction improves reading order for dual-column academic PDFs.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _positioned_text_segment(
                [
                    (72, 722, 11, left_top),
                    (330, 722, 11, right_top),
                    (72, 684, 11, left_mid),
                    (330, 684, 11, right_mid),
                    (72, 646, 11, left_low),
                    (330, 646, 11, right_low),
                ]
            ),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _positioned_text_segment(
                [
                    (72, 722, 11, left_top),
                    (330, 722, 11, right_top),
                    (72, 684, 11, left_mid),
                    (330, 684, 11, right_mid),
                    (72, 646, 11, left_low),
                    (330, 646, 11, right_low),
                ]
            ),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(244, 748, 16, "References"),
            _text_command(72, 700, 10, f"{reference_one} {reference_two}"),
            _text_command(298, 36, 10, "4"),
        ],
        [
            _text_command(72, 720, 10, f"{reference_two} {reference_one}"),
            _text_command(298, 36, 10, "5"),
        ],
    ]
    _write_pdf(path, pages, title="Transformer Attention in Practice", author="Test Author")


def _write_asymmetric_first_page_academic_paper_pdf(path: Path) -> None:
    outline_entries = [
        (1, "1 Introduction", 1),
        (1, "2 Related Work", 2),
        (1, "References", 3),
    ]
    pages = [
        [
            _text_command(
                120,
                760,
                20,
                "Forming Effective Human-AI Teams: Building Machine Learning Models that Complement the Capabilities of Multiple Experts",
            ),
            _text_command(170, 730, 12, "P. Hemmer, S. Schellhammer, M. Vossing, J. Jakubik, G. Satzger"),
            _text_command(126, 704, 11, "Karlsruhe Institute of Technology"),
            _text_command(146, 684, 11, "patrick.hemmer@kit.edu"),
            _text_command(154, 656, 12, "Abstract"),
            _text_command(14, 646, 10, "arXiv:2206.07948v1 [cs.AI]"),
            _text_command(
                72,
                632,
                11,
                "Machine learning (ML) models are increasingly used in application domains with human experts.",
            ),
            _text_command(
                72,
                602,
                11,
                "When predictions are difficult, teams may defer selected instances to people with complementary skills.",
            ),
            _text_command(
                330,
                632,
                11,
                "of side information that is not accessible to the ML model and motivates expert allocation policies.",
            ),
            _text_command(
                330,
                602,
                11,
                "This abstract continuation should remain with the title page rather than spilling into the introduction chapter.",
            ),
            _text_command(72, 418, 14, "1 Introduction"),
            _text_command(
                72,
                384,
                11,
                "Over the last years, the performance of machine learning models has become comparable to human experts.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 14, "2 Related Work"),
            _text_command(
                72,
                684,
                11,
                "Related work compares deferral methods, human-AI teamwork baselines, and allocation systems.",
            ),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 14, "References"),
            _text_command(72, 684, 11, "[1] A. Researcher. 2024. Structured recovery for academic PDFs. ACL."),
            _text_command(298, 36, 10, "3"),
        ],
    ]
    _write_pdf(
        path,
        pages,
        title="Forming Effective Human-AI Teams: Building Machine Learning Models that Complement the Capabilities of Multiple Experts",
        author="Test Author",
        outline_entries=outline_entries,
    )


def _write_asymmetric_first_page_academic_paper_with_uppercase_continuation_pdf(path: Path) -> None:
    outline_entries = [
        (1, "1 Introduction", 1),
        (1, "2 Related Work", 2),
        (1, "References", 3),
    ]
    pages = [
        [
            _text_command(
                120,
                760,
                20,
                "Forming Effective Human-AI Teams: Building Machine Learning Models that Complement the Capabilities of Multiple Experts",
            ),
            _text_command(170, 730, 12, "P. Hemmer, S. Schellhammer, M. Vossing, J. Jakubik, G. Satzger"),
            _text_command(126, 704, 11, "Karlsruhe Institute of Technology"),
            _text_command(146, 684, 11, "patrick.hemmer@kit.edu"),
            _text_command(154, 656, 12, "Abstract"),
            _text_command(
                72,
                632,
                11,
                "Machine learning (ML) models are increasingly used in application domains with human experts.",
            ),
            _text_command(
                72,
                602,
                11,
                "Teams may defer selected instances to people with complementary skills when predictions are difficult.",
            ),
            _text_command(
                330,
                632,
                11,
                "This abstract continuation begins with uppercase text but still belongs to the abstract discussion.",
            ),
            _text_command(
                330,
                602,
                11,
                "These concluding abstract sentences describe expert allocation before the introduction begins.",
            ),
            _text_command(72, 418, 14, "1 Introduction"),
            _text_command(
                72,
                384,
                11,
                "Over the last years, the performance of machine learning models has become comparable to human experts.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 14, "2 Related Work"),
            _text_command(
                72,
                684,
                11,
                "Related work compares deferral methods, human-AI teamwork baselines, and allocation systems.",
            ),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 14, "References"),
            _text_command(72, 684, 11, "[1] A. Researcher. 2024. Structured recovery for academic PDFs. ACL."),
            _text_command(298, 36, 10, "3"),
        ],
    ]
    _write_pdf(
        path,
        pages,
        title="Forming Effective Human-AI Teams: Building Machine Learning Models that Complement the Capabilities of Multiple Experts",
        author="Test Author",
        outline_entries=outline_entries,
    )


def _write_outlined_multi_column_academic_paper_pdf(path: Path) -> None:
    outline_entries = [
        (1, "1 Introduction", 2),
        (1, "2 Related Work", 2),
        (1, "3 Approach", 3),
        (1, "References", 4),
    ]
    reference_one = (
        "[1] A. Researcher and B. Scientist. 2024. Structured recovery for compact academic PDFs. ACL."
    )
    reference_two = (
        "[2] C. Author and D. Writer. 2023. Attention models for translation pipelines. EMNLP."
    )
    pages = [
        [
            _text_command(
                120,
                760,
                20,
                "Forming Effective Human-AI Teams: Building Machine Learning Models that Complement the Capabilities of Multiple Experts",
            ),
            _text_command(180, 730, 12, "P. Hemmer, S. Schellhammer, M. Vossing, J. Jakubik, G. Satzger"),
            _text_command(
                72,
                684,
                12,
                "Abstract This paper studies how to recover section-aware structure from dual-column academic PDFs before translation.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 14, "1 Introduction"),
            _text_command(
                72,
                684,
                11,
                "Introduction left column explains why human-AI teamwork papers need section-faithful recovery before translation.",
            ),
            _text_command(
                72,
                648,
                11,
                "The introduction continues in the left column before the reader should move to the second top-level section.",
            ),
            _text_command(330, 724, 14, "2 Related Work"),
            _text_command(
                330,
                684,
                11,
                "Related work in the right column compares prior deferral methods, teamwork baselines, and allocation systems.",
            ),
            _text_command(
                330,
                648,
                11,
                "The right column continues only after the left column introduction content has been consumed in reading order.",
            ),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 14, "3 Approach"),
            _text_command(
                72,
                684,
                11,
                "The approach section formalizes the classifier and allocation system as a coordinated human-AI team.",
            ),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(244, 748, 16, "References"),
            _text_command(72, 700, 10, f"{reference_one} {reference_two}"),
            _text_command(298, 36, 10, "4"),
        ],
    ]
    _write_pdf(
        path,
        pages,
        title="Forming Effective Human-AI Teams",
        author="Test Author",
        outline_entries=outline_entries,
    )


def _write_single_column_research_paper_pdf(path: Path) -> None:
    title_and_abstract = (
        "Forming Effective Human-AI Teams: Building Machine Lear ning Models that Complement the Capabilities "
        "of Multiple Experts Patrick Hemmer 1 , Sebastian Schellhammer 1 ; 2 , Michael Vossing 1 , "
        "Johannes Jakubik 1 and Gerhard Satzger 1 1 Karlsruhe Institute of Technology 2 GESIS - Leibniz "
        "Institute for the Social Sciences patrick.hemmer@kit.edu Abstract Machine learning models are "
        "increasingly used in application domains that involve working together with multiple experts."
    )
    body_text = (
        "We study how machine learning systems can complement experts with different capabilities and "
        "coordinate deferrals across a shared decision process."
    )
    references_text = (
        "Refer ences [ Bansal et al. , 2021 ] Gagan Bansal, Besmira Nushi, Ece Kamar, Eric Horvitz, "
        "and Daniel Weld. Is the most accurate ai the best teammate? optimizing ai for teamwork. In AAAI, 2021."
    )
    pages = [
        [
            _text_command(36, 742, 12, title_and_abstract),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(36, 742, 12, body_text),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(36, 742, 12, references_text),
            _text_command(298, 36, 10, "3"),
        ],
    ]
    _write_pdf(path, pages, title="", author="")


def _write_broken_title_heading_paper_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(180, 760, 20, "Attention Is All Y ou Need"),
            _text_command(214, 730, 12, "A. Researcher and B. Scientist"),
            _text_command(
                72,
                684,
                12,
                "Abstract This paper studies how broken title headings should be normalized before export.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(244, 748, 16, "References"),
            _text_command(
                72,
                700,
                10,
                "[1] A. Researcher and B. Scientist. 2024. Structured recovery for compact academic PDFs. ACL.",
            ),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Attention is All you Need", author="Test Author")


def _write_toc_driven_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 760, 22, "Contents"),
            _text_command(72, 708, 12, "Chapter 1 Strategic Moats ........ 2"),
            _text_command(72, 678, 12, "Chapter 2 Network Effects ........ 3"),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 794, 10, "Signal vs Noise"),
            _text_command(72, 724, 22, "Strategic Moats"),
            _text_command(72, 670, 12, "Durable differentiation compounds when reinvestment remains disciplined."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 794, 10, "Signal vs Noise"),
            _text_command(72, 724, 22, "Network Effects"),
            _text_command(72, 670, 12, "Usage density improves the product for every additional participant."),
            _text_command(298, 36, 10, "3"),
        ],
    ]
    _write_pdf(path, pages, title="Signal vs Noise", author="Test Author")


def _write_outline_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 760, 22, "Front Matter"),
            _text_command(72, 700, 12, "This page should not become a standalone chapter when outline data exists."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 22, "Strategic Moats"),
            _text_command(72, 670, 12, "Moats endure when reinvestment and customer trust reinforce each other."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 22, "Network Effects"),
            _text_command(72, 670, 12, "Dense ecosystems improve retention, pricing power, and distribution reach."),
            _text_command(298, 36, 10, "3"),
        ],
    ]
    _write_pdf(
        path,
        pages,
        title="Outline Sample",
        author="Test Author",
        outline_entries=[
            (1, "Strategic Moats", 2),
            (1, "Network Effects", 3),
        ],
    )


def _write_footnote_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 662, 12, "A moat compounds over time 1"),
            _text_command(72, 632, 12, "Distribution deepens with each cohort."),
            _text_command(72, 86, 9, "1 First footnote explains the claim."),
            _text_command(72, 68, 9, "2 Orphaned footnote without a visible anchor."),
            _text_command(298, 36, 10, "1"),
        ],
    ]
    _write_pdf(path, pages, title="Footnote Sample", author="Test Author")


def _write_cross_page_footnote_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 662, 12, "A moat compounds over time1."),
            _text_command(72, 86, 9, "1 This footnote starts at the bottom and"),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 764, 9, "continues on the next page without repeating the marker."),
            _text_command(72, 662, 12, "Fresh body text resumes after the footnote finishes."),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Cross Page Footnote Sample", author="Test Author")


def _write_next_page_footnote_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 662, 12, "A moat compounds over time1."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 764, 9, "1 This footnote starts on the next page after a layout break."),
            _text_command(72, 662, 12, "Regular body text starts lower on the page."),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Next Page Footnote Sample", author="Test Author")


def _write_same_page_multisegment_footnote_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 662, 12, "A moat compounds over time1."),
            _text_command(72, 72, 9, "1 First footnote paragraph ends with a full stop."),
            _text_command(72, 52, 9, "Second paragraph starts a new sentence with more context."),
            _text_command(298, 36, 10, "1"),
        ],
    ]
    _write_pdf(path, pages, title="Same Page Multi-Segment Footnote Sample", author="Test Author")


def _write_cross_page_multiparagraph_footnote_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 662, 12, "A moat compounds over time1."),
            _text_command(72, 86, 9, "1 This footnote starts at the bottom and introduces the first idea."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 776, 9, "Second paragraph starts on the next page with more detail."),
            _text_command(72, 754, 9, "Third paragraph continues the explanation before body text resumes."),
            _text_command(72, 662, 12, "Regular body text resumes lower on the page."),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Cross Page Multi-Paragraph Footnote Sample", author="Test Author")


def _write_section_family_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Preface"),
            _text_command(72, 670, 12, "This preface explains why the book begins with mental models."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "Great products keep compounding trust as they scale."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 22, "Appendix A Metrics"),
            _text_command(72, 670, 12, "Retention, payback period, and margin expansion matter together."),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 22, "References"),
            _text_command(72, 670, 12, "Hamilton, J. Compounding Companies. 2024."),
            _text_command(298, 36, 10, "4"),
        ],
        [
            _text_command(72, 724, 22, "Index"),
            _text_command(72, 670, 12, "advantage, durable products, retention loops, and switching costs 12"),
            _text_command(298, 36, 10, "5"),
        ],
    ]
    _write_pdf(path, pages, title="Section Family Sample", author="Test Author")


def _write_outline_with_appendix_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Strategic Moats"),
            _text_command(72, 670, 12, "Moats widen when distribution and product quality reinforce each other."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 22, "Appendix A Metrics"),
            _text_command(72, 670, 12, "Rule of forty, payback, and retention are reviewed here."),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(
        path,
        pages,
        title="Outline Appendix Sample",
        author="Test Author",
        outline_entries=[(1, "Strategic Moats", 1)],
    )


def _write_toc_offset_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 760, 22, "Contents"),
            _text_command(72, 708, 12, "Chapter 1 Strategic Moats ........ 1"),
            _text_command(72, 678, 12, "Chapter 2 Network Effects ........ 2"),
            _text_command(298, 36, 10, "vii"),
        ],
        [
            _text_command(72, 724, 22, "Preface"),
            _text_command(72, 670, 12, "This preface explains the mental model behind the rest of the book."),
            _text_command(298, 36, 10, "viii"),
        ],
        [
            _text_command(72, 724, 22, "Strategic Moats"),
            _text_command(72, 670, 12, "Durable differentiation compounds when reinvestment remains disciplined."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 22, "Network Effects"),
            _text_command(72, 670, 12, "Usage density improves the product for every additional participant."),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Offset TOC Sample", author="Test Author")


def _write_headingless_references_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "Durable products create room for repeated reinvestment."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 11, "Hamilton, J. Durable Advantage. 2024."),
            _text_command(72, 696, 11, "Lee, S. Network Density and Scale. 2023."),
            _text_command(72, 668, 11, "https://doi.org/10.1000/example-reference"),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Headingless References Sample", author="Test Author")


def _write_headingless_index_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "This chapter defines the product strategy and market loops."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 11, "advantage, durable 12, 16"),
            _text_command(72, 696, 11, "network effects 18, 21"),
            _text_command(72, 668, 11, "switching costs 24"),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Headingless Index Sample", author="Test Author")


def _write_appendix_continuation_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "Product strategy sets the frame for the rest of the book."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 22, "Appendix A Metrics"),
            _text_command(72, 670, 12, "The appendix introduces the measurement glossary."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 12, "Retention cohort tables describe how durable usage develops over time."),
            _text_command(72, 694, 12, "Payback analysis complements the cohort view and pricing data."),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 22, "Chapter 2 Network Effects"),
            _text_command(72, 670, 12, "The next chapter returns to the main body of the text."),
            _text_command(298, 36, 10, "4"),
        ],
    ]
    _write_pdf(path, pages, title="Appendix Continuation Sample", author="Test Author")


def _write_appendix_intro_after_references_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "The body chapter ends before the references and appendices begin."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 11, "[1] Smith, J. Durable Advantage. 2024."),
            _text_command(72, 696, 11, "[2] Lee, S. Network Density. 2023."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(
                72,
                724,
                11,
                "Appendix Tool Catalog This appendix summarizes the built-in tools used by the system.",
            ),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 11, "The appendix continues with detailed tool descriptions and usage notes."),
            _text_command(298, 36, 10, "4"),
        ],
    ]
    _write_pdf(path, pages, title="Appendix Intro Sample", author="Test Author")


def _write_lettered_appendix_subsections_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "The body chapter ends before the references and appendices begin."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 11, "[1] Smith, J. Durable Advantage. 2024."),
            _text_command(72, 696, 11, "[2] Lee, S. Network Density. 2023."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(
                72,
                724,
                11,
                "A Tool Catalog Table 1 provides the complete catalog of built-in tools; "
                "this appendix summarizes the tools available in the system.",
            ),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 11, "The appendix continues with detailed tool descriptions and usage notes."),
            _text_command(298, 36, 10, "4"),
        ],
        [
            _text_command(
                72,
                724,
                11,
                "Ctrl+L - Clear screen / + text - Trigger command autocomplete "
                "K Prompt Templates This appendix reproduces the system prompt templates used by the toolchain.",
            ),
            _text_command(298, 36, 10, "5"),
        ],
        [
            _text_command(72, 724, 11, "The prompt appendix continues with template excerpts and supporting notes."),
            _text_command(298, 36, 10, "6"),
        ],
    ]
    _write_pdf(path, pages, title="Lettered Appendix Sections", author="Test Author")


def _write_appendix_top_level_subheading_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "The body chapter ends before the references and appendices begin."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 11, "[1] Smith, J. Durable Advantage. 2024."),
            _text_command(72, 696, 11, "[2] Lee, S. Network Density. 2023."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(
                72,
                724,
                11,
                "K Prompt Templates This appendix reproduces the system prompt templates used by the toolchain.",
            ),
            _text_command(72, 696, 11, "The prompt appendix starts with core identity and composition notes."),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 11, "The prompt appendix continues with rendered template examples."),
            _text_command(72, 696, 11, "These pages remain part of the same appendix section."),
            _text_command(298, 36, 10, "4"),
        ],
        [
            _text_command(
                72,
                724,
                12,
                "K.4 Specialized Standalone Templates These templates are loaded directly by their subsystems.",
            ),
            _text_command(72, 696, 11, "The section continues with compaction template notes."),
            _text_command(72, 668, 11, "The section continues with critique template notes."),
            _text_command(72, 640, 11, "The section continues with init template notes."),
            _text_command(298, 36, 10, "5"),
        ],
        [
            _text_command(72, 724, 11, "The standalone template appendix section continues with implementation notes."),
            _text_command(298, 36, 10, "6"),
        ],
    ]
    _write_pdf(path, pages, title="Appendix Top-level Subheading Sample", author="Test Author")


def _write_appendix_nested_subheading_signal_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "The body chapter ends before the appendices begin."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(
                72,
                724,
                11,
                "K Prompt Templates This appendix reproduces the system prompt templates used by the toolchain.",
            ),
            _text_command(72, 696, 11, "The prompt appendix starts with core identity and composition notes."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 11, "The prompt appendix continues with rendered template examples."),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 12, "K.2.5 Context Awareness (Priority 85-95)"),
            _text_command(72, 696, 11, "The nested section discusses output awareness and scratchpad behavior."),
            _text_command(298, 36, 10, "4"),
        ],
    ]
    _write_pdf(path, pages, title="Appendix Nested Subheading Signal Sample", author="Test Author")


def _write_false_positive_guard_pdf(path: Path) -> None:
    abstract_text = (
        "This paper was published in 2017, compares results with prior work [10], "
        "and explains why the method improves sequence modeling without becoming a references page. "
        "The paragraph remains normal body prose even though it mentions years, citations, and commas."
    )
    formula_like_text = (
        "To illustrate dot products, assume the components have mean 0 and variance 1, "
        "then q \\001 k grows with dimensionality 1"
    )
    pages = [
        [
            _text_command(72, 724, 22, "Paper Title"),
            _text_command(72, 670, 11, abstract_text),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 11, formula_like_text),
            _text_command(72, 694, 11, "d k \\051 V \\0501\\051 Additive attention [2] 1"),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(
                72,
                724,
                11,
                "References [1] Smith, J. Durable Advantage. 2024. [2] Lee, S. Network Density. 2023.",
            ),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 11, "[3] Patel, R. Attention Systems. 2022."),
            _text_command(298, 36, 10, "4"),
        ],
    ]
    _write_pdf(path, pages, title="False Positive Guard Sample", author="Test Author")


def _write_isolated_index_false_start_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "This chapter stays in the main body even if one page looks index-like."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 11, "advantage, durable 12, 16"),
            _text_command(72, 696, 11, "network effects 18, 21"),
            _text_command(72, 668, 11, "switching costs 24"),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 22, "Chapter 2 Network Effects"),
            _text_command(72, 670, 12, "The second chapter should not inherit an index section boundary."),
            _text_command(298, 36, 10, "3"),
        ],
    ]
    _write_pdf(path, pages, title="Isolated Index Guard Sample", author="Test Author")


def _write_sparse_index_signature_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "A normal chapter page should remain body content."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 12, "The API call stays inside the chapter body even with short metadata lines."),
            _text_command(72, 698, 11, "GPT -4"),
            _text_command(72, 676, 11, "Model : GPT -4"),
            _text_command(72, 654, 11, "Parameters : Temperature: .7 Max tokens: 256"),
            _text_command(72, 632, 11, "Listing 5.1 first_function.py (API call)"),
            _text_command(72, 610, 11, "The page still explains the workflow rather than acting like a back-of-book index."),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Sparse Index Signature Sample", author="Test Author")


def _write_inline_index_heading_backmatter_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "This chapter establishes the main strategy and context."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 22, "Appendix A Metrics"),
            _text_command(72, 670, 12, "The appendix explains the supporting measurements and tools."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 11, "312 INDEX durable products 4, 8 agent memory 18, 21 switching costs 24"),
            _text_command(72, 696, 11, "network effects 30, 31 retention cohorts 42"),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 12, "Upcoming Titles"),
            _text_command(72, 696, 11, "Multi-Agent Systems with AutoGen"),
            _text_command(72, 668, 11, "ISBN 9781633436145 325 pages $59.99"),
            _text_command(298, 36, 10, "4"),
        ],
    ]
    _write_pdf(path, pages, title="Inline Index Heading Sample", author="Test Author")


def _write_appendix_backmatter_cue_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "The main narrative ends before the appendix and tail matter begin."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 22, "Appendix A Metrics"),
            _text_command(72, 670, 12, "The appendix lists the supporting measurements and formulas."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 12, "The appendix continues with glossary items and sample calculations."),
            _text_command(72, 696, 12, "These notes should remain translatable appendix content."),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 18, "Upcoming Titles"),
            _text_command(72, 696, 12, "Multi-Agent Systems with AutoGen"),
            _text_command(72, 668, 12, "Manning Books"),
            _text_command(72, 640, 12, "ISBN 9781633436145 325 pages $59.99"),
            _text_command(298, 36, 10, "4"),
        ],
    ]
    _write_pdf(path, pages, title="Appendix Backmatter Cue Sample", author="Test Author")


def _write_inline_multi_appendix_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "The main body ends before the appendices begin."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 11, "Appendix A Metrics"),
            _text_command(72, 696, 11, "The first appendix covers measurement definitions and cohort formulas."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 724, 11, "The appendix continues with retention examples and glossary notes."),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 724, 11, "Appendix B Tooling"),
            _text_command(72, 696, 11, "The second appendix describes development tooling and setup steps."),
            _text_command(298, 36, 10, "4"),
        ],
        [
            _text_command(72, 724, 11, "The tooling appendix continues with environment details and scripts."),
            _text_command(298, 36, 10, "5"),
        ],
    ]
    _write_pdf(path, pages, title="Inline Multi Appendix Sample", author="Test Author")


def _write_chapter_intro_recovery_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 770, 12, "1"),
            _text_command(72, 742, 12, "Introduction to agents"),
            _text_command(72, 714, 12, "and their world The agent is an active decision maker in many systems."),
            _text_command(
                72,
                674,
                12,
                "This chapter covers Defining the concept of agents and understanding their environment.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 714, 12, "Agents continue to make choices based on the observations they receive."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(72, 770, 12, "14"),
            _text_command(72, 742, 12, "Harnessing the power"),
            _text_command(72, 714, 12, "of large language models The term large language model now dominates AI."),
            _text_command(
                72,
                674,
                12,
                "This chapter covers Understanding LLMs and connecting them to useful agent workflows.",
            ),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 714, 12, "Large language models supply the reasoning substrate for modern agents."),
            _text_command(298, 36, 10, "4"),
        ],
    ]
    _write_pdf(path, pages, title="Chapter Intro Recovery Sample", author="Test Author")


def _write_embedded_chapter_intro_recovery_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(
                72,
                742,
                12,
                "Exploring multi-agent systems Now we explore collaborative agents in practice. "
                "This chapter covers Building multi-agent systems and understanding coordination.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 714, 12, "Multi-agent systems coordinate via messages, plans, and shared context."),
            _text_command(298, 36, 10, "2"),
        ],
        [
            _text_command(
                72,
                742,
                12,
                "Agent reasoning and evaluation No w that we have explored retrieval, this chapter turns to "
                "reasoning patterns. This chapter covers Prompting for reasoning and evaluating outcomes.",
            ),
            _text_command(298, 36, 10, "3"),
        ],
        [
            _text_command(72, 714, 12, "Reasoning prompts and evaluations make agent behavior more reliable."),
            _text_command(298, 36, 10, "4"),
        ],
    ]
    _write_pdf(path, pages, title="Embedded Chapter Intro Recovery Sample", author="Test Author")


def _write_frontmatter_intro_recovery_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(
                72,
                742,
                12,
                "vi brief contents 1 Introduction to agents and their world 1 2 Harnessing the power of large language models 14",
            ),
            _text_command(298, 36, 10, "vi"),
        ],
        [
            _text_command(
                72,
                742,
                12,
                "xiii preface This book is about building intelligent agents in practice.",
            ),
            _text_command(298, 36, 10, "xiii"),
        ],
        [
            _text_command(72, 770, 12, "1"),
            _text_command(72, 742, 12, "Introduction to agents"),
            _text_command(72, 714, 12, "and their world The agent is an active decision maker in many systems."),
            _text_command(
                72,
                674,
                12,
                "This chapter covers Defining the concept of agents and understanding their environment.",
            ),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 714, 12, "Agents continue to make choices based on the observations they receive."),
            _text_command(298, 36, 10, "2"),
        ],
    ]
    _write_pdf(path, pages, title="Frontmatter Intro Recovery Sample", author="Test Author")


def _write_book_with_preface_contact_and_internal_conclusion_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 724, 22, "Preface"),
            _text_command(72, 670, 12, "This preface explains how to use the book as a practical field guide."),
            _text_command(298, 36, 10, "i"),
        ],
        [
            _text_command(72, 724, 18, "O'Reilly Online Learning"),
            _text_command(72, 684, 12, "Our online learning platform gives you on-demand access to courses and books."),
            _text_command(72, 648, 12, "Please address comments and questions to support@oreilly.com or visit https://oreilly.com."),
            _text_command(72, 612, 18, "How to Contact Us"),
            _text_command(72, 576, 12, "Find us on LinkedIn: https://linkedin.com/company/oreilly-media"),
            _text_command(72, 540, 12, "Watch us on YouTube: https://youtube.com/oreillymedia"),
            _text_command(298, 36, 10, "ii"),
        ],
        [
            _text_command(72, 724, 18, "Acknowledgments"),
            _text_command(72, 670, 12, "We thank the reviewers and early readers who improved the manuscript."),
            _text_command(298, 36, 10, "iii"),
        ],
        [
            _text_command(72, 724, 22, "Chapter 1 Durable Products"),
            _text_command(72, 670, 12, "Agent systems become durable when memory, tools, and evaluation reinforce one another."),
            _text_command(72, 610, 18, "Conclusion"),
            _text_command(72, 574, 12, "This conclusion wraps the chapter but should not become a standalone chapter."),
            _text_command(298, 36, 10, "1"),
        ],
        [
            _text_command(72, 724, 22, "Chapter 2 Learning Systems"),
            _text_command(72, 670, 12, "The next chapter explains how feedback loops improve agent quality over time."),
            _text_command(298, 36, 10, "19"),
        ],
    ]
    _write_pdf(
        path,
        pages,
        title="Practical Agent Systems",
        author="Test Author",
        outline_entries=[
            (1, "Preface", 1),
            (1, "Chapter 1 Durable Products", 4),
            (2, "Conclusion", 4),
            (1, "Chapter 2 Learning Systems", 5),
        ],
    )


def _write_multiline_heading_pdf(path: Path) -> None:
    pages = [
        [
            _text_command(72, 722, 24, "A Thought Leader's Perspective: Power"),
            _text_command(72, 686, 24, "and Responsibility"),
            _text_command(
                72,
                620,
                12,
                "This section explains why powerful systems also require careful governance.",
            ),
            _text_command(298, 36, 10, "1"),
        ]
    ]
    _write_pdf(path, pages, title="Multiline Heading Sample", author="Test Author")


class PdfBootstrapPipelineTests(unittest.TestCase):
    def test_wrapped_prose_heuristics_do_not_treat_acknowledgement_text_as_code_or_table(self) -> None:
        lines = [
            "I am deeply indebted to the many talented people who helped bring this book to life. My",
            "heartfelt thanks go to Marco Fago for his immense contributions, from code and",
            "diagrams to reviewing the entire text. I'm also grateful to Mahtab Syed for his coding work",
            "and to Ankita Guha for her incredibly detailed feedback on so many chapters. The book was",
            "significantly improved by the insightful amendments from Priya Saxena, the careful reviews",
            "from Jae Lee, and the dedicated work of Mario da Roza in creating the NotebookLM version.",
        ]
        text = "\n".join(lines)

        self.assertFalse(_looks_like_code(text, len(lines)))
        self.assertFalse(_looks_like_table(len(lines), lines))

    def test_wrapped_numeric_academic_table_heuristics_detect_compressed_multiline_tables(self) -> None:
        lines = [
            "Team Accuracy (%) Method",
            "[F, 357, 117]",
            "[F, 357, 121]",
            "[F, 249, 124]",
            "[F, 249, 296] Baselines:",
            "- JSF",
            "94.69 (± 0.25)",
            "90.14 (± 0.17)",
            "88.69 (± 0.32)",
            "90.92 (± 0.13)",
            "- One Classifier",
            "83.13 (± 0.21)",
            "84.70 (± 0.13)",
            "84.59 (± 0.12)",
            "83.63 (± 0.08)",
            "Our Approach:",
            "- C. & E. Team",
            "95.45 (± 0.06)",
            "91.72 (± 0.12)",
            "90.36 (± 0.11)",
            "91.74 (± 0.09)",
        ]

        self.assertTrue(_looks_like_table(len(lines), lines))

    def test_wrapped_individual_accuracy_table_heuristics_detect_id_equals_layout(self) -> None:
        lines = [
            "Individual Accuracy (%) Instances allocated to",
            "Classifier F",
            "ID = 357",
            "ID = 117 Classifier F",
            "97.73 (± 1.76)",
            "78.93 (± 2.47)",
            "96.43 (± 1.86)",
            "ID = 357",
            "31.88 (± 4.73)",
            "98.78 (± 0.32)",
            "98.55 (± 0.34)",
            "ID = 117",
            "61.69 (± 1.15)",
            "79.90 (± 0.29)",
            "94.67 (± 0.02)",
        ]

        self.assertTrue(_looks_like_table(len(lines), lines))

    def test_late_table_promotion_merges_wrapped_table_fragments_before_caption_linking(self) -> None:
        service = PdfStructureRecoveryService()
        recovered_blocks = [
            _RecoveredBlock(
                role="body",
                block_type=BlockType.PARAGRAPH,
                text="Team Accuracy (%) Method\n[F, 357, 117]\n[F, 357, 121]\n[F, 249, 124]\n[F, 249, 296]",
                page_start=1,
                page_end=1,
                bbox_regions=[{"page_number": 1, "bbox": [72.0, 96.0, 292.0, 126.0]}],
                reading_order_index=1,
                parse_confidence=0.9,
                flags=[],
                font_size_avg=10.0,
                source_path="sample.pdf",
                anchor="p1-b1",
                metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            ),
            _RecoveredBlock(
                role="table_like",
                block_type=BlockType.TABLE,
                text="Baselines:\n- JSF\n94.69 (± 0.25)\n90.14 (± 0.17)\n88.69 (± 0.32)\n90.92 (± 0.13)",
                page_start=1,
                page_end=1,
                bbox_regions=[{"page_number": 1, "bbox": [72.0, 132.0, 292.0, 224.0]}],
                reading_order_index=2,
                parse_confidence=0.9,
                flags=[],
                font_size_avg=10.0,
                source_path="sample.pdf",
                anchor="p1-b2",
                metadata={"pdf_page_family": "body", "pdf_block_role": "table_like"},
            ),
            _RecoveredBlock(
                role="caption",
                block_type=BlockType.CAPTION,
                text="Table 1: Team accuracies of our approach and the baselines.",
                page_start=1,
                page_end=1,
                bbox_regions=[{"page_number": 1, "bbox": [72.0, 232.0, 292.0, 252.0]}],
                reading_order_index=3,
                parse_confidence=0.9,
                flags=[],
                font_size_avg=9.0,
                source_path="sample.pdf",
                anchor="p1-b3",
                metadata={"pdf_page_family": "body", "pdf_block_role": "caption"},
            ),
        ]

        promoted_blocks = service._promote_late_table_like_bodies(recovered_blocks)
        merged_blocks = service._merge_adjacent_table_fragments(promoted_blocks)
        service._link_artifact_captions(merged_blocks)

        table_blocks = [block for block in merged_blocks if block.role == "table_like"]
        self.assertEqual(len(table_blocks), 1)
        self.assertIn("Team Accuracy (%) Method", table_blocks[0].text)
        self.assertIn("94.69 (± 0.25)", table_blocks[0].text)
        self.assertEqual(
            table_blocks[0].metadata.get("linked_caption_text"),
            "Table 1: Team accuracies of our approach and the baselines.",
        )
        self.assertIn("late_table_like_promoted", table_blocks[0].flags)
        self.assertIn("table_fragments_merged", table_blocks[0].flags)

    def test_late_table_promotion_handles_hyphenated_table_caption_and_numeric_stack_fragments(self) -> None:
        service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Model Selection",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Chapter 1 Model Selection",
                            bbox=(72.0, 96.0, 320.0, 124.0),
                            line_texts=["Chapter 1 Model Selection"],
                            span_count=24,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=18.0,
                            font_size_avg=18.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="This explosion of choice is good news: competition is driving faster innovation, better performance, and lower costs.",
                            bbox=(72.0, 156.0, 432.0, 212.0),
                            line_texts=[
                                "This explosion of choice is good news: competition is driving faster innovation, better performance, and lower costs."
                            ],
                            span_count=48,
                            line_count=1,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                    ],
                    image_blocks=[],
                ),
                PdfPage(
                    page_number=2,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=2,
                            block_number=1,
                            text=(
                                "Table 1-1. HELM Core Scenario leaderboard (August 2025). Comparative benchmark "
                                "performance of the top 10 models across reasoning and evaluation tasks."
                            ),
                            bbox=(72.0, 52.0, 404.0, 90.0),
                            line_texts=[
                                "Table 1-1. HELM Core Scenario leaderboard (August 2025). Comparative benchmark",
                                "performance of the top 10 models across reasoning and evaluation tasks.",
                            ],
                            span_count=28,
                            line_count=2,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                        PdfTextBlock(
                            page_number=2,
                            block_number=2,
                            text=(
                                "Model\nMean\nscore Omni-\nMATH—\nAcc\nGPT-5 mini (2025-08-07)\n0.819\n0.835\n0.756\n"
                                "0.927\n0.855\n0.722\no4-mini (2025-04-16)\n0.812\n0.82\n0.735\n0.929"
                            ),
                            bbox=(75.6, 95.1, 423.0, 203.3),
                            line_texts=[
                                "Model",
                                "Mean",
                                "score Omni-",
                                "MATH—",
                                "Acc",
                                "GPT-5 mini (2025-08-07)",
                                "0.819",
                                "0.835",
                                "0.756",
                                "0.927",
                                "0.855",
                                "0.722",
                                "o4-mini (2025-04-16)",
                                "0.812",
                                "0.82",
                                "0.735",
                                "0.929",
                            ],
                            span_count=64,
                            line_count=17,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                        PdfTextBlock(
                            page_number=2,
                            block_number=3,
                            text=(
                                "0.798\n0.844\n0.726\n0.835\n0.866\n0.718 Grok 4 (0709)\n0.785\n0.851\n0.726\n"
                                "0.949\n0.797\n0.603\nClaude 4 Opus (20250514, extended thinking) 0.78"
                            ),
                            bbox=(75.6, 205.6, 412.1, 290.0),
                            line_texts=[
                                "0.798",
                                "0.844",
                                "0.726",
                                "0.835",
                                "0.866",
                                "0.718 Grok 4 (0709)",
                                "0.785",
                                "0.851",
                                "0.726",
                                "0.949",
                                "0.797",
                                "0.603",
                                "Claude 4 Opus (20250514, extended thinking) 0.78",
                            ],
                            span_count=58,
                            line_count=13,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                        PdfTextBlock(
                            page_number=2,
                            block_number=4,
                            text="That said, they aren’t always the most efficient choice.",
                            bbox=(72.0, 303.0, 432.0, 340.0),
                            line_texts=["That said, they aren’t always the most efficient choice."],
                            span_count=18,
                            line_count=1,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                    ],
                    image_blocks=[],
                ),
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=2,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = service.recover("hyphenated-table-sample.pdf", extraction, profile)

        caption_block = next(block for block in parsed.chapters[0].blocks if block.text.startswith("Table 1-1."))
        table_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.TABLE.value)
        prose_blocks = [block for block in parsed.chapters[0].blocks if block.block_type == BlockType.PARAGRAPH.value]
        self.assertEqual(caption_block.block_type, BlockType.CAPTION.value)
        self.assertIn("GPT-5 mini", table_block.text)
        self.assertIn("Grok 4 (0709)", table_block.text)
        self.assertEqual(table_block.metadata.get("linked_caption_text"), caption_block.text)
        self.assertEqual(caption_block.metadata.get("caption_for_role"), "table")
        self.assertIn("table_fragments_merged", table_block.metadata.get("recovery_flags", []))
        self.assertTrue(any("This explosion of choice is good news" in block.text for block in prose_blocks))
        self.assertTrue(any("That said, they aren’t always the most efficient choice." in block.text for block in prose_blocks))

    def test_normalize_multiline_text_repairs_wrapped_pdf_hyphenation(self) -> None:
        normalized = _normalize_multiline_text(
            "general-\npurpose models require little cus‐\ntomization and sometimes beat o4-\nmini on cost."
        )
        self.assertEqual(
            normalized,
            "general-purpose models require little customization and sometimes beat o4-mini on cost.",
        )

    def test_bootstrap_pipeline_supports_low_risk_text_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "sample.pdf"
            _write_low_risk_text_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(result.document.source_type, SourceType.PDF_TEXT)
        self.assertEqual(result.document.metadata_json["pdf_profile"]["pdf_kind"], "text_pdf")
        self.assertEqual(result.document.metadata_json["pdf_profile"]["layout_risk"], "low")
        self.assertEqual(result.document.title, "Signal vs Noise")
        self.assertEqual(len(result.chapters), 1)

        merged_body_block = next(
            block
            for block in result.blocks
            if block.source_span_json.get("pdf_block_role") == "body"
            and block.source_span_json.get("source_page_end") == 2
        )
        self.assertIn(
            "Pricing power matters because durable advantages compound across cycles.",
            merged_body_block.source_text,
        )
        self.assertIn("cross_page_repaired", merged_body_block.source_span_json["recovery_flags"])
        self.assertIn("dehyphenated", merged_body_block.source_span_json["recovery_flags"])

        header_footer_sentences = [
            sentence
            for sentence in result.sentences
            if sentence.nontranslatable_reason in {"pdf_header", "pdf_footer"}
        ]
        self.assertTrue(header_footer_sentences)
        self.assertTrue(all(not sentence.translatable for sentence in header_footer_sentences))
        self.assertTrue(
            all("Signal vs Noise" not in sentence.source_text for sentence in result.sentences if sentence.translatable)
        )
        self.assertTrue(all(sentence.source_text not in {"1", "2"} for sentence in result.sentences if sentence.translatable))

    def test_bootstrap_pipeline_supports_high_risk_text_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "two-column.pdf"
            _write_high_risk_two_column_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(result.document.source_type, SourceType.PDF_TEXT)
        self.assertEqual(result.document.metadata_json["pdf_profile"]["pdf_kind"], "text_pdf")
        self.assertEqual(result.document.metadata_json["pdf_profile"]["layout_risk"], "high")
        self.assertEqual(len(result.chapters), 1)
        self.assertEqual(result.chapters[0].risk_level, Severity.CRITICAL)
        self.assertIn(
            "layout_risk_high",
            result.chapters[0].metadata_json.get("structure_flags", []),
        )

    def test_bootstrap_pipeline_merges_multiline_heading_continuations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "multiline-heading.pdf"
            _write_multiline_heading_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        heading_blocks = [block for block in result.blocks if block.block_type == BlockType.HEADING]
        self.assertEqual(len(heading_blocks), 1)
        self.assertEqual(
            heading_blocks[0].source_text,
            "A Thought Leader’s Perspective: Power and Responsibility",
        )

    def test_structure_recovery_merges_heading_continuation_fragments(self) -> None:
        service = PdfStructureRecoveryService()
        previous = _RecoveredBlock(
            role="heading",
            block_type=BlockType.HEADING,
            text="A Thought Leader’s Perspective: Power",
            page_start=6,
            page_end=6,
            bbox_regions=[{"page_number": 6, "bbox": [93.0, 103.0, 709.0, 133.0]}],
            reading_order_index=32,
            parse_confidence=0.95,
            flags=[],
            metadata={"pdf_page_family": "body"},
            font_size_avg=24.0,
            source_path="pdf://page/6",
            anchor="p6-b34",
        )
        current = _RecoveredBlock(
            role="heading",
            block_type=BlockType.HEADING,
            text="and Responsibility",
            page_start=6,
            page_end=6,
            bbox_regions=[{"page_number": 6, "bbox": [94.0, 145.0, 380.0, 178.0]}],
            reading_order_index=33,
            parse_confidence=0.95,
            flags=[],
            metadata={"pdf_page_family": "body"},
            font_size_avg=24.0,
            source_path="pdf://page/6",
            anchor="p6-b35",
        )

        merged = service._merge_adjacent_heading_continuations([previous, current])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].text, "A Thought Leader’s Perspective: Power and Responsibility")
        self.assertIn("multiline_heading_merged", merged[0].flags)

    def test_structure_recovery_repairs_prose_continuation_misclassified_as_code(self) -> None:
        service = PdfStructureRecoveryService()
        pages = [PdfPage(page_number=10, width=595.0, height=842.0, blocks=[])]
        previous = _RecoveredBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text=(
                "While the chapters are ordered to build concepts progressively, feel free to use the book "
                "as a reference, jumping to chapters that address specific challenges you face in your "
                "own agent development projects. The appendices provide a comprehensive look at advanced "
                "prompting techniques, principles for applying AI agents in real-world"
            ),
            page_start=10,
            page_end=10,
            bbox_regions=[{"page_number": 10, "bbox": [94.0, 516.0, 724.0, 642.0]}],
            reading_order_index=69,
            parse_confidence=0.95,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            font_size_avg=13.0,
            source_path="pdf://page/10",
            anchor="p10-b69",
        )
        current = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "environments, and an overview of essential agentic frameworks. To complement this,\n"
                "practical online-only tutorials are included, offering step-by-step guidance on building\n"
                "agents with specific platforms like AgentSpace and for the command-line interface. The\n"
                "emphasis throughout is on practical application; we strongly encourage you to run the\n"
                "code examples, experiment with them, and adapt them to build your own intelligent\n"
                "systems on your chosen canvas."
            ),
            page_start=10,
            page_end=10,
            bbox_regions=[{"page_number": 10, "bbox": [94.0, 648.0, 724.0, 790.0]}],
            reading_order_index=70,
            parse_confidence=0.7,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=13.0,
            source_path="pdf://page/10",
            anchor="p10-b70",
        )

        repaired = service._repair_prose_artifact_continuations([previous, current], pages)

        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0].role, "body")
        self.assertEqual(repaired[0].block_type, BlockType.PARAGRAPH)
        self.assertIn("essential agentic frameworks", repaired[0].text)
        self.assertIn("practical online-only tutorials", repaired[0].text)
        self.assertEqual(repaired[0].metadata["pdf_block_role"], "body")
        self.assertIn("prose_artifact_continuation_repaired", repaired[0].flags)

    def test_bootstrap_pipeline_supports_academic_paper_pdf_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-paper.pdf"
            _write_outlined_multi_column_academic_paper_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        profile = result.document.metadata_json["pdf_profile"]
        self.assertEqual(result.document.source_type, SourceType.PDF_TEXT)
        self.assertEqual(profile["extractor_kind"], "pymupdf")
        self.assertEqual(profile["layout_risk"], "medium")
        self.assertEqual(profile["recovery_lane"], "academic_paper")
        self.assertTrue(profile["academic_paper_candidate"])
        self.assertEqual(profile["trailing_reference_page_count"], 1)
        self.assertIn("1 Introduction", [chapter.title_src for chapter in result.chapters])
        self.assertEqual(result.chapters[-1].title_src, "References")
        self.assertEqual(result.chapters[-1].metadata_json["pdf_section_family"], "references")

    def test_recovery_splits_leading_code_prefix_from_mixed_body_block(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text=(
                "from langchain_openai import ChatOpenAI\n"
                "llm = ChatOpenAI(model_name=\"gpt-4o\")\n"
                "messages = [HumanMessage(\"What is the weather today?\")]\n"
                "Tools, meanwhile, are external functions that your model can call to extend its "
                "capabilities beyond text generation."
            ),
            page_start=94,
            page_end=94,
            bbox_regions=[{"page_number": 94, "bbox": [72.0, 320.0, 540.0, 420.0]}],
            reading_order_index=12,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            font_size_avg=10.0,
            source_path="pdf://page/94",
            anchor="p94-b12",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].role, "code_like")
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertTrue(repaired[0].text.startswith("from langchain_openai"))
        self.assertEqual(repaired[1].role, "body")
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertIn("Tools, meanwhile", repaired[1].text)
        self.assertIn("mixed_code_prose_split", repaired[0].flags)

    def test_looks_like_code_uses_multiline_text_when_raw_line_count_is_one(self) -> None:
        text = (
            "from langchain_core.tools import tool\n"
            "import requests\n"
            "@tool\n"
            "def query_wolfram_alpha(expression: str) -> str:\n"
            "    return requests.get(expression).text"
        )

        self.assertTrue(_looks_like_code(text, 1))

    def test_looks_like_code_detects_multiline_json_object(self) -> None:
        text = (
            "{\n"
            '"trends": [\n'
            "{\n"
            '"trend_name": "AI-Powered Personalization",\n'
            '"supporting_data": "73% of consumers prefer to do business with brands that use personal information "\n'
            "}\n"
            "]\n"
            "}"
        )

        self.assertTrue(_looks_like_code(text, 1))

    def test_recovery_splits_code_like_block_with_inline_trailing_prose(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "tool_embeddings = []\n"
                "index_to_tool = {\n"
                "0: query_wolfram_alpha,\n"
                "1: trigger_zapier_webhook,\n"
                "} Those embeddings for your tool catalog only need to be computed once, and now\n"
                "they're ready to be quickly retrieved."
            ),
            page_start=98,
            page_end=98,
            bbox_regions=[{"page_number": 98, "bbox": [72.0, 320.0, 540.0, 420.0]}],
            reading_order_index=18,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/98",
            anchor="p98-b18",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].role, "code_like")
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertNotIn("Those embeddings", repaired[0].text)
        self.assertEqual(repaired[1].role, "body")
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertIn("Those embeddings for your tool catalog", repaired[1].text)

    def test_recovery_splits_json_code_block_with_inline_trailing_prose(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "{\n"
                '"trends": [\n'
                "{\n"
                '"trend_name": "AI-Powered Personalization",\n'
                '"supporting_data": "73% of consumers prefer to do business with brands that use personal information "\n'
                "}\n"
                "]\n"
                "} This structured format ensures that the data is machine-readable and can be "
                "precisely parsed."
            ),
            page_start=99,
            page_end=99,
            bbox_regions=[{"page_number": 99, "bbox": [72.0, 320.0, 540.0, 420.0]}],
            reading_order_index=19,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/99",
            anchor="p99-b19",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].role, "code_like")
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertIn('"trend_name": "AI-Powered Personalization"', repaired[0].text)
        self.assertNotIn("This structured format ensures", repaired[0].text)
        self.assertEqual(repaired[1].role, "body")
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertIn("This structured format ensures that the data is machine-readable", repaired[1].text)

    def test_recovery_splits_single_line_json_code_block_with_trailing_prose(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                '{"trends": [{"trend_name": "AI-Powered Personalization", '
                '"supporting_data": "73% of consumers prefer personalized experiences."}]} '
                "This structured format ensures that the data is machine-readable and can be precisely parsed."
            ),
            page_start=100,
            page_end=100,
            bbox_regions=[{"page_number": 100, "bbox": [72.0, 320.0, 540.0, 420.0]}],
            reading_order_index=20,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/100",
            anchor="p100-b20",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].role, "code_like")
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertNotIn("This structured format ensures", repaired[0].text)
        self.assertEqual(repaired[1].role, "body")
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertIn("This structured format ensures that the data is machine-readable", repaired[1].text)

    def test_recovery_keeps_bare_function_call_inside_code_prefix_before_trailing_prose(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                'print("\\n" + "="*30 + " FINAL RESULT " + "="*30)\n'
                'print("\\nFinal refined code after the reflection process:\\n")\n'
                "print(current_code)\n"
                'if __name__ == "__main__":\n'
                "run_reflection_loop()\n"
                "The code begins by setting up the environment, loading API keys, and initializing a language model."
            ),
            page_start=100,
            page_end=100,
            bbox_regions=[{"page_number": 100, "bbox": [72.0, 320.0, 540.0, 430.0]}],
            reading_order_index=21,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/100",
            anchor="p100-b21",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].role, "code_like")
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertIn("run_reflection_loop()", repaired[0].text)
        self.assertNotIn("The code begins by setting up", repaired[0].text)
        self.assertEqual(repaired[1].role, "body")
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertTrue(repaired[1].text.startswith("The code begins by setting up"))

    def test_recovery_keeps_zero_width_bare_function_call_inside_code_prefix_before_trailing_prose(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                'blog_creation_crew = Crew(\n'
                '    agents=[researcher, writer],\n'
                ")\n"
                'if __name__ == "__main__":\n'
                "main()\u200b\n"
                "We will now delve into further examples within the Google ADK framework."
            ),
            page_start=101,
            page_end=101,
            bbox_regions=[{"page_number": 101, "bbox": [72.0, 320.0, 540.0, 430.0]}],
            reading_order_index=22,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/101",
            anchor="p101-b22",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].role, "code_like")
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertIn('if __name__ == "__main__":', repaired[0].text)
        self.assertIn("main()", repaired[0].text)
        self.assertNotIn("We will now delve into further examples", repaired[0].text)
        self.assertEqual(repaired[1].role, "body")
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertTrue(repaired[1].text.startswith("We will now delve into further examples"))

    def test_recovery_keeps_import_and_wrapped_comment_continuations_inside_code_block(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "from langchain_core.runnables import RunnablePassthrough,\n"
                "RunnableBranch\n"
                "# --- Define Coordinator Router Chain (equivalent to ADK\n"
                "coordinator's instruction) ---\n"
                "coordinator_router_prompt = ChatPromptTemplate.from_messages([\n"
                '("system", "Analyze the user request.")\n'
                '("user", "{request}")\n'
                "])"
            ),
            page_start=101,
            page_end=103,
            bbox_regions=[{"page_number": 101, "bbox": [72.0, 320.0, 540.0, 430.0]}],
            reading_order_index=22,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/101",
            anchor="p101-b22",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0].role, "code_like")
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertIn("RunnableBranch", repaired[0].text)
        self.assertIn("coordinator's instruction) ---", repaired[0].text)

    def test_recovery_treats_wrapped_shell_command_as_code(self) -> None:
        command_text = (
            "pip install langchain langgraph google-cloud-aiplatform\n"
            "langchain-google-genai google-adk deprecated pydantic"
        )

        self.assertTrue(_looks_like_code(command_text, 2))

    def test_shell_command_continuation_does_not_absorb_wrapped_explanatory_prose(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "pip install langchain langchain-community langchain-openai langgraph\n"
                "Note that langchain-openai can be substituted with the appropriate package for a different\n"
                "model provider. Subsequently, the execution environment must be configured."
            ),
            page_start=103,
            page_end=103,
            bbox_regions=[{"page_number": 103, "bbox": [72.0, 320.0, 540.0, 410.0]}],
            reading_order_index=25,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/103",
            anchor="p103-b25",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertEqual(repaired[0].text, "pip install langchain langchain-community langchain-openai langgraph")
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertTrue(repaired[1].text.startswith("Note that langchain-openai"))

    def test_code_continuation_recognizes_pipe_operator_after_parenthesized_assignment(self) -> None:
        self.assertTrue(
            _looks_like_code_continuation_line(
                "| prompt_transform",
                [
                    "full_chain = (",
                    '{"specifications": extraction_chain}',
                ],
            )
        )

    def test_recovery_splits_operator_led_code_fragment_before_trailing_prose(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "| prompt_transform\n"
                "| llm\n"
                "| StrOutputParser()\n"
                ")\n"
                'print("\\n--- Final JSON Output ---")\n'
                "print(final_result)\n"
                "This Python code demonstrates how to use the LangChain library to process text."
            ),
            page_start=102,
            page_end=103,
            bbox_regions=[{"page_number": 102, "bbox": [72.0, 320.0, 540.0, 430.0]}],
            reading_order_index=24,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/102",
            anchor="p102-b24",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertIn("| prompt_transform", repaired[0].text)
        self.assertIn("print(final_result)", repaired[0].text)
        self.assertNotIn("This Python code demonstrates", repaired[0].text)
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertTrue(repaired[1].text.startswith("This Python code demonstrates"))

    def test_recovery_splits_trailing_prose_after_code_when_comments_contain_apostrophes(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "# The StrOutputParser() converts the LLM's message output to a simple\n"
                "string.\n"
                "extraction_chain = prompt_extract | llm | StrOutputParser()\n"
                "# The full chain passes the output of the extraction chain into the\n"
                "'specifications'\n"
                "# variable for the transformation prompt.\n"
                "full_chain = (\n"
                '{"specifications": extraction_chain}\n'
                "| prompt_transform\n"
                "| llm\n"
                "| StrOutputParser()\n"
                ")\n"
                'print("\\n--- Final JSON Output ---")\n'
                "print(final_result) This Python code demonstrates how to use the LangChain library to process text.\n"
                "It utilizes two separate prompts and then prints the final result."
            ),
            page_start=102,
            page_end=103,
            bbox_regions=[{"page_number": 102, "bbox": [72.0, 320.0, 540.0, 430.0]}],
            reading_order_index=24,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/102",
            anchor="p102-b24-apostrophe",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertIn("print(final_result)", repaired[0].text)
        self.assertNotIn("This Python code demonstrates", repaired[0].text)
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertTrue(repaired[1].text.startswith("This Python code demonstrates"))

    def test_recovery_merges_cross_page_code_continuations_when_next_page_starts_with_code_arguments(self) -> None:
        service = PdfStructureRecoveryService()
        previous = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text="# Researcher 3: Carbon Capture\nresearcher_agent_3 = LlmAgent(",
            page_start=56,
            page_end=56,
            bbox_regions=[{"page_number": 56, "bbox": [72.0, 620.0, 548.0, 736.0]}],
            reading_order_index=40,
            parse_confidence=0.92,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/56",
            anchor="p56-b40",
        )
        current = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                'name="CarbonCaptureResearcher",\n'
                "model=GEMINI_MODEL,\n"
                'instruction="""You are an AI Research Assistant specializing in climate solutions.'
            ),
            page_start=57,
            page_end=57,
            bbox_regions=[{"page_number": 57, "bbox": [72.0, 92.0, 548.0, 228.0]}],
            reading_order_index=1,
            parse_confidence=0.91,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/56",
            anchor="p57-b1",
        )
        pages = [
            PdfPage(page_number=56, width=595.0, height=842.0, blocks=[], image_blocks=[]),
            PdfPage(page_number=57, width=595.0, height=842.0, blocks=[], image_blocks=[]),
        ]

        merged = service._merge_cross_page_code_continuations([previous, current], pages)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].page_end, 57)
        self.assertIn("researcher_agent_3 = LlmAgent(", merged[0].text)
        self.assertIn('name="CarbonCaptureResearcher",', merged[0].text)
        self.assertIn("cross_page_code_continuation_merged", merged[0].flags)

    def test_recovery_merges_cross_page_code_continuations_across_footer_separators(self) -> None:
        service = PdfStructureRecoveryService()
        previous = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                '# --- 3. Define the Merger Agent (Runs *after* the parallel agents)\n'
                'instruction="""You are an AI Assistant responsible for combining research findings.\n'
                "Do NOT add"
            ),
            page_start=56,
            page_end=57,
            bbox_regions=[
                {"page_number": 56, "bbox": [72.0, 620.0, 548.0, 736.0]},
                {"page_number": 57, "bbox": [72.0, 80.0, 548.0, 120.0]},
            ],
            reading_order_index=40,
            parse_confidence=0.92,
            flags=["cross_page_repaired"],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/56",
            anchor="p56-b40",
        )
        footer_page_56 = _RecoveredBlock(
            role="footer",
            block_type=BlockType.PARAGRAPH,
            text="10",
            page_start=56,
            page_end=56,
            bbox_regions=[{"page_number": 56, "bbox": [500.0, 780.0, 540.0, 820.0]}],
            reading_order_index=41,
            parse_confidence=0.99,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "footer"},
            font_size_avg=10.0,
            source_path="pdf://page/56",
            anchor="p56-footer",
        )
        footer_page_57 = _RecoveredBlock(
            role="footer",
            block_type=BlockType.PARAGRAPH,
            text="11",
            page_start=57,
            page_end=57,
            bbox_regions=[{"page_number": 57, "bbox": [500.0, 780.0, 540.0, 820.0]}],
            reading_order_index=1,
            parse_confidence=0.99,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "footer"},
            font_size_avg=10.0,
            source_path="pdf://page/56",
            anchor="p57-footer",
        )
        current = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "any external knowledge, facts, or details not present in these specific summaries.\n"
                '""",\n'
                "description=\"Combines research findings.\")"
            ),
            page_start=58,
            page_end=58,
            bbox_regions=[{"page_number": 58, "bbox": [72.0, 92.0, 548.0, 228.0]}],
            reading_order_index=1,
            parse_confidence=0.91,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/56",
            anchor="p58-b1",
        )
        pages = [
            PdfPage(page_number=56, width=595.0, height=842.0, blocks=[], image_blocks=[]),
            PdfPage(page_number=57, width=595.0, height=842.0, blocks=[], image_blocks=[]),
            PdfPage(page_number=58, width=595.0, height=842.0, blocks=[], image_blocks=[]),
        ]

        merged = service._merge_cross_page_code_continuations(
            [previous, footer_page_56, footer_page_57, current],
            pages,
        )

        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0].block_type, BlockType.CODE)
        self.assertEqual(merged[0].page_end, 58)
        self.assertIn("Do NOT add", merged[0].text)
        self.assertIn("any external knowledge, facts, or details not present", merged[0].text)
        self.assertIn('description="Combines research findings."', merged[0].text)
        self.assertIn("cross_page_code_continuation_merged", merged[0].flags)

    def test_code_continuation_recognizes_opening_quoted_string_after_call_prefix(self) -> None:
        self.assertTrue(
            _looks_like_code_continuation_line(
                '"Extract the technical specifications from the following',
                ["prompt_extract = ChatPromptTemplate.from_template("],
            )
        )

    def test_recovery_does_not_split_labeled_prose_prefix_as_code(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "What: Agentic systems must often respond to a wide variety of inputs and situations\n"
                "that cannot be handled by a single, linear process. A simple sequential workflow lacks\n"
                "the ability to make decisions based on context."
            ),
            page_start=104,
            page_end=104,
            bbox_regions=[{"page_number": 104, "bbox": [72.0, 320.0, 540.0, 410.0]}],
            reading_order_index=23,
            parse_confidence=0.93,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/104",
            anchor="p104-b23",
        )

        repaired = service._split_mixed_code_prose_blocks([block])

        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0].role, "code_like")
        self.assertEqual(repaired[0].block_type, BlockType.CODE)
        self.assertEqual(repaired[0].text, block.text)

    def test_recovery_merges_same_anchor_code_continuations_before_splitting_trailing_prose(self) -> None:
        service = PdfStructureRecoveryService()
        prefix = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "{\n"
                '"trends": [\n'
                "{\n"
                '"trend_name": "AI-Powered Personalization",\n'
                '"supporting_data": "73% of consumers prefer to do business with'
            ),
            page_start=101,
            page_end=101,
            bbox_regions=[{"page_number": 101, "bbox": [72.0, 320.0, 540.0, 370.0]}],
            reading_order_index=21,
            parse_confidence=0.92,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/101",
            anchor="p101-b21",
        )
        continuation = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                'brands that use personal information to make their shopping\n'
                'experiences more relevant."\n'
                "},\n"
                "{\n"
                '"trend_name": "Sustainable and Ethical Brands",\n'
                '"supporting_data": "Sales of products with ESG-related claims."\n'
                "}\n"
                "]\n"
                "}\n"
                "This structured format ensures that the data is machine-readable and can be precisely parsed."
            ),
            page_start=101,
            page_end=101,
            bbox_regions=[{"page_number": 101, "bbox": [72.0, 372.0, 540.0, 430.0]}],
            reading_order_index=22,
            parse_confidence=0.88,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/101",
            anchor="p101-b21",
        )

        merged = service._merge_same_anchor_code_continuations([prefix, continuation])
        repaired = service._split_mixed_code_prose_blocks(merged)

        self.assertEqual(len(merged), 1)
        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].anchor, "p101-b21")
        self.assertEqual(repaired[1].anchor, "p101-b21-trailing-prose")
        self.assertIn('"trend_name": "Sustainable and Ethical Brands"', repaired[0].text)
        self.assertNotIn("This structured format ensures", repaired[0].text)
        self.assertIn("This structured format ensures that the data is machine-readable", repaired[1].text)

    def test_recovery_promotes_docstring_body_adjacent_to_code(self) -> None:
        service = PdfStructureRecoveryService()
        code_prefix = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text="@tool\ndef trigger_zapier_webhook(zap_id: str, payload: dict) -> str:",
            page_start=95,
            page_end=95,
            bbox_regions=[{"page_number": 95, "bbox": [72.0, 180.0, 540.0, 230.0]}],
            reading_order_index=10,
            parse_confidence=0.95,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/95",
            anchor="p95-b10",
        )
        docstring_block = _RecoveredBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text=(
                "\"\"\"\n"
                "Trigger a Zapier webhook to execute a predefined Zap.\n"
                "Args:\n"
                "zap_id (str): The unique identifier for the Zap.\n"
                "Returns:\n"
                "str: Confirmation message upon successful triggering.\n"
                "\"\"\""
            ),
            page_start=95,
            page_end=95,
            bbox_regions=[{"page_number": 95, "bbox": [72.0, 232.0, 540.0, 300.0]}],
            reading_order_index=11,
            parse_confidence=0.92,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            font_size_avg=10.0,
            source_path="pdf://page/95",
            anchor="p95-b11",
        )
        code_suffix = _RecoveredBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text="response = requests.post(zapier_webhook_url, json=payload)\nreturn response.text",
            page_start=95,
            page_end=95,
            bbox_regions=[{"page_number": 95, "bbox": [72.0, 302.0, 540.0, 360.0]}],
            reading_order_index=12,
            parse_confidence=0.92,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            font_size_avg=10.0,
            source_path="pdf://page/95",
            anchor="p95-b12",
        )

        promoted = service._promote_late_code_like_bodies([code_prefix, docstring_block, code_suffix])

        self.assertEqual(promoted[1].role, "code_like")
        self.assertEqual(promoted[1].block_type, BlockType.CODE)
        self.assertIn("late_code_like_promoted", promoted[1].flags)

    def test_recovery_promotes_comment_body_adjacent_to_code(self) -> None:
        service = PdfStructureRecoveryService()
        code_prefix = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text="def build_agent(prompt: str) -> str:",
            page_start=95,
            page_end=95,
            bbox_regions=[{"page_number": 95, "bbox": [72.0, 180.0, 540.0, 230.0]}],
            reading_order_index=10,
            parse_confidence=0.95,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=10.0,
            source_path="pdf://page/95",
            anchor="p95-b10",
        )
        comment_block = _RecoveredBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text=(
                "# Validate the prompt before building the chain.\n"
                "# Reuse the cached planner when the prompt fingerprint matches."
            ),
            page_start=95,
            page_end=95,
            bbox_regions=[{"page_number": 95, "bbox": [72.0, 232.0, 540.0, 284.0]}],
            reading_order_index=11,
            parse_confidence=0.92,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            font_size_avg=10.0,
            source_path="pdf://page/95",
            anchor="p95-b11",
        )
        code_suffix = _RecoveredBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text="    return planner.run(prompt)",
            page_start=95,
            page_end=95,
            bbox_regions=[{"page_number": 95, "bbox": [72.0, 286.0, 540.0, 332.0]}],
            reading_order_index=12,
            parse_confidence=0.92,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            font_size_avg=10.0,
            source_path="pdf://page/95",
            anchor="p95-b12",
        )

        promoted = service._promote_late_code_like_bodies([code_prefix, comment_block, code_suffix])

        self.assertEqual(promoted[1].role, "code_like")
        self.assertEqual(promoted[1].block_type, BlockType.CODE)
        self.assertIn("late_code_like_promoted", promoted[1].flags)

    def test_recovery_promotes_cross_page_code_body(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text=(
                "# Define tool groups with descriptions\n"
                "tool_groups = {\n"
                "\"Computation\": {\n"
                "\"tools\": []\n"
                "}\n"
                "@tool\n"
                "def select_group_llm(query: str) -> str:\n"
                "return response.content.strip()"
            ),
            page_start=123,
            page_end=124,
            bbox_regions=[
                {"page_number": 123, "bbox": [72.0, 320.0, 540.0, 700.0]},
                {"page_number": 124, "bbox": [72.0, 48.0, 540.0, 220.0]},
            ],
            reading_order_index=30,
            parse_confidence=0.91,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            font_size_avg=10.0,
            source_path="pdf://page/123",
            anchor="p123-b30",
        )

        promoted = service._promote_late_code_like_bodies([block])

        self.assertEqual(promoted[0].role, "code_like")
        self.assertEqual(promoted[0].block_type, BlockType.CODE)
        self.assertIn("late_code_like_promoted", promoted[0].flags)

    def test_bootstrap_pipeline_recovers_inline_academic_section_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-inline-sections.pdf"
            _write_academic_paper_with_inline_sections_pdf(pdf_path)

            extractor = BasicPdfTextExtractor()
            parser = PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor))
            parsed = parser.parse(pdf_path)

        heading_texts = [
            block.text
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.HEADING.value
        ]
        self.assertIn("Abstract", heading_texts)
        self.assertIn("1 Introduction", heading_texts)
        self.assertIn("2 Model Architecture", heading_texts)
        self.assertIn("3 Training", heading_texts)
        self.assertIn("4 Results", heading_texts)

        recovered_heading = next(
            block
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.HEADING.value and block.text == "2 Model Architecture"
        )
        self.assertIn("academic_section_heading_recovered", recovered_heading.metadata["recovery_flags"])
        self.assertEqual(recovered_heading.metadata["pdf_academic_heading_kind"], "numbered")
        self.assertEqual(recovered_heading.metadata["pdf_academic_section_level"], 1)

    def test_bootstrap_pipeline_cleans_noisy_inline_academic_section_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-noisy-inline-sections.pdf"
            _write_academic_paper_with_noisy_inline_sections_pdf(pdf_path)

            extractor = BasicPdfTextExtractor()
            parser = PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor))
            parsed = parser.parse(pdf_path)

        heading_texts = [
            block.text
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.HEADING.value
        ]
        self.assertIn("3 Model Architecture", heading_texts)
        self.assertIn("3.1 Encoder and Decoder Stacks", heading_texts)
        self.assertIn("3.4 Embeddings and Softmax", heading_texts)
        self.assertIn("7 Conclusion", heading_texts)
        self.assertNotIn("3 Model Ar", heading_texts)
        self.assertNotIn("3.1 Encoder and Decoder Stacks Encoder:", heading_texts)
        self.assertNotIn("3.4 Embeddings and Softmax Similarly", heading_texts)
        self.assertNotIn("Model", heading_texts)

    def test_bootstrap_pipeline_repairs_broken_academic_heading_tail_before_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-broken-heading-tail.pdf"
            _write_academic_paper_with_broken_heading_tail_pdf(pdf_path)

            extractor = BasicPdfTextExtractor()
            parser = PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor))
            parsed = parser.parse(pdf_path)

        heading_texts = [
            block.text
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.HEADING.value
        ]
        self.assertIn("3.2.1 Scaled Dot-Product Attention", heading_texts)
        self.assertIn("3.2.2 Multi-Head Attention", heading_texts)
        self.assertNotIn("3.2.1 Scaled Dot-Pr", heading_texts)
        broken_heading = next(
            block
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.HEADING.value and block.text == "3.2.1 Scaled Dot-Product Attention"
        )
        self.assertIn("academic_section_heading_recovered", broken_heading.metadata["recovery_flags"])

    def test_profiler_keeps_positioned_multi_column_academic_paper_in_medium_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-positioned-columns.pdf"
            _write_positioned_multi_column_academic_paper_pdf(pdf_path)

            extractor = BasicPdfTextExtractor()
            profile = PdfFileProfiler(extractor).profile(pdf_path)

        self.assertEqual(profile.layout_risk, "medium")
        self.assertEqual(profile.recovery_lane, "academic_paper")
        self.assertTrue(profile.academic_paper_candidate)
        self.assertGreaterEqual(profile.multi_column_page_count, 2)

    def test_bootstrap_pipeline_orders_positioned_multi_column_academic_paper_left_then_right(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-positioned-columns.pdf"
            _write_positioned_multi_column_academic_paper_pdf(pdf_path)

            extractor = BasicPdfTextExtractor()
            parser = PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor))
            parsed = parser.parse(pdf_path)

        body_texts = [
            block.text
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.metadata.get("pdf_page_family") == "body"
            and block.block_type in {BlockType.HEADING.value, BlockType.PARAGRAPH.value}
        ]
        combined_body_text = " ".join(
            block_text
            for block_text in body_texts
            if block_text
        )
        self.assertLess(
            combined_body_text.index("Left column continues with more detail"),
            combined_body_text.index("2 Related Work Right"),
        )
        self.assertLess(
            combined_body_text.index("Left column ends with a final sentence"),
            combined_body_text.index("Right column then discusses related work"),
        )
        profile = parsed.metadata["pdf_profile"]
        self.assertEqual(profile["layout_risk"], "medium")
        self.assertEqual(profile["recovery_lane"], "academic_paper")
        self.assertGreaterEqual(profile["multi_column_page_count"], 2)

    def test_profiler_recognizes_outlined_multi_column_academic_paper_with_pymupdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-outlined-columns.pdf"
            _write_outlined_multi_column_academic_paper_pdf(pdf_path)

            extractor = PyMuPDFTextExtractor()
            profile = PdfFileProfiler(extractor).profile(pdf_path)

        self.assertEqual(profile.extractor_kind, "pymupdf")
        self.assertEqual(profile.layout_risk, "medium")
        self.assertEqual(profile.recovery_lane, "academic_paper")
        self.assertTrue(profile.academic_paper_candidate)
        self.assertTrue(profile.outline_present)

    def test_profiler_recognizes_single_column_pymupdf_academic_paper_without_outline(self) -> None:
        reference_text = (
            "[1] Ashish Vaswani, Noam Shazeer, and Niki Parmar. 2017. Attention Is All You Need. "
            "[2] Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua Bengio. 2015. Neural machine translation."
        )
        extraction = PdfExtraction(
            title="Attention Is All You Need",
            author="Test Author",
            metadata={"pdf_extractor": "pymupdf"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Attention Is All You Need",
                            bbox=(180.0, 92.0, 432.0, 118.0),
                            line_texts=["Attention Is All You Need"],
                            span_count=36,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=20.0,
                            font_size_avg=19.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="Abstract",
                            bbox=(280.0, 320.0, 332.0, 336.0),
                            line_texts=["Abstract"],
                            span_count=10,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=12.0,
                            font_size_avg=11.5,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=3,
                            text=(
                                "The abstract summarizes the architecture and explains why the model "
                                "scales well across sequence transduction tasks."
                            ),
                            bbox=(120.0, 344.0, 492.0, 420.0),
                            line_texts=[
                                "The abstract summarizes the architecture and explains why the model",
                                "scales well across sequence transduction tasks.",
                            ],
                            span_count=84,
                            line_count=2,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=4,
                            text="1\nIntroduction",
                            bbox=(108.0, 520.0, 206.0, 544.0),
                            line_texts=["1", "Introduction"],
                            span_count=16,
                            line_count=2,
                            font_size_min=11.0,
                            font_size_max=12.0,
                            font_size_avg=11.5,
                        ),
                    ],
                ),
                *[
                    PdfPage(
                        page_number=index,
                        width=612.0,
                        height=792.0,
                        blocks=[
                            PdfTextBlock(
                                page_number=index,
                                block_number=1,
                                text="attention " * 520,
                                bbox=(108.0, 120.0, 504.0, 720.0),
                                line_texts=["attention " * 520],
                                span_count=1600,
                                line_count=38,
                                font_size_min=9.0,
                                font_size_max=10.0,
                                font_size_avg=9.5,
                            )
                        ],
                    )
                    for index in (2, 3)
                ],
                *[
                    PdfPage(
                        page_number=index,
                        width=612.0,
                        height=792.0,
                        blocks=[
                            PdfTextBlock(
                                page_number=index,
                                block_number=1,
                                text=reference_text,
                                bbox=(108.0, 120.0, 504.0, 720.0),
                                line_texts=[reference_text],
                                span_count=220,
                                line_count=8,
                                font_size_min=9.0,
                                font_size_max=10.0,
                                font_size_avg=9.5,
                            )
                        ],
                    )
                    for index in (4, 5)
                ],
            ],
        )

        profile = PdfFileProfiler().profile_from_extraction(extraction)

        self.assertEqual(profile.extractor_kind, "pymupdf")
        self.assertEqual(profile.layout_risk, "medium")
        self.assertEqual(profile.recovery_lane, "academic_paper")
        self.assertTrue(profile.academic_paper_candidate)
        self.assertEqual(profile.multi_column_page_count, 0)

    def test_bootstrap_pipeline_waits_for_same_page_outline_heading_in_academic_paper(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-outlined-columns.pdf"
            _write_outlined_multi_column_academic_paper_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        chapter_titles = [chapter.title_src for chapter in result.chapters]
        self.assertIn("1 Introduction", chapter_titles)
        self.assertIn("2 Related Work", chapter_titles)

        introduction_chapter = next(chapter for chapter in result.chapters if chapter.title_src == "1 Introduction")
        related_work_chapter = next(chapter for chapter in result.chapters if chapter.title_src == "2 Related Work")
        self.assertLess(introduction_chapter.ordinal, related_work_chapter.ordinal)
        related_work_blocks = [block for block in result.blocks if block.chapter_id == related_work_chapter.id]
        self.assertTrue(related_work_blocks)
        self.assertEqual(related_work_blocks[0].block_type, BlockType.HEADING)
        self.assertEqual(related_work_blocks[0].source_text, "2 Related Work")
        self.assertIn(
            "Related work in the right column compares prior deferr",
            " ".join(block.source_text for block in related_work_blocks),
        )
        self.assertNotEqual(related_work_chapter.metadata_json.get("source_page_start"), 1)

        profile = result.document.metadata_json["pdf_profile"]
        self.assertEqual(profile["extractor_kind"], "pymupdf")
        self.assertEqual(profile["layout_risk"], "medium")
        self.assertEqual(profile["recovery_lane"], "academic_paper")

    def test_bootstrap_pipeline_keeps_first_page_abstract_continuation_with_title_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-asymmetric-first-page.pdf"
            _write_asymmetric_first_page_academic_paper_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        chapter_titles = [chapter.title_src for chapter in result.chapters]
        self.assertIn("1 Introduction", chapter_titles)
        title_page = result.chapters[0]
        introduction_chapter = next(chapter for chapter in result.chapters if chapter.title_src == "1 Introduction")
        title_page_blocks = [block for block in result.blocks if block.chapter_id == title_page.id]
        introduction_blocks = [block for block in result.blocks if block.chapter_id == introduction_chapter.id]
        title_page_text = " ".join(block.source_text for block in title_page_blocks)
        introduction_text = " ".join(block.source_text for block in introduction_blocks)

        self.assertIn(
            "Machine learning (ML) models are increasingly used in application domains with human experts.",
            title_page_text,
        )
        self.assertIn(
            "of side information that is not accessible to the ML mod",
            title_page_text,
        )
        self.assertNotIn(
            "of side information that is not accessible to the ML mod",
            introduction_text,
        )
        self.assertTrue(introduction_blocks)
        self.assertEqual(introduction_blocks[0].block_type, BlockType.HEADING)
        self.assertEqual(introduction_blocks[0].source_text, "1 Introduction")
        first_page_evidence = result.document.metadata_json["pdf_page_evidence"]["pdf_pages"][0]
        self.assertEqual(first_page_evidence["page_layout_risk"], "high")
        self.assertIn("academic_first_page_asymmetric", first_page_evidence["page_layout_reasons"])

    def test_bootstrap_pipeline_keeps_uppercase_first_page_abstract_continuation_with_title_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-asymmetric-uppercase-first-page.pdf"
            _write_asymmetric_first_page_academic_paper_with_uppercase_continuation_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        chapter_titles = [chapter.title_src for chapter in result.chapters]
        self.assertIn("1 Introduction", chapter_titles)
        title_page = result.chapters[0]
        introduction_chapter = next(chapter for chapter in result.chapters if chapter.title_src == "1 Introduction")
        title_page_text = " ".join(block.source_text for block in result.blocks if block.chapter_id == title_page.id)
        introduction_text = " ".join(
            block.source_text for block in result.blocks if block.chapter_id == introduction_chapter.id
        )

        self.assertIn(
            "This abstract continuation begins with uppercase text",
            title_page_text,
        )
        self.assertIn(
            "These concluding abstract sentences describe expert",
            title_page_text,
        )
        self.assertNotIn(
            "This abstract continuation begins with uppercase text",
            introduction_text,
        )
        self.assertNotIn(
            "These concluding abstract sentences describe expert",
            introduction_text,
        )

    def test_parser_recovers_title_and_references_for_single_column_research_paper(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "single-column-research-paper.pdf"
            _write_single_column_research_paper_pdf(pdf_path)

            extractor = BasicPdfTextExtractor()
            parser = PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor))
            parsed = parser.parse(pdf_path)

        self.assertEqual(
            parsed.title,
            "Forming Effective Human-AI Teams: Building Machine Learning Models that Complement the Capabilities of Multiple Experts",
        )
        self.assertEqual([chapter.title for chapter in parsed.chapters], [parsed.title, "References"])
        self.assertEqual(
            [chapter.metadata["pdf_section_family"] for chapter in parsed.chapters],
            ["body", "references"],
        )
        self.assertEqual(
            parsed.metadata["pdf_page_evidence"]["pdf_pages"][2]["page_family"],
            "references",
        )
        heading_texts = [
            block.text
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.HEADING.value
        ]
        self.assertIn(parsed.title, heading_texts)
        self.assertIn("References", heading_texts)
        first_body_text = next(
            block.text
            for block in parsed.chapters[0].blocks
            if block.block_type == BlockType.PARAGRAPH.value
        )
        self.assertIn("Abstract Machine learning models are increasingly used", first_body_text)

    def test_parser_normalizes_broken_first_page_title_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "broken-title-heading-paper.pdf"
            _write_broken_title_heading_paper_pdf(pdf_path)

            extractor = BasicPdfTextExtractor()
            parser = PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor))
            parsed = parser.parse(pdf_path)

        self.assertEqual(parsed.title, "Attention is All you Need")
        self.assertEqual(parsed.chapters[0].title, "Attention is All you Need")
        heading_texts = [
            block.text
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.HEADING.value
        ]
        self.assertIn("Attention Is All You Need", heading_texts)
        self.assertNotIn("Attention Is All Y ou Need", heading_texts)

    def test_bootstrap_pipeline_uses_toc_entries_to_recover_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "toc-driven.pdf"
            _write_toc_driven_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(len(result.chapters), 2)
        self.assertEqual([chapter.title_src for chapter in result.chapters], ["Strategic Moats", "Network Effects"])
        self.assertEqual(
            [chapter.metadata_json["source_page_start"] for chapter in result.chapters],
            [2, 3],
        )
        self.assertTrue(all("Contents" not in block.source_text for block in result.blocks))
        self.assertTrue(all("........" not in block.source_text for block in result.blocks))

    def test_bootstrap_pipeline_links_footnotes_and_marks_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "footnotes.pdf"
            _write_footnote_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        footnote_blocks = [block for block in result.blocks if block.block_type == BlockType.FOOTNOTE]
        self.assertEqual(len(footnote_blocks), 2)
        linked_footnote = next(block for block in footnote_blocks if block.source_text.startswith("1 "))
        orphaned_footnote = next(block for block in footnote_blocks if block.source_text.startswith("2 "))
        body_block = next(
            block for block in result.blocks if block.source_text.startswith("A moat compounds over time")
        )

        self.assertTrue(linked_footnote.source_span_json["footnote_anchor_matched"])
        self.assertEqual(linked_footnote.source_span_json["footnote_anchor_label"], "1")
        self.assertEqual(
            linked_footnote.source_span_json["footnote_anchor_block_anchor"],
            body_block.source_span_json["anchor"],
        )
        self.assertIn("footnote_anchor_linked", linked_footnote.source_span_json["recovery_flags"])

        self.assertFalse(orphaned_footnote.source_span_json["footnote_anchor_matched"])
        self.assertEqual(orphaned_footnote.source_span_json["footnote_anchor_label"], "2")
        self.assertIn("footnote_orphaned", orphaned_footnote.source_span_json["recovery_flags"])

    def test_bootstrap_pipeline_repairs_cross_page_footnote_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "cross-page-footnotes.pdf"
            _write_cross_page_footnote_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        footnote_blocks = [block for block in result.blocks if block.block_type == BlockType.FOOTNOTE]
        self.assertEqual(len(footnote_blocks), 1)
        footnote_block = footnote_blocks[0]
        self.assertIn("continues on the next page without repeating the marker.", footnote_block.source_text)
        self.assertEqual(footnote_block.source_span_json["source_page_start"], 1)
        self.assertEqual(footnote_block.source_span_json["source_page_end"], 2)
        self.assertTrue(footnote_block.source_span_json["footnote_anchor_matched"])
        self.assertIn("cross_page_repaired", footnote_block.source_span_json["recovery_flags"])
        self.assertIn("footnote_continuation_repaired", footnote_block.source_span_json["recovery_flags"])

    def test_bootstrap_pipeline_links_footnote_that_starts_on_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "next-page-footnote.pdf"
            _write_next_page_footnote_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        footnote_block = next(block for block in result.blocks if block.block_type == BlockType.FOOTNOTE)
        body_block = next(
            block for block in result.blocks if block.source_text.startswith("A moat compounds over time1.")
        )
        self.assertTrue(footnote_block.source_span_json["footnote_anchor_matched"])
        self.assertEqual(footnote_block.source_span_json["footnote_anchor_label"], "1")
        self.assertEqual(
            footnote_block.source_span_json["footnote_anchor_block_anchor"],
            body_block.source_span_json["anchor"],
        )
        self.assertEqual(footnote_block.source_span_json["footnote_anchor_page"], 1)
        self.assertNotIn("footnote_orphaned", footnote_block.source_span_json["recovery_flags"])

    def test_bootstrap_pipeline_relocates_same_page_multisegment_footnote_paragraph(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "same-page-multisegment-footnote.pdf"
            _write_same_page_multisegment_footnote_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        footnote_blocks = [block for block in result.blocks if block.block_type == BlockType.FOOTNOTE]
        self.assertEqual(len(footnote_blocks), 1)
        footnote_block = footnote_blocks[0]
        self.assertIn("Second paragraph starts a new sentence with more context.", footnote_block.source_text)
        self.assertEqual(footnote_block.source_span_json["footnote_segment_count"], 2)
        self.assertEqual(footnote_block.source_span_json["footnote_segment_roles"], ["footnote", "body"])
        self.assertEqual(
            footnote_block.source_span_json["footnote_relocation_modes"],
            ["same_page_body_segment"],
        )
        self.assertIn("footnote_multisegment_repaired", footnote_block.source_span_json["recovery_flags"])
        page_evidence = result.document.metadata_json["pdf_page_evidence"]["pdf_pages"][0]
        self.assertEqual(page_evidence["relocated_footnote_count"], 1)
        self.assertEqual(page_evidence["max_footnote_segment_count"], 2)

    def test_bootstrap_pipeline_relocates_cross_page_multiparagraph_footnote(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "cross-page-multiparagraph-footnote.pdf"
            _write_cross_page_multiparagraph_footnote_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        footnote_blocks = [block for block in result.blocks if block.block_type == BlockType.FOOTNOTE]
        self.assertEqual(len(footnote_blocks), 1)
        footnote_block = footnote_blocks[0]
        self.assertIn("Second paragraph starts on the next page with more detail.", footnote_block.source_text)
        self.assertIn("Third paragraph continues the explanation before body text resumes.", footnote_block.source_text)
        self.assertEqual(footnote_block.source_span_json["source_page_start"], 1)
        self.assertEqual(footnote_block.source_span_json["source_page_end"], 2)
        self.assertEqual(footnote_block.source_span_json["footnote_segment_count"], 3)
        self.assertEqual(
            footnote_block.source_span_json["footnote_relocation_modes"],
            ["cross_page_body_segment"],
        )
        self.assertIn("footnote_multisegment_repaired", footnote_block.source_span_json["recovery_flags"])
        pages = result.document.metadata_json["pdf_page_evidence"]["pdf_pages"]
        self.assertEqual([page["relocated_footnote_count"] for page in pages], [1, 1])
        self.assertEqual([page["max_footnote_segment_count"] for page in pages], [3, 3])

    def test_bootstrap_pipeline_classifies_page_families_and_splits_special_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "section-families.pdf"
            _write_section_family_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Preface", "Chapter 1 Durable Products", "Appendix A Metrics", "References", "Index"],
        )
        self.assertEqual(
            [chapter.metadata_json["source_page_start"] for chapter in result.chapters],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["frontmatter", "body", "appendix", "references", "index"],
        )
        appendix_block = next(block for block in result.blocks if block.source_text.startswith("Appendix A Metrics"))
        references_block = next(block for block in result.blocks if block.source_text.startswith("References"))
        index_block = next(block for block in result.blocks if block.source_text.startswith("Index"))
        self.assertEqual(appendix_block.source_span_json["pdf_page_family"], "appendix")
        self.assertEqual(references_block.source_span_json["pdf_page_family"], "references")
        self.assertEqual(index_block.source_span_json["pdf_page_family"], "index")

    def test_outline_recovery_keeps_appendix_outside_outline_body_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "outline-appendix.pdf"
            _write_outline_with_appendix_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual([chapter.title_src for chapter in result.chapters], ["Strategic Moats", "Appendix A Metrics"])
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "appendix"],
        )

    def test_toc_recovery_reconciles_printed_page_numbers_with_pdf_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "toc-offset.pdf"
            _write_toc_offset_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Preface", "Strategic Moats", "Network Effects"],
        )
        self.assertEqual(
            [chapter.metadata_json["source_page_start"] for chapter in result.chapters],
            [2, 3, 4],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["frontmatter", "body", "body"],
        )

    def test_bootstrap_pipeline_detects_headingless_references_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "headingless-references.pdf"
            _write_headingless_references_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual([chapter.title_src for chapter in result.chapters], ["Chapter 1 Durable Products", "References"])
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "references"],
        )
        references_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 2
        ]
        self.assertTrue(references_blocks)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "references" for block in references_blocks))

    def test_bootstrap_pipeline_detects_headingless_index_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "headingless-index.pdf"
            _write_headingless_index_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual([chapter.title_src for chapter in result.chapters], ["Chapter 1 Durable Products", "Index"])
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "index"],
        )
        index_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 2
        ]
        self.assertTrue(index_blocks)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "index" for block in index_blocks))

    def test_bootstrap_pipeline_propagates_appendix_family_to_headingless_continuation_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "appendix-continuation.pdf"
            _write_appendix_continuation_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Chapter 1 Durable Products", "Appendix A Metrics", "Chapter 2 Network Effects"],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "appendix", "body"],
        )
        appendix_page_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 3
        ]
        self.assertTrue(appendix_page_blocks)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "appendix" for block in appendix_page_blocks))

    def test_bootstrap_pipeline_detects_appendix_intro_after_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "appendix-intro-after-references.pdf"
            _write_appendix_intro_after_references_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Chapter 1 Durable Products", "References", "Appendix Tool Catalog"],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "references", "appendix"],
        )
        appendix_page_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") in {3, 4}
        ]
        self.assertTrue(appendix_page_blocks)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "appendix" for block in appendix_page_blocks))

    def test_bootstrap_pipeline_splits_lettered_appendix_intro_subsections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "lettered-appendix-subsections.pdf"
            _write_lettered_appendix_subsections_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Chapter 1 Durable Products", "References", "Appendix A Tool Catalog", "Appendix K Prompt Templates"],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "references", "appendix", "appendix"],
        )
        self.assertEqual(
            [chapter.metadata_json["source_page_start"] for chapter in result.chapters],
            [1, 2, 3, 5],
        )
        appendix_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") in {3, 5}
        ]
        self.assertTrue(appendix_blocks)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "appendix" for block in appendix_blocks))

    def test_infer_appendix_intro_title_ignores_late_body_cue(self) -> None:
        text = (
            "307 B.4 Installing VS Code Python extensions 1 Launch VS Code and open the Extensions panel. "
            "Install the following list of extensions. Prompt Flow, for testing LLM prompts. "
            "Semantic Kernel Tools, for working with the Semantic Kernel framework. "
            "Docker, for managing Docker containers. Dev Containers, for running development environments "
            "with containers. You'll only need to install the extensions for each VS Code environment "
            "you're running. However, if you run VS Code in containers, you must install extensions for "
            "each container you're running. Working with Python in the Dev Containers extension will be "
            "covered later in this appendix."
        )

        self.assertIsNone(_infer_appendix_intro_title(text))

    def test_infer_appendix_subheading_title_from_top_level_label(self) -> None:
        text = "K.4 Specialized Standalone Templates These templates are loaded directly by their subsystems."

        self.assertEqual(
            _infer_appendix_subheading_title(text),
            "Appendix K.4 Specialized Standalone Templates",
        )

    def test_infer_appendix_subheading_title_ignores_nested_label(self) -> None:
        text = "K.2.5 Context Awareness (Priority 85-95)"

        self.assertIsNone(_infer_appendix_subheading_title(text))

    def test_infer_appendix_nested_subheading_title_from_nested_label(self) -> None:
        text = "K.2.5 Context Awareness (Priority 85-95)"

        self.assertEqual(
            _infer_appendix_nested_subheading_title(text),
            "Appendix K.2.5 Context Awareness",
        )

    def test_detect_backmatter_cue_from_heading_title(self) -> None:
        cue = _detect_backmatter_cue(
            [
                "Upcoming Titles",
                "Multi-Agent Systems with AutoGen",
                "ISBN 9781633436145 325 pages $59.99",
            ]
        )

        self.assertEqual(cue, ("Upcoming Titles", "heading_title"))

    def test_detect_backmatter_cue_from_marketing_signals(self) -> None:
        cue = _detect_backmatter_cue(
            [
                "Multi-Agent Systems with AutoGen",
                "Manning Books",
                "ISBN 9781633436145 325 pages $59.99",
                "Visit manning.com for source code downloads",
            ]
        )

        self.assertEqual(cue, ("Multi-Agent Systems with AutoGen", "marketing_signals"))

    def test_infer_intro_page_title_cleans_pdf_escape_and_sentence_tail_noise(self) -> None:
        title = _infer_intro_page_title(
            [
                "Words\\222 awakening:",
                "Why large language models have captured attention Any suf ficiently a d",
            ]
        )

        self.assertEqual(
            title,
            "Words awakening: Why large language models have captured attention",
        )

    def test_infer_intro_page_title_cleans_spaced_word_artifacts(self) -> None:
        title = _infer_intro_page_title(
            [
                "Data engineering f o r large language models:",
                "Setting up for success",
            ]
        )

        self.assertEqual(
            title,
            "Data engineering for large language models: Setting up for success",
        )

    def test_infer_intro_page_title_trims_embedded_sentence_restart(self) -> None:
        title = _infer_intro_page_title(
            [
                "Large language model operations:",
                "Building a platform for LLMs Before anything els e ,",
            ]
        )

        self.assertEqual(
            title,
            "Large language model operations: Building a platform for LLMs",
        )

    def test_infer_intro_page_title_keeps_article_before_deep_dive(self) -> None:
        title = _infer_intro_page_title(
            [
                "Large language models: A deep dive into",
                "language modeling I f y o u k n o w t h e e n e m y a n d k n o w y o u r s e l f ,",
            ]
        )

        self.assertEqual(
            title,
            "Large language models: A deep dive into language modeling",
        )

    def test_infer_intro_page_title_keeps_article_before_deep_dive_in_single_block(self) -> None:
        title = _infer_intro_page_title(
            [
                "Large language models: A deep dive into language modeling "
                "I f y o u k n o w t h e e n e m y a n d k n o w y o u r s e l f ,",
            ]
        )

        self.assertEqual(
            title,
            "Large language models: A deep dive into language modeling",
        )

    def test_infer_intro_page_title_trims_spaced_epigraph_restart(self) -> None:
        title = _infer_intro_page_title(
            [
                "Data engineering f o r large language models:",
                "Setting up for success D a t a i s l i k e g a r b a g e . Y o u \\222 d b e t t e r k n o w",
            ]
        )

        self.assertEqual(
            title,
            "Data engineering for large language models: Setting up for success",
        )

    def test_infer_intro_page_title_preserves_question_title_spacing(self) -> None:
        title = _infer_intro_page_title(
            [
                "Deploying an LLM on a Raspberry Pi:",
                "How low can you go? The bitternes s of p o or quality rem ains",
            ]
        )

        self.assertEqual(
            title,
            "Deploying an LLM on a Raspberry Pi: How low can you go?",
        )

    def test_bootstrap_pipeline_splits_appendix_top_level_subheading_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "appendix-top-level-subheading.pdf"
            _write_appendix_top_level_subheading_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            [
                "Chapter 1 Durable Products",
                "References",
                "Appendix K Prompt Templates",
                "Appendix K.4 Specialized Standalone Templates",
            ],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "references", "appendix", "appendix"],
        )
        self.assertEqual(
            [chapter.metadata_json["source_page_start"] for chapter in result.chapters],
            [1, 2, 3, 5],
        )

    def test_bootstrap_pipeline_keeps_nested_appendix_subheading_as_page_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "appendix-nested-subheading.pdf"
            _write_appendix_nested_subheading_signal_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)
            parsed = PDFParser().parse(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Chapter 1 Durable Products", "Appendix K Prompt Templates"],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "appendix"],
        )
        nested_page = next(
            page for page in parsed.metadata["pdf_page_evidence"]["pdf_pages"] if page["page_number"] == 4
        )
        self.assertEqual(
            nested_page["appendix_nested_subheadings"],
            [
                {
                    "label": "K.2.5",
                    "depth": 2,
                    "title": "Context Awareness",
                    "full_title": "Appendix K.2.5 Context Awareness",
                }
            ],
        )

    def test_bootstrap_pipeline_avoids_false_positive_references_and_index_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "false-positive-guard.pdf"
            _write_false_positive_guard_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual([chapter.title_src for chapter in result.chapters], ["Paper Title", "References"])
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "references"],
        )
        page_one_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 1
        ]
        page_two_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 2
        ]
        self.assertTrue(page_one_blocks)
        self.assertTrue(page_two_blocks)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "body" for block in page_one_blocks))
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "body" for block in page_two_blocks))

    def test_bootstrap_pipeline_does_not_start_special_section_from_isolated_index_like_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "isolated-index-guard.pdf"
            _write_isolated_index_false_start_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Chapter 1 Durable Products", "Chapter 2 Network Effects"],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "body"],
        )
        page_two_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 2
        ]
        self.assertTrue(page_two_blocks)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "body" for block in page_two_blocks))
        self.assertTrue(all(block.source_span_json.get("pdf_page_content_family") == "index" for block in page_two_blocks))

    def test_bootstrap_pipeline_ignores_sparse_index_signature_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "sparse-index-signature.pdf"
            _write_sparse_index_signature_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual([chapter.title_src for chapter in result.chapters], ["Chapter 1 Durable Products"])
        noisy_page_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 2
        ]
        self.assertTrue(noisy_page_blocks)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "body" for block in noisy_page_blocks))
        self.assertTrue(
            all(block.source_span_json.get("pdf_page_content_family") in {None, "body"} for block in noisy_page_blocks)
        )

    def test_bootstrap_pipeline_detects_inline_index_heading_and_splits_back_matter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "inline-index-heading.pdf"
            _write_inline_index_heading_backmatter_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Chapter 1 Durable Products", "Appendix A Metrics", "Index", "Back Matter"],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "appendix", "index", "backmatter"],
        )
        index_page_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 3
        ]
        back_matter_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 4
        ]
        back_matter_sentences = [
            sentence
            for sentence in result.sentences
            if sentence.block_id
            in {
                block.id
                for block in back_matter_blocks
                if block.source_span_json.get("pdf_block_role") == "body"
            }
        ]
        self.assertTrue(index_page_blocks)
        self.assertTrue(back_matter_blocks)
        self.assertTrue(back_matter_sentences)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "index" for block in index_page_blocks))
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "backmatter" for block in back_matter_blocks))
        self.assertTrue(all(not sentence.translatable for sentence in back_matter_sentences))
        self.assertTrue(
            all(sentence.nontranslatable_reason == "pdf_backmatter" for sentence in back_matter_sentences)
        )

    def test_bootstrap_pipeline_promotes_appendix_tail_page_with_backmatter_cue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "appendix-backmatter-cue.pdf"
            _write_appendix_backmatter_cue_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Chapter 1 Durable Products", "Appendix A Metrics", "Back Matter"],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "appendix", "backmatter"],
        )
        cue_page_blocks = [
            block for block in result.blocks if block.source_span_json.get("source_page_start") == 4
        ]
        cue_page_body_blocks = [
            block for block in cue_page_blocks if block.source_span_json.get("pdf_block_role") == "body"
        ]
        self.assertTrue(cue_page_blocks)
        self.assertTrue(cue_page_body_blocks)
        self.assertTrue(all(block.source_span_json["pdf_page_family"] == "backmatter" for block in cue_page_blocks))
        self.assertTrue(
            all(block.source_span_json["pdf_page_family_source"] == "backmatter_cue" for block in cue_page_blocks)
        )
        self.assertTrue(
            all(block.source_span_json["pdf_page_backmatter_cue"] == "Upcoming Titles" for block in cue_page_blocks)
        )
        self.assertTrue(all(not sentence.translatable for sentence in result.sentences if sentence.block_id in {
            block.id for block in cue_page_body_blocks
        }))

    def test_bootstrap_pipeline_splits_inline_multi_appendix_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "inline-multi-appendix.pdf"
            _write_inline_multi_appendix_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Chapter 1 Durable Products", "Appendix A Metrics", "Appendix B Tooling"],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["body", "appendix", "appendix"],
        )
        self.assertEqual(
            [chapter.metadata_json["source_page_start"] for chapter in result.chapters],
            [1, 2, 4],
        )

    def test_bootstrap_pipeline_recovers_chapters_from_intro_pages_without_font_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "chapter-intro-recovery.pdf"
            _write_chapter_intro_recovery_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Introduction to agents and their world", "Harnessing the power of large language models"],
        )
        self.assertEqual(
            [chapter.metadata_json["source_page_start"] for chapter in result.chapters],
            [1, 3],
        )
        self.assertTrue(all(chapter.metadata_json["pdf_section_family"] == "body" for chapter in result.chapters))

    def test_bootstrap_pipeline_recovers_chapters_when_intro_cue_is_embedded_in_body_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "embedded-chapter-intro-recovery.pdf"
            _write_embedded_chapter_intro_recovery_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Exploring multi-agent systems", "Agent reasoning and evaluation"],
        )
        self.assertEqual(
            [chapter.metadata_json["source_page_start"] for chapter in result.chapters],
            [1, 3],
        )

    def test_bootstrap_pipeline_labels_frontmatter_before_first_intro_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "frontmatter-intro-recovery.pdf"
            _write_frontmatter_intro_recovery_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        self.assertEqual(
            [chapter.title_src for chapter in result.chapters],
            ["Front Matter", "Introduction to agents and their world"],
        )
        self.assertEqual(
            [chapter.metadata_json["pdf_section_family"] for chapter in result.chapters],
            ["frontmatter", "body"],
        )
        self.assertEqual(
            [chapter.metadata_json["source_page_start"] for chapter in result.chapters],
            [1, 3],
        )


class BasicPdfOutlineRecoveryTests(unittest.TestCase):
    def test_helper_recognizes_numbered_book_heading_with_embedded_body(self) -> None:
        result = _leading_numbered_book_heading_and_remainder(
            "2.2.2 Controlling vocabulary size in tokenization GPT-NeoX, a publicly available LLM, takes about 10 GB to store its vocabulary on disk."
        )

        self.assertEqual(
            result,
            (
                "2.2.2 Controlling vocabulary size in tokenization",
                "GPT-NeoX, a publicly available LLM, takes about 10 GB to store its vocabulary on disk.",
                3,
            ),
        )

    def test_helper_recognizes_all_caps_book_subheading_with_embedded_body(self) -> None:
        result = _leading_all_caps_book_heading_and_remainder(
            "IDENTIFYING SUBWORDS WITH BYTE-PAIR ENCODING The general theme of LLMs is to do less feature engineering by hand."
        )

        self.assertEqual(
            result,
            (
                "IDENTIFYING SUBWORDS WITH BYTE-PAIR ENCODING",
                "The general theme of LLMs is to do less feature engineering by hand.",
                4,
            ),
        )

    def test_helper_recognizes_figure_caption_without_space_after_fig_prefix(self) -> None:
        self.assertTrue(
            _looks_like_figure_caption(
                "Fig.1: Agentic AI functions as an intelligent assistant, continuously learning through experience."
            )
        )

    def test_helper_recognizes_references_and_notes_heading_with_numeric_entries(self) -> None:
        result = _leading_reference_heading_and_remainder(
            "References and Notes\n1. M. E. Raichle et al., Proc. Natl. Acad. Sci. U.S.A. 98, 676 (2001)."
        )

        self.assertEqual(
            result,
            (
                "References and Notes",
                "1. M. E. Raichle etal., Proc. Natl. Acad. Sci. U.S.A. 98, 676 (2001)",
            ),
        )

    def test_helper_allows_question_style_visual_heading(self) -> None:
        self.assertTrue(_looks_like_visual_heading("What makes an AI system an Agent?", 1))

    def test_recovery_splits_embedded_numbered_heading_from_code_like_block(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="code_like",
            block_type=BlockType.CODE,
            text=(
                "2.2.3 Tokenization in detail The normalization and segmentation steps in the "
                "tokenization process largely determine the vocabulary size."
            ),
            page_start=42,
            page_end=42,
            bbox_regions=[{"page_number": 42, "bbox": [72.0, 120.0, 540.0, 220.0]}],
            reading_order_index=1,
            parse_confidence=0.92,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "code_like"},
            font_size_avg=12.0,
            source_path="pdf://page/42",
            anchor="p42-b254",
        )

        repaired = service._split_embedded_page_heading_segments(
            block,
            is_first_substantive_page_block=True,
            page_has_heading=False,
        )

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].role, "heading")
        self.assertEqual(repaired[0].block_type, BlockType.HEADING)
        self.assertEqual(repaired[0].metadata["heading_level"], 3)
        self.assertEqual(repaired[0].text, "2.2.3 Tokenization in detail")
        self.assertEqual(repaired[1].role, "body")
        self.assertEqual(repaired[1].block_type, BlockType.PARAGRAPH)
        self.assertTrue(repaired[1].text.startswith("The normalization and segmentation steps"))

    def test_recovery_prefers_academic_heading_split_for_embedded_numbered_section(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text=(
                "3.2 Attention An attention function can be described as mapping a query "
                "and a set of key-value pairs to an output."
            ),
            page_start=3,
            page_end=3,
            bbox_regions=[{"page_number": 3, "bbox": [108.0, 601.0, 504.0, 640.0]}],
            reading_order_index=1,
            parse_confidence=0.92,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            font_size_avg=11.0,
            source_path="pdf://page/3",
            anchor="p3-b33",
        )

        repaired = service._split_embedded_page_heading_segments(
            block,
            is_first_substantive_page_block=False,
            page_has_heading=True,
        )

        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].role, "heading")
        self.assertEqual(repaired[0].text, "3.2 Attention")
        self.assertEqual(repaired[0].metadata["heading_level"], 3)
        self.assertEqual(repaired[1].role, "body")
        self.assertTrue(repaired[1].text.startswith("An attention function can be described"))

    def test_recovery_splits_embedded_first_page_abstract_heading_from_academic_frontmatter(self) -> None:
        service = PdfStructureRecoveryService()
        block = _RecoveredBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text=(
                "Shunyu Yao, Jeffrey Zhao Department of Computer Science, Princeton University "
                "Google Research, Brain Team arXiv:2210.03629v3 [cs.CL] 10 Mar 2023 ABSTRACT "
                "While large language models have demonstrated impressive performance across tasks, "
                "their reasoning and acting abilities have primarily been studied as separate topics."
            ),
            page_start=1,
            page_end=1,
            bbox_regions=[{"page_number": 1, "bbox": [108.0, 140.0, 504.0, 483.0]}],
            reading_order_index=1,
            parse_confidence=0.9,
            flags=[],
            metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            font_size_avg=10.0,
            source_path="pdf://page/1",
            anchor="p1-b3",
        )

        repaired = service._split_embedded_page_heading_segments(
            block,
            is_first_substantive_page_block=False,
            page_has_heading=True,
        )

        self.assertEqual(len(repaired), 3)
        self.assertEqual(repaired[0].role, "body")
        self.assertIn("Princeton University", repaired[0].text)
        self.assertEqual(repaired[1].role, "heading")
        self.assertEqual(repaired[1].text, "Abstract")
        self.assertEqual(repaired[1].metadata["heading_level"], 2)
        self.assertEqual(repaired[2].role, "body")
        self.assertTrue(repaired[2].text.startswith("While large language models"))

    def test_recovery_populates_default_level_for_generic_body_heading(self) -> None:
        service = PdfStructureRecoveryService()
        heading = _RecoveredBlock(
            role="heading",
            block_type=BlockType.HEADING,
            text="Prompt Chaining Pattern Overview",
            page_start=21,
            page_end=21,
            bbox_regions=[{"page_number": 21, "bbox": [72.0, 120.0, 540.0, 170.0]}],
            reading_order_index=1,
            parse_confidence=0.95,
            flags=[],
            metadata={"pdf_page_family": "body"},
            font_size_avg=14.0,
            source_path="pdf://page/21",
            anchor="p21-b139",
        )

        normalized = service._populate_missing_heading_levels([heading])

        self.assertEqual(normalized[0].metadata["heading_level"], 2)

    def test_helper_recovers_academic_top_level_numbered_heading_and_level(self) -> None:
        result = _next_academic_inline_heading(
            "2 Background The goal of reducing sequential computation also forms the foundation of the Extended Neural GPU."
        )

        self.assertEqual(
            result,
            (
                0,
                "2 Background",
                "The goal of reducing sequential computation also forms the foundation of the Extended Neural GPU.",
                {"heading_kind": "numbered", "section_level": 2},
            ),
        )

    def test_helper_stops_academic_numbered_heading_before_sentence_like_tail(self) -> None:
        result = _next_academic_inline_heading(
            "3.2 Attention An attention function can be described as mapping a query and a set of key-value pairs to an output."
        )

        self.assertEqual(
            result,
            (
                0,
                "3.2 Attention",
                "An attention function can be described as mapping a query and a set of key-value pairs to an output.",
                {"heading_kind": "numbered", "section_level": 3},
            ),
        )

    def test_helper_recovers_embedded_academic_abstract_segments(self) -> None:
        result = _embedded_academic_abstract_segments(
            "Author Name Department of Computer Science, Princeton University arXiv:2210.03629v3 [cs.CL] ABSTRACT "
            "While large language models have demonstrated impressive performance across tasks, their abilities remain uneven."
        )

        self.assertEqual(
            result,
            (
                "Author Name Department of Computer Science, Princeton University arXiv:2210.03629v3 [cs.CL]",
                "Abstract",
                "While large language models have demonstrated impressive performance across tasks, their abilities remain uneven",
            ),
        )

    def test_helper_assigns_deeper_level_to_lettered_appendix_subheading(self) -> None:
        self.assertEqual(_book_heading_level("E.1 SUCCESS AND FAILURE MODES ANALYSIS"), 3)

    def test_helper_detects_function_style_equation_line(self) -> None:
        self.assertTrue(
            _looks_like_equation(
                "PE(pos,2i) = sin(pos/10000^(2i/d_model))",
                1,
                (235.509, 287.551, 386.307, 301.089),
                612.0,
            )
        )

    def test_helper_rejects_prose_column_as_numeric_table_fragment(self) -> None:
        lines = [
            "nlike other animals, human beings spend",
            "a lot of time thinking about what is not",
            "going on around them, contemplating",
            "events that happened in the past, might happen",
            "in the future, or will never happen at all. Indeed,",
            "42.5% of samples were pleasant and 26.5% were unpleasant.",
        ]

        self.assertFalse(_looks_like_numeric_table_fragment(lines))

    def test_recovery_classifies_numeric_table_fragment_as_table(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Academic Table Sample",
            author="Test Author",
            metadata={"pdf_extractor": "pymupdf"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Table 1: Maximum path lengths and complexity.",
                            bbox=(108.0, 96.0, 504.0, 132.0),
                            line_texts=["Table 1: Maximum path lengths and complexity."],
                            span_count=40,
                            line_count=1,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text=(
                                "Layer Type\n"
                                "Complexity per Layer\n"
                                "Sequential Operations\n"
                                "Self-Attention\n"
                                "O(n2 · d)\n"
                                "O(1)\n"
                                "Recurrent\n"
                                "O(n · d2)\n"
                                "O(n)\n"
                            ),
                            bbox=(108.0, 150.0, 420.0, 360.0),
                            line_texts=[
                                "Layer Type",
                                "Complexity per Layer",
                                "Sequential Operations",
                                "Self-Attention",
                                "O(n2 · d)",
                                "O(1)",
                                "Recurrent",
                                "O(n · d2)",
                                "O(n)",
                            ],
                            span_count=120,
                            line_count=9,
                            font_size_min=9.0,
                            font_size_max=9.0,
                            font_size_avg=9.0,
                        ),
                    ],
                )
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=1,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="medium",
            ocr_required=False,
            extractor_kind="pymupdf",
            recovery_lane="academic_paper",
            trailing_reference_page_count=1,
            academic_paper_candidate=True,
        )

        parsed = recovery_service.recover("academic-table-sample.pdf", extraction, profile)

        table_blocks = [
            block
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.TABLE.value
        ]
        self.assertTrue(table_blocks)
        self.assertIn("Layer Type", table_blocks[0].text)

    def test_basic_extractor_reads_outline_entries_and_parser_uses_bookmarks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "outline.pdf"
            _write_outline_pdf(pdf_path)

            extractor = BasicPdfTextExtractor()
            extraction = extractor.extract(pdf_path)
            parser = PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor))
            parsed = parser.parse(pdf_path)

        self.assertEqual(
            [(entry.level, entry.title, entry.page_number) for entry in extraction.outline_entries],
            [(1, "Strategic Moats", 2), (1, "Network Effects", 3)],
        )
        self.assertEqual([chapter.title for chapter in parsed.chapters], ["Strategic Moats", "Network Effects"])
        self.assertEqual([chapter.metadata["source_page_start"] for chapter in parsed.chapters], [2, 3])

    def test_parser_emits_pdf_page_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "sample.pdf"
            _write_low_risk_text_pdf(pdf_path)

            extractor = BasicPdfTextExtractor()
            parsed = PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor)).parse(pdf_path)

        evidence = parsed.metadata["pdf_page_evidence"]
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["extractor_kind"], "basic")
        self.assertEqual(evidence["page_count"], 2)
        self.assertEqual(len(evidence["pdf_pages"]), 2)
        first_page = evidence["pdf_pages"][0]
        second_page = evidence["pdf_pages"][1]
        self.assertEqual(first_page["page_number"], 1)
        self.assertEqual(first_page["page_family"], "body")
        self.assertEqual(first_page["layout_signals"], [])
        self.assertEqual(first_page["page_layout_risk"], "low")
        self.assertEqual(first_page["page_layout_reasons"], [])
        self.assertEqual(first_page["role_counts"]["heading"], 1)
        self.assertIn("cross_page_repaired", second_page["recovery_flags"])
        self.assertIn("dehyphenated", second_page["recovery_flags"])

    def test_recovery_preserves_pdf_image_blocks_as_image_blocks(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Image Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Chapter 1 Vision Systems",
                            bbox=(72.0, 96.0, 360.0, 124.0),
                            line_texts=["Chapter 1 Vision Systems"],
                            span_count=24,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=18.0,
                            font_size_avg=18.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="Models improve when diagrams and screenshots remain visible to reviewers.",
                            bbox=(72.0, 148.0, 540.0, 188.0),
                            line_texts=["Models improve when diagrams and screenshots remain visible to reviewers."],
                            span_count=72,
                            line_count=2,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                    ],
                    image_blocks=[
                        PdfImageBlock(
                            page_number=1,
                            block_number=3,
                            bbox=(72.0, 240.0, 420.0, 520.0),
                            width_px=1024,
                            height_px=768,
                            image_ext="png",
                        )
                    ],
                )
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=1,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = recovery_service.recover("image-sample.pdf", extraction, profile)

        image_blocks = [
            block
            for block in parsed.chapters[0].blocks
            if block.block_type == BlockType.IMAGE.value
        ]
        self.assertEqual(len(image_blocks), 1)
        self.assertEqual(image_blocks[0].text, "[Image]")
        self.assertEqual(image_blocks[0].metadata["pdf_block_role"], "image")
        self.assertEqual(image_blocks[0].metadata["image_ext"], "png")
        self.assertEqual(image_blocks[0].metadata["image_width_px"], 1024)
        self.assertEqual(image_blocks[0].metadata["image_height_px"], 768)
        page_evidence = parsed.metadata["pdf_page_evidence"]["pdf_pages"][0]
        self.assertEqual(page_evidence["raw_image_block_count"], 1)
        self.assertEqual(page_evidence["role_counts"]["image"], 1)

    def test_recovery_keeps_book_inline_heading_image_legend_and_cross_page_bullet_structure(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Tokenization Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Introduction 11",
                            bbox=(108.0, 55.0, 504.0, 69.0),
                            line_texts=["Introduction 11"],
                            span_count=8,
                            line_count=1,
                            font_size_min=9.0,
                            font_size_max=9.0,
                            font_size_avg=9.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="Tokenization: Breaking Text into Pieces",
                            bbox=(108.0, 406.0, 336.0, 420.0),
                            line_texts=["Tokenization: Breaking Text into Pieces"],
                            span_count=12,
                            line_count=1,
                            font_size_min=13.1,
                            font_size_max=13.1,
                            font_size_avg=13.1,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=3,
                            text="Before a large language model can process text, that text needs to be broken",
                            bbox=(108.0, 441.0, 504.0, 455.0),
                            line_texts=["Before a large language model can process text, that text needs to be broken"],
                            span_count=24,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=4,
                            text="down into smaller units called tokens. Tokens can be individual words, parts of",
                            bbox=(108.0, 461.0, 504.0, 475.0),
                            line_texts=["down into smaller units called tokens. Tokens can be individual words, parts of"],
                            span_count=24,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=5,
                            text="words, or even single characters. The process of splitting text into tokens is",
                            bbox=(108.0, 481.0, 504.0, 495.0),
                            line_texts=["words, or even single characters. The process of splitting text into tokens is"],
                            span_count=24,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=6,
                            text="known as tokenization, and it’s a crucial step in preparing data for a language",
                            bbox=(108.0, 502.0, 504.0, 516.0),
                            line_texts=["known as tokenization, and it’s a crucial step in preparing data for a language"],
                            span_count=24,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=7,
                            text="model.",
                            bbox=(108.0, 522.0, 142.0, 536.0),
                            line_texts=["model."],
                            span_count=4,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=8,
                            text="This sentence contains 27 tokens",
                            bbox=(237.0, 593.0, 375.0, 605.0),
                            line_texts=["This sentence contains 27 tokens"],
                            span_count=8,
                            line_count=1,
                            font_size_min=8.7,
                            font_size_max=8.7,
                            font_size_avg=8.7,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=9,
                            text="Different LLMs use different tokenization strategies, which can have a significant",
                            bbox=(108.0, 621.0, 506.0, 635.0),
                            line_texts=["Different LLMs use different tokenization strategies, which can have a significant"],
                            span_count=22,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=10,
                            text="impact on the model’s performance and capabilities. Some common tokenizers used by LLMs include:",
                            bbox=(108.0, 641.0, 504.0, 675.0),
                            line_texts=[
                                "impact on the model’s performance and capabilities. Some common",
                                "tokenizers used by LLMs include:",
                            ],
                            span_count=26,
                            line_count=2,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=11,
                            text="• GPT (Byte Pair Encoding): GPT tokenizers use a technique called byte pair",
                            bbox=(123.0, 696.0, 504.0, 710.0),
                            line_texts=["• GPT (Byte Pair Encoding): GPT tokenizers use a technique called byte pair"],
                            span_count=20,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                    ],
                    image_blocks=[
                        PdfImageBlock(
                            page_number=1,
                            block_number=12,
                            bbox=(108.0, 545.0, 504.0, 589.0),
                            width_px=717,
                            height_px=80,
                            image_ext="png",
                        )
                    ],
                ),
                PdfPage(
                    page_number=2,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=2,
                            block_number=1,
                            text="Introduction 12",
                            bbox=(108.0, 55.0, 504.0, 69.0),
                            line_texts=["Introduction 12"],
                            span_count=8,
                            line_count=1,
                            font_size_min=9.0,
                            font_size_max=9.0,
                            font_size_avg=9.0,
                        ),
                        PdfTextBlock(
                            page_number=2,
                            block_number=2,
                            text="encoding (BPE) to break text into subword units. BPE iteratively merges the most frequent pairs of bytes in a text corpus.",
                            bbox=(133.0, 89.0, 504.0, 143.0),
                            line_texts=[
                                "encoding (BPE) to break text into subword units. BPE iteratively merges",
                                "the most frequent pairs of bytes in a text corpus.",
                            ],
                            span_count=24,
                            line_count=2,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                    ],
                    image_blocks=[],
                ),
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=2,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = recovery_service.recover("tokenization-structure-sample.pdf", extraction, profile)
        chapter_blocks = parsed.chapters[0].blocks

        heading_block = next(
            block
            for block in chapter_blocks
            if block.block_type == BlockType.HEADING.value
            and block.text == "Tokenization: Breaking Text into Pieces"
        )
        intro_body_block = next(
            block
            for block in chapter_blocks
            if block.block_type == BlockType.PARAGRAPH.value
            and block.text.startswith("Before a large language model can process text")
        )
        image_block = next(block for block in chapter_blocks if block.block_type == BlockType.IMAGE.value)
        caption_block = next(
            block
            for block in chapter_blocks
            if block.block_type == BlockType.CAPTION.value
            and block.text == "This sentence contains 27 tokens"
        )
        prose_block = next(
            block
            for block in chapter_blocks
            if block.block_type == BlockType.PARAGRAPH.value
            and block.text.startswith("Different LLMs use different tokenization strategies")
        )
        bullet_block = next(
            block
            for block in chapter_blocks
            if block.block_type in (BlockType.PARAGRAPH.value, BlockType.LIST_ITEM.value)
            and block.text.startswith("• GPT (Byte Pair Encoding):")
        )

        self.assertNotIn("Before a large language model", heading_block.text)
        self.assertFalse(intro_body_block.text.startswith("Tokenization:"))
        self.assertEqual(image_block.metadata["linked_caption_text"], "This sentence contains 27 tokens")
        self.assertTrue(caption_block.metadata["caption_for_source_anchor"].endswith(image_block.anchor))
        self.assertLess(image_block.ordinal, caption_block.ordinal)
        self.assertLess(caption_block.ordinal, prose_block.ordinal)
        self.assertIn("encoding (BPE) to break text into subword units.", bullet_block.text)
        self.assertEqual(bullet_block.metadata["source_page_start"], 1)
        self.assertEqual(bullet_block.metadata["source_page_end"], 2)

    def test_recovery_classifies_centered_equation_block_as_equation(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Equation Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Chapter 1 Differentiable Objectives",
                            bbox=(72.0, 72.0, 360.0, 94.0),
                            line_texts=["Chapter 1 Differentiable Objectives"],
                            span_count=1,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=18.0,
                            font_size_avg=18.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="We optimize the model using a normalized probabilistic objective.",
                            bbox=(72.0, 132.0, 540.0, 150.0),
                            line_texts=["We optimize the model using a normalized probabilistic objective."],
                            span_count=1,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=3,
                            text="p(y|x) = softmax(W_h h_t + b)",
                            bbox=(192.0, 228.0, 420.0, 244.0),
                            line_texts=["p(y|x) = softmax(W_h h_t + b)"],
                            span_count=1,
                            line_count=1,
                            font_size_min=12.0,
                            font_size_max=12.0,
                            font_size_avg=12.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=4,
                            text="The decoder then samples the next token from this distribution.",
                            bbox=(72.0, 290.0, 540.0, 308.0),
                            line_texts=["The decoder then samples the next token from this distribution."],
                            span_count=1,
                            line_count=1,
                            font_size_min=11.0,
                            font_size_max=11.0,
                            font_size_avg=11.0,
                        ),
                    ],
                    image_blocks=[],
                )
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=1,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = recovery_service.recover("equation-sample.pdf", extraction, profile)

        equation_blocks = [
            block
            for block in parsed.chapters[0].blocks
            if block.block_type == BlockType.EQUATION.value
        ]
        self.assertEqual(len(equation_blocks), 1)
        self.assertEqual(equation_blocks[0].text, "p(y|x) = softmax(W_h h_t + b)")
        self.assertEqual(equation_blocks[0].metadata["pdf_block_role"], "equation")
        page_evidence = parsed.metadata["pdf_page_evidence"]["pdf_pages"][0]
        self.assertEqual(page_evidence["role_counts"]["equation"], 1)

    def test_recovery_links_pdf_image_blocks_to_nearby_caption_blocks(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Image Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Chapter 1 Vision Systems",
                            bbox=(72.0, 96.0, 360.0, 124.0),
                            line_texts=["Chapter 1 Vision Systems"],
                            span_count=24,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=18.0,
                            font_size_avg=18.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="Figure 1. System overview diagram.",
                            bbox=(96.0, 532.0, 396.0, 556.0),
                            line_texts=["Figure 1. System overview diagram."],
                            span_count=18,
                            line_count=1,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                    ],
                    image_blocks=[
                        PdfImageBlock(
                            page_number=1,
                            block_number=3,
                            bbox=(72.0, 240.0, 420.0, 520.0),
                            width_px=1024,
                            height_px=768,
                            image_ext="png",
                        )
                    ],
                )
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=1,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = recovery_service.recover("image-sample.pdf", extraction, profile)

        image_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.IMAGE.value)
        caption_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.CAPTION.value)
        self.assertEqual(image_block.metadata["linked_caption_text"], "Figure 1. System overview diagram.")
        self.assertEqual(image_block.metadata["image_alt"], "Figure 1. System overview diagram.")
        self.assertTrue(image_block.metadata["linked_caption_source_anchor"].endswith(caption_block.anchor))
        self.assertIn("caption_linked", image_block.metadata["recovery_flags"])
        self.assertTrue(caption_block.metadata["caption_for_source_anchor"].endswith(image_block.anchor))
        self.assertEqual(caption_block.metadata["caption_for_role"], "image")
        self.assertIn("image_caption_linked", caption_block.metadata["recovery_flags"])

    def test_recovery_links_pdf_image_blocks_to_next_page_caption_blocks(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Cross Page Figure Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Chapter 1 Model Comparison",
                            bbox=(72.0, 96.0, 360.0, 124.0),
                            line_texts=["Chapter 1 Model Comparison"],
                            span_count=24,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=18.0,
                            font_size_avg=18.0,
                        ),
                    ],
                    image_blocks=[
                        PdfImageBlock(
                            page_number=1,
                            block_number=2,
                            bbox=(72.0, 220.0, 420.0, 690.0),
                            width_px=1024,
                            height_px=768,
                            image_ext="png",
                        )
                    ],
                ),
                PdfPage(
                    page_number=2,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=2,
                            block_number=1,
                            text="Figure 1. System overview diagram.",
                            bbox=(96.0, 92.0, 396.0, 116.0),
                            line_texts=["Figure 1. System overview diagram."],
                            span_count=18,
                            line_count=1,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                    ],
                    image_blocks=[],
                ),
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=2,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = recovery_service.recover("cross-page-image-sample.pdf", extraction, profile)

        image_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.IMAGE.value)
        caption_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.CAPTION.value)
        self.assertEqual(image_block.metadata["linked_caption_text"], "Figure 1. System overview diagram.")
        self.assertTrue(image_block.metadata["linked_caption_source_anchor"].endswith(caption_block.anchor))
        self.assertTrue(caption_block.metadata["caption_for_source_anchor"].endswith(image_block.anchor))
        self.assertEqual(caption_block.metadata["caption_for_role"], "image")

    def test_pymupdf_extractor_clusters_vector_drawings_into_image_blocks(self) -> None:
        extractor = PyMuPDFTextExtractor()

        class DummyPage:
            rect = SimpleNamespace(width=612.0, height=792.0)

            def get_drawings(self) -> list[dict[str, tuple[float, float, float, float]]]:
                return [
                    {"rect": (120.0, 180.0, 280.0, 300.0)},
                    {"rect": (122.0, 318.0, 282.0, 430.0)},
                    {"rect": (60.0, 308.0, 540.0, 320.0)},
                ]

        image_blocks = extractor._extract_vector_drawing_blocks(DummyPage(), page_number=1, start_block_number=10)

        self.assertEqual(len(image_blocks), 1)
        self.assertEqual(image_blocks[0].image_type, "vector_drawing")
        self.assertEqual(image_blocks[0].block_number, 10)
        self.assertLessEqual(image_blocks[0].bbox[0], 80.0)
        self.assertGreaterEqual(image_blocks[0].bbox[2], 320.0)

    def test_recovery_links_pdf_table_blocks_to_nearby_caption_blocks(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Table Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Chapter 1 Model Comparison",
                            bbox=(72.0, 96.0, 360.0, 124.0),
                            line_texts=["Chapter 1 Model Comparison"],
                            span_count=24,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=18.0,
                            font_size_avg=18.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="Model  BLEU  Cost\nTransformer  27.5  1.0x10^19\nBaseline  25.1  8.0x10^18",
                            bbox=(84.0, 244.0, 420.0, 314.0),
                            line_texts=[
                                "Model  BLEU  Cost",
                                "Transformer  27.5  1.0x10^19",
                                "Baseline  25.1  8.0x10^18",
                            ],
                            span_count=42,
                            line_count=3,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=3,
                            text="Table 1. Translation quality and training cost.",
                            bbox=(96.0, 330.0, 420.0, 352.0),
                            line_texts=["Table 1. Translation quality and training cost."],
                            span_count=18,
                            line_count=1,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                    ],
                    image_blocks=[],
                )
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=1,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = recovery_service.recover("table-sample.pdf", extraction, profile)

        table_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.TABLE.value)
        caption_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.CAPTION.value)
        self.assertEqual(table_block.metadata["linked_caption_text"], "Table 1. Translation quality and training cost.")
        self.assertTrue(table_block.metadata["linked_caption_source_anchor"].endswith(caption_block.anchor))
        self.assertIn("caption_linked", table_block.metadata["recovery_flags"])
        self.assertTrue(caption_block.metadata["caption_for_source_anchor"].endswith(table_block.anchor))
        self.assertEqual(caption_block.metadata["caption_for_role"], "table")
        self.assertIn("table_caption_linked", caption_block.metadata["recovery_flags"])

    def test_recovery_links_pdf_table_blocks_to_adjacent_explanation_blocks(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Table Context Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Chapter 1 Model Comparison",
                            bbox=(72.0, 96.0, 360.0, 124.0),
                            line_texts=["Chapter 1 Model Comparison"],
                            span_count=24,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=18.0,
                            font_size_avg=18.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="Model  BLEU  Cost\nTransformer  27.5  1.0x10^19\nBaseline  25.1  8.0x10^18",
                            bbox=(84.0, 244.0, 420.0, 314.0),
                            line_texts=[
                                "Model  BLEU  Cost",
                                "Transformer  27.5  1.0x10^19",
                                "Baseline  25.1  8.0x10^18",
                            ],
                            span_count=42,
                            line_count=3,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=3,
                            text="Table 1. Translation quality and training cost.",
                            bbox=(96.0, 330.0, 420.0, 352.0),
                            line_texts=["Table 1. Translation quality and training cost."],
                            span_count=18,
                            line_count=1,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=4,
                            text="Table 1 reports the main translation results and highlights the compute gap between the stronger Transformer and the cheaper baseline.",
                            bbox=(96.0, 366.0, 504.0, 410.0),
                            line_texts=[
                                "Table 1 reports the main translation results and highlights the compute gap",
                                "between the stronger Transformer and the cheaper baseline.",
                            ],
                            span_count=36,
                            line_count=2,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                    ],
                    image_blocks=[],
                )
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=1,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = recovery_service.recover("table-context-sample.pdf", extraction, profile)

        table_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.TABLE.value)
        explanation_block = next(
            block
            for block in parsed.chapters[0].blocks
            if block.block_type == BlockType.PARAGRAPH.value
            and "Table 1 reports the main translation results" in block.text
        )
        self.assertEqual(
            table_block.metadata["artifact_group_context_source_anchors"],
            [f"pdf://page/1#{explanation_block.anchor}"],
        )
        self.assertIn("artifact_group_context_linked", table_block.metadata["recovery_flags"])
        self.assertEqual(
            explanation_block.metadata["artifact_group_source_anchor"],
            f"pdf://page/1#{table_block.anchor}",
        )
        self.assertEqual(explanation_block.metadata["artifact_group_role"], "table")
        self.assertIn("table_group_context_linked", explanation_block.metadata["recovery_flags"])

    def test_recovery_does_not_link_table_caption_to_image_block(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Mixed Caption Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Table 1. This caption belongs to the table below, not the figure above.",
                            bbox=(96.0, 532.0, 430.0, 556.0),
                            line_texts=["Table 1. This caption belongs to the table below, not the figure above."],
                            span_count=18,
                            line_count=1,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                    ],
                    image_blocks=[
                        PdfImageBlock(
                            page_number=1,
                            block_number=2,
                            bbox=(72.0, 240.0, 420.0, 520.0),
                            width_px=1024,
                            height_px=768,
                            image_ext="png",
                        )
                    ],
                )
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=1,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = recovery_service.recover("mixed-caption-sample.pdf", extraction, profile)

        image_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.IMAGE.value)
        caption_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.CAPTION.value)
        self.assertNotIn("linked_caption_text", image_block.metadata)
        self.assertNotIn("caption_linked", image_block.metadata.get("recovery_flags", []))
        self.assertNotIn("caption_for_source_anchor", caption_block.metadata)

    def test_recovery_links_equation_blocks_to_nearby_equation_captions(self) -> None:
        recovery_service = PdfStructureRecoveryService()
        extraction = PdfExtraction(
            title="Equation Caption Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Chapter 1 Optimization",
                            bbox=(72.0, 96.0, 360.0, 124.0),
                            line_texts=["Chapter 1 Optimization"],
                            span_count=24,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=18.0,
                            font_size_avg=18.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="p(y|x) = softmax(W_h h_t + b)",
                            bbox=(192.0, 228.0, 420.0, 244.0),
                            line_texts=["p(y|x) = softmax(W_h h_t + b)"],
                            span_count=1,
                            line_count=1,
                            font_size_min=12.0,
                            font_size_max=12.0,
                            font_size_avg=12.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=3,
                            text="Equation 1. Decoder token distribution.",
                            bbox=(168.0, 268.0, 444.0, 288.0),
                            line_texts=["Equation 1. Decoder token distribution."],
                            span_count=18,
                            line_count=1,
                            font_size_min=10.0,
                            font_size_max=10.0,
                            font_size_avg=10.0,
                        ),
                    ],
                    image_blocks=[],
                )
            ],
        )
        profile = PdfFileProfile(
            pdf_kind="text_pdf",
            page_count=1,
            has_extractable_text=True,
            outline_present=False,
            layout_risk="low",
            ocr_required=False,
            extractor_kind="basic",
        )

        parsed = recovery_service.recover("equation-caption-sample.pdf", extraction, profile)

        equation_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.EQUATION.value)
        caption_block = next(block for block in parsed.chapters[0].blocks if block.block_type == BlockType.CAPTION.value)
        self.assertEqual(equation_block.metadata["linked_caption_text"], "Equation 1. Decoder token distribution.")
        self.assertTrue(equation_block.metadata["linked_caption_source_anchor"].endswith(caption_block.anchor))
        self.assertIn("caption_linked", equation_block.metadata["recovery_flags"])
        self.assertTrue(caption_block.metadata["caption_for_source_anchor"].endswith(equation_block.anchor))
        self.assertEqual(caption_block.metadata["caption_for_role"], "equation")
        self.assertIn("equation_caption_linked", caption_block.metadata["recovery_flags"])

    def test_parse_service_emits_document_image_records_for_pdf_image_blocks(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "image_alt": "System overview diagram",
                                    },
                                    parse_confidence=0.97,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="11111111-1111-4111-8111-111111111111",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-image-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )

        artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")

        self.assertEqual(len(artifacts.document_images), 1)
        image = artifacts.document_images[0]
        self.assertEqual(image.document_id, document.id)
        self.assertEqual(image.block_id, artifacts.blocks[0].id)
        self.assertEqual(image.page_number, 1)
        self.assertEqual(image.image_type, "embedded_image")
        self.assertEqual(image.alt_text, "System overview diagram")
        self.assertEqual(image.width_px, 1024)
        self.assertEqual(image.height_px, 768)
        self.assertEqual(image.metadata_json["image_ext"], "png")

    def test_parse_service_materializes_image_caption_block_links(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "linked_caption_text": "Figure 1. System overview diagram.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Figure 1. System overview diagram.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "caption_for_source_anchor": "pdf://page/1#p1-img1",
                                    },
                                    parse_confidence=0.94,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="77777777-7777-4777-8777-777777777777",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-image-caption-link-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )

        artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")

        image_block = artifacts.blocks[0]
        caption_block = artifacts.blocks[1]
        self.assertEqual(image_block.source_span_json["linked_caption_block_id"], caption_block.id)
        self.assertEqual(caption_block.source_span_json["caption_for_block_id"], image_block.id)
        self.assertEqual(artifacts.document_images[0].alt_text, "Figure 1. System overview diagram.")
        self.assertEqual(artifacts.document_images[0].metadata_json["linked_caption_block_id"], caption_block.id)

    def test_ocr_pdf_text_extractor_groups_surya_lines_into_pdf_blocks(self) -> None:
        class _FakeRunner:
            def run(self, *, file_path, output_dir):
                results_path = Path(output_dir) / "results.json"
                results_path.write_text(
                    json.dumps(
                        {
                            Path(file_path).stem: [
                                {
                                    "image_bbox": [0, 0, 600, 800],
                                    "text_lines": [
                                        {
                                            "text": "<b>Chapter 1 Intelligent Systems</b>",
                                            "confidence": 0.98,
                                            "bbox": [150, 80, 450, 124],
                                        },
                                        {
                                            "text": "<b>Agentic</b> design patterns help teams structure complex AI workflows.",
                                            "confidence": 0.94,
                                            "bbox": [72, 180, 540, 202],
                                        },
                                        {
                                            "text": "They make planning, tool use, and memory more reliable in practice.",
                                            "confidence": 0.93,
                                            "bbox": [72, 206, 548, 228],
                                        },
                                    ],
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                return results_path

        extraction = OcrPdfTextExtractor(runner=_FakeRunner()).extract("scan-sample.pdf")

        self.assertEqual(extraction.metadata["pdf_extractor"], "surya_ocr")
        self.assertEqual(len(extraction.pages), 1)
        self.assertEqual(extraction.pages[0].width, 600.0)
        self.assertEqual(extraction.pages[0].height, 800.0)
        self.assertEqual(len(extraction.pages[0].blocks), 2)
        self.assertEqual(extraction.pages[0].blocks[0].text, "Chapter 1 Intelligent Systems")
        self.assertNotIn("<b>", extraction.pages[0].blocks[0].text)
        self.assertIn("Agentic design patterns help teams structure", extraction.pages[0].blocks[1].text)
        self.assertEqual(extraction.pages[0].blocks[1].line_count, 2)

    def test_uv_surya_ocr_runner_pins_transformers_for_runtime_compatibility(self) -> None:
        with patch("book_agent.domain.structure.ocr.shutil.which", side_effect=["/opt/homebrew/bin/uv", "/opt/homebrew/bin/python3.13"]):
            command = UvSuryaOcrRunner(page_range="0-31")._build_command(
                file_path="scan-sample.pdf",
                output_dir="/tmp/book-agent-ocr-smoke",
            )

        self.assertEqual(command[:4], ["uv", "run", "--python", "3.13"])
        self.assertIn("surya-ocr==0.17.1", command)
        self.assertIn("transformers==4.56.1", command)
        self.assertIn("--page_range", command)
        self.assertIn("0-31", command)
        self.assertIn("surya_ocr", command)
        self.assertEqual(command[-2:], ["--output_dir", str(Path("/tmp/book-agent-ocr-smoke").resolve())])

    def test_uv_surya_ocr_runner_writes_status_snapshots_during_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "ocr-output"
            output_dir.mkdir()
            status_path = Path(temp_dir) / "ocr-status.json"
            poll_results = [None, 0]

            class _FakeProcess:
                pid = 4321

                def poll(self):
                    result = poll_results.pop(0)
                    if result == 0:
                        (output_dir / "results.json").write_text(
                            json.dumps({"scan-sample": []}),
                            encoding="utf-8",
                        )
                    return result

            def _fake_popen(_command, stdout, stderr, text):
                self.assertTrue(text)
                stdout.write("warming up model weights\n")
                stdout.flush()
                stderr.write("loading OCR runtime\n")
                stderr.flush()
                return _FakeProcess()

            with (
                patch.dict(
                    os.environ,
                    {
                        "BOOK_AGENT_OCR_STATUS_PATH": str(status_path),
                        "BOOK_AGENT_OCR_HEARTBEAT_SECONDS": "0.1",
                    },
                    clear=False,
                ),
                patch(
                    "book_agent.domain.structure.ocr.shutil.which",
                    side_effect=["/opt/homebrew/bin/uv", "/opt/homebrew/bin/python3.13"],
                ),
                patch("book_agent.domain.structure.ocr.subprocess.Popen", side_effect=_fake_popen),
                patch("book_agent.domain.structure.ocr.time.sleep", return_value=None),
            ):
                results_path = UvSuryaOcrRunner().run(
                    file_path="scan-sample.pdf",
                    output_dir=output_dir,
                )
            self.assertEqual(results_path, output_dir / "results.json")
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status_payload["state"], "succeeded")
            self.assertEqual(status_payload["pid"], 4321)
            self.assertEqual(status_payload["returncode"], 0)
            self.assertEqual(status_payload["output_snapshot"]["results_json_path"], str(results_path.resolve()))
            self.assertIn("warming up model weights", status_payload["stdout_tail"])
            self.assertIn("loading OCR runtime", status_payload["stderr_tail"])

    def test_parse_service_routes_pdf_scan_documents_to_ocr_parser(self) -> None:
        class _UnexpectedPdfParser:
            def parse(self, _file_path, profile=None):
                raise AssertionError("text PDF parser should not be used for pdf_scan")

        class _StubOcrPdfParser:
            def __init__(self):
                self.called = False

            def parse(self, _file_path, profile=None):
                self.called = True
                return ParsedDocument(
                    title="OCR Sample",
                    author="OCR Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="OCR Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="Scanned text recovered through OCR.",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 120.0, 520.0, 180.0]}
                                            ]
                                        },
                                    },
                                    parse_confidence=0.81,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={"pdf_extractor": "surya_ocr"},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="99999999-9999-4999-8999-999999999999",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-ocr-route-test",
            source_path="scan-sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={
                "pdf_profile": {
                    "pdf_kind": "scanned_pdf",
                    "layout_risk": "high",
                    "ocr_required": True,
                }
            },
            created_at=now,
            updated_at=now,
        )
        ocr_parser = _StubOcrPdfParser()

        artifacts = ParseService(
            pdf_parser=_UnexpectedPdfParser(),
            ocr_pdf_parser=ocr_parser,
        ).parse(document, "scan-sample.pdf")

        self.assertTrue(ocr_parser.called)
        self.assertEqual(artifacts.document.status, DocumentStatus.PARSED)
        self.assertEqual(artifacts.document.title, "OCR Sample")
        self.assertEqual(artifacts.blocks[0].source_text, "Scanned text recovered through OCR.")

    def test_parse_service_routes_pdf_mixed_documents_to_ocr_parser(self) -> None:
        class _UnexpectedPdfParser:
            def parse(self, _file_path, profile=None):
                raise AssertionError("text PDF parser should not be used for pdf_mixed")

        class _StubOcrPdfParser:
            def __init__(self):
                self.called = False
                self.profile = None

            def parse(self, _file_path, profile=None):
                self.called = True
                self.profile = profile
                return ParsedDocument(
                    title="Mixed Sample",
                    author="OCR Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-mixed-chapter-001",
                            href="pdf://page/1",
                            title="Mixed Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.CODE.value,
                                    text="def plan():\n    return 'ok'",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 120.0, 520.0, 220.0]}
                                            ]
                                        },
                                        "pdf_block_role": "code",
                                        "protected_artifact": True,
                                    },
                                    parse_confidence=0.79,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={"pdf_extractor": "surya_ocr", "pdf_kind": "mixed_pdf"},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="99999999-9999-4999-8999-111111111111",
            source_type=SourceType.PDF_MIXED,
            file_fingerprint="fingerprint-mixed-route-test",
            source_path="mixed-sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={
                "pdf_profile": {
                    "pdf_kind": "mixed_pdf",
                    "layout_risk": "medium",
                    "ocr_required": True,
                    "protected_artifacts_detected": True,
                }
            },
            created_at=now,
            updated_at=now,
        )
        ocr_parser = _StubOcrPdfParser()

        artifacts = ParseService(
            pdf_parser=_UnexpectedPdfParser(),
            ocr_pdf_parser=ocr_parser,
        ).parse(document, "mixed-sample.pdf")

        self.assertTrue(ocr_parser.called)
        self.assertEqual(ocr_parser.profile["pdf_kind"], "mixed_pdf")
        self.assertEqual(artifacts.document.status, DocumentStatus.PARSED)
        self.assertEqual(artifacts.document.title, "Mixed Sample")
        self.assertEqual(artifacts.blocks[0].source_text, "def plan():\n    return 'ok'")
        self.assertTrue(artifacts.blocks[0].source_span_json["protected_artifact"])


class PdfProfilerTests(unittest.TestCase):
    def test_profiler_flags_fragmented_half_width_pages_as_high_risk(self) -> None:
        dense_text = "attention " * 220
        extraction = PdfExtraction(
            title="Sample",
            author="Test Author",
            metadata={},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text=dense_text,
                            bbox=(304.0, 100.0, 612.0, 744.0),
                            line_texts=[dense_text],
                            span_count=1800,
                            line_count=40,
                            font_size_min=9.0,
                            font_size_max=10.0,
                            font_size_avg=9.5,
                        )
                    ],
                ),
                PdfPage(
                    page_number=2,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=2,
                            block_number=1,
                            text=dense_text,
                            bbox=(304.0, 100.0, 612.0, 744.0),
                            line_texts=[dense_text],
                            span_count=1750,
                            line_count=38,
                            font_size_min=9.0,
                            font_size_max=10.0,
                            font_size_avg=9.5,
                        )
                    ],
                ),
            ],
        )

        profile = PdfFileProfiler().profile_from_extraction(extraction)

        self.assertEqual(profile.layout_risk, "high")
        self.assertEqual(profile.suspicious_page_numbers, [1, 2])

    def test_profiler_marks_scanned_pages_as_high_risk(self) -> None:
        extraction = PdfExtraction(
            title="Scanned Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[],
                )
            ],
        )

        profile = PdfFileProfiler().profile_from_extraction(extraction)

        self.assertEqual(profile.pdf_kind, "scanned_pdf")
        self.assertTrue(profile.ocr_required)
        self.assertEqual(profile.layout_risk, "high")
        self.assertEqual(profile.extractor_kind, "basic")

    def test_profiler_downgrades_basic_fragment_only_long_text_pdf_to_medium_risk(self) -> None:
        dense_text = "agent " * 700
        pages: list[PdfPage] = []
        for index in range(1, 51):
            if index <= 16:
                blocks = [
                    PdfTextBlock(
                        page_number=index,
                        block_number=1,
                        text=dense_text,
                        bbox=(304.0, 100.0, 612.0, 744.0),
                        line_texts=[dense_text],
                        span_count=1800,
                        line_count=40,
                        font_size_min=9.0,
                        font_size_max=10.0,
                        font_size_avg=9.5,
                    )
                ]
            else:
                blocks = [
                    PdfTextBlock(
                        page_number=index,
                        block_number=1,
                        text=dense_text,
                        bbox=(72.0, 100.0, 612.0, 744.0),
                        line_texts=[dense_text],
                        span_count=1800,
                        line_count=40,
                        font_size_min=9.0,
                        font_size_max=10.0,
                        font_size_avg=9.5,
                    )
                ]
            pages.append(
                PdfPage(
                    page_number=index,
                    width=612.0,
                    height=792.0,
                    blocks=blocks,
                )
            )
        extraction = PdfExtraction(
            title="Long Basic Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=pages,
        )

        profile = PdfFileProfiler().profile_from_extraction(extraction)

        self.assertEqual(profile.pdf_kind, "text_pdf")
        self.assertEqual(profile.extractor_kind, "basic")
        self.assertEqual(profile.multi_column_page_count, 0)
        self.assertEqual(profile.fragment_page_count, 16)
        self.assertEqual(profile.layout_risk, "medium")

    def test_profiler_keeps_short_basic_fragment_only_text_pdf_high_risk(self) -> None:
        dense_text = "attention " * 500
        extraction = PdfExtraction(
            title="Short Basic Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=index,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=index,
                            block_number=1,
                            text=dense_text,
                            bbox=(304.0, 100.0, 612.0, 744.0),
                            line_texts=[dense_text],
                            span_count=1500,
                            line_count=35,
                            font_size_min=9.0,
                            font_size_max=10.0,
                            font_size_avg=9.5,
                        )
                    ],
                )
                for index in range(1, 12)
            ],
        )

        profile = PdfFileProfiler().profile_from_extraction(extraction)

        self.assertEqual(profile.pdf_kind, "text_pdf")
        self.assertEqual(profile.fragment_page_count, 11)
        self.assertEqual(profile.layout_risk, "high")

    def test_profiler_downgrades_short_basic_academic_paper_candidate_to_medium_risk(self) -> None:
        dense_text = "attention " * 520
        reference_text = (
            "[1] Ashish Vaswani, Noam Shazeer, and Niki Parmar. 2017. Attention Is All You Need. "
            "[2] Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua Bengio. 2015. Neural machine translation."
        )
        extraction = PdfExtraction(
            title="Academic Paper Sample",
            author="Test Author",
            metadata={"pdf_extractor": "basic"},
            outline_entries=[],
            pages=[
                PdfPage(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    blocks=[
                        PdfTextBlock(
                            page_number=1,
                            block_number=1,
                            text="Transformer Attention in Practice",
                            bbox=(180.0, 84.0, 430.0, 112.0),
                            line_texts=["Transformer Attention in Practice"],
                            span_count=32,
                            line_count=1,
                            font_size_min=18.0,
                            font_size_max=20.0,
                            font_size_avg=19.0,
                        ),
                        PdfTextBlock(
                            page_number=1,
                            block_number=2,
                            text="Abstract We study translation-oriented recovery for compact academic PDFs.",
                            bbox=(72.0, 148.0, 540.0, 192.0),
                            line_texts=["Abstract We study translation-oriented recovery for compact academic PDFs."],
                            span_count=84,
                            line_count=2,
                            font_size_min=10.0,
                            font_size_max=11.0,
                            font_size_avg=10.5,
                        ),
                    ],
                ),
                *[
                    PdfPage(
                        page_number=index,
                        width=612.0,
                        height=792.0,
                        blocks=[
                            PdfTextBlock(
                                page_number=index,
                                block_number=1,
                                text=dense_text,
                                bbox=(304.0, 100.0, 612.0, 744.0),
                                line_texts=[dense_text],
                                span_count=1800,
                                line_count=40,
                                font_size_min=9.0,
                                font_size_max=10.0,
                                font_size_avg=9.5,
                            )
                        ],
                    )
                    for index in (2, 3)
                ],
                *[
                    PdfPage(
                        page_number=index,
                        width=612.0,
                        height=792.0,
                        blocks=[
                            PdfTextBlock(
                                page_number=index,
                                block_number=1,
                                text=reference_text,
                                bbox=(72.0, 120.0, 540.0, 744.0),
                                line_texts=[reference_text],
                                span_count=220,
                                line_count=8,
                                font_size_min=9.0,
                                font_size_max=10.0,
                                font_size_avg=9.5,
                            )
                        ],
                    )
                    for index in (4, 5)
                ],
            ],
        )

        profile = PdfFileProfiler().profile_from_extraction(extraction)

        self.assertEqual(profile.pdf_kind, "text_pdf")
        self.assertEqual(profile.fragment_page_count, 2)
        self.assertEqual(profile.layout_risk, "medium")
        self.assertEqual(profile.recovery_lane, "academic_paper")
        self.assertEqual(profile.trailing_reference_page_count, 2)
        self.assertTrue(profile.academic_paper_candidate)

    def test_reference_entry_ignores_publisher_contact_and_social_lines(self) -> None:
        self.assertFalse(
            _looks_like_reference_entry(
                "Please address comments and questions to support@oreilly.com or visit https://oreilly.com."
            )
        )
        self.assertFalse(
            _looks_like_reference_entry(
                "Find us on LinkedIn: https://linkedin.com/company/oreilly-media"
            )
        )
        self.assertTrue(
            _looks_like_reference_entry(
                "[1] Smith, J. 2024. Agent evaluation in practice. https://doi.org/10.1234/example"
            )
        )

    def test_profiler_downgrades_outlined_localized_multi_column_book_to_medium_risk(self) -> None:
        dense_text = "agent systems " * 240
        pages: list[PdfPage] = []
        for index in range(1, 101):
            if index in {25, 40, 55, 70, 85, 96}:
                blocks = [
                    PdfTextBlock(
                        page_number=index,
                        block_number=1,
                        text=dense_text,
                        bbox=(72.0, 100.0, 250.0, 330.0),
                        line_texts=[dense_text],
                        span_count=900,
                        line_count=22,
                        font_size_min=9.0,
                        font_size_max=10.0,
                        font_size_avg=9.5,
                    ),
                    PdfTextBlock(
                        page_number=index,
                        block_number=2,
                        text=dense_text,
                        bbox=(330.0, 88.0, 510.0, 318.0),
                        line_texts=[dense_text],
                        span_count=900,
                        line_count=22,
                        font_size_min=9.0,
                        font_size_max=10.0,
                        font_size_avg=9.5,
                    ),
                    PdfTextBlock(
                        page_number=index,
                        block_number=3,
                        text=dense_text,
                        bbox=(72.0, 352.0, 250.0, 582.0),
                        line_texts=[dense_text],
                        span_count=900,
                        line_count=22,
                        font_size_min=9.0,
                        font_size_max=10.0,
                        font_size_avg=9.5,
                    ),
                    PdfTextBlock(
                        page_number=index,
                        block_number=4,
                        text=dense_text,
                        bbox=(330.0, 340.0, 510.0, 570.0),
                        line_texts=[dense_text],
                        span_count=900,
                        line_count=22,
                        font_size_min=9.0,
                        font_size_max=10.0,
                        font_size_avg=9.5,
                    ),
                ]
            else:
                blocks = [
                    PdfTextBlock(
                        page_number=index,
                        block_number=1,
                        text=dense_text,
                        bbox=(72.0, 100.0, 540.0, 744.0),
                        line_texts=[dense_text],
                        span_count=1800,
                        line_count=40,
                        font_size_min=9.0,
                        font_size_max=10.0,
                        font_size_avg=9.5,
                    )
                ]
            pages.append(PdfPage(page_number=index, width=612.0, height=792.0, blocks=blocks))

        extraction = PdfExtraction(
            title="Building Agent Systems",
            author="Test Author",
            metadata={"pdf_extractor": "pymupdf"},
            outline_entries=[
                PdfOutlineEntry(level=1, title="Preface", page_number=5),
                PdfOutlineEntry(level=1, title="Chapter 1 Durable Products", page_number=10),
                PdfOutlineEntry(level=1, title="Chapter 2 Learning Systems", page_number=30),
            ],
            pages=pages,
        )

        profile = PdfFileProfiler().profile_from_extraction(extraction)

        self.assertEqual(profile.pdf_kind, "text_pdf")
        self.assertEqual(profile.multi_column_page_count, 6)
        self.assertEqual(profile.layout_risk, "medium")
        self.assertEqual(profile.recovery_lane, "outlined_book")

    def test_bootstrap_pipeline_keeps_book_conclusion_and_contact_pages_out_of_top_level_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "book-preface-contact-conclusion.pdf"
            _write_book_with_preface_contact_and_internal_conclusion_pdf(pdf_path)

            parser = PDFParser(extractor=BasicPdfTextExtractor())
            profile = parser.profiler.profile(pdf_path)
            result = parser.parse(pdf_path, profile=profile)

        self.assertEqual(profile.layout_risk, "low")
        self.assertEqual(
            [chapter.title for chapter in result.chapters],
            ["Preface", "Chapter 1 Durable Products", "Chapter 2 Learning Systems"],
        )
        self.assertNotIn("Conclusion", [chapter.title for chapter in result.chapters])
        self.assertNotIn("References", [chapter.title for chapter in result.chapters])
        self.assertEqual(result.chapters[0].metadata["pdf_section_family"], "frontmatter")
        self.assertEqual(result.chapters[1].metadata["pdf_section_family"], "body")
        self.assertEqual(result.chapters[2].metadata["pdf_section_family"], "body")

    def test_bootstrap_pipeline_resolves_document_book_title_from_source_filename_for_outlined_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = (
                Path(tmpdir)
                / "Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems (Antonio Gulli) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
            )
            _write_pdf(
                pdf_path,
                [
                    [
                        _text_command(72, 724, 22, "Dedication"),
                        _text_command(72, 670, 12, "A short dedication page."),
                    ],
                    [
                        _text_command(72, 724, 22, "Foreword"),
                        _text_command(72, 670, 12, "A short foreword page."),
                    ],
                    [
                        _text_command(72, 724, 22, "Chapter 1 Durable Products"),
                        _text_command(72, 670, 12, "The first body chapter starts here."),
                    ],
                ],
                title="",
                author="Test Author",
                outline_entries=[
                    (1, "Dedication", 1),
                    (1, "Foreword", 2),
                    (1, "Chapter 1 Durable Products", 3),
                ],
            )

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        expected_title = "Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems"
        self.assertEqual(result.document.title, expected_title)
        self.assertEqual(result.document.title_src, expected_title)
        self.assertIsNone(result.document.title_tgt)
        self.assertEqual(result.document.metadata_json["document_title"]["src"], expected_title)
        self.assertEqual(result.document.metadata_json["document_title"]["resolution_source"], "source_filename")

    def test_outlined_book_parser_collapses_auxiliary_top_level_outline_entries_into_neighboring_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "outlined-book-outline-collapse.pdf"
            _write_pdf(
                pdf_path,
                [
                    [
                        _text_command(72, 724, 22, "Dedication"),
                        _text_command(72, 670, 12, "Dedication page."),
                    ],
                    [
                        _text_command(72, 724, 22, "Foreword"),
                        _text_command(72, 670, 12, "Foreword page."),
                    ],
                    [
                        _text_command(72, 724, 20, "A Thought Leader's Perspective: Power and Responsibility"),
                        _text_command(72, 670, 12, "This should stay inside the foreword."),
                    ],
                    [
                        _text_command(72, 724, 22, "Introduction"),
                        _text_command(72, 670, 12, "Introduction page."),
                    ],
                    [
                        _text_command(72, 724, 22, "Chapter 1 Durable Products"),
                        _text_command(72, 670, 12, "Main chapter content."),
                    ],
                    [
                        _text_command(72, 724, 20, "Key Takeaways"),
                        _text_command(72, 670, 12, "This should stay inside chapter 1."),
                    ],
                    [
                        _text_command(72, 724, 20, "Conclusion"),
                        _text_command(72, 670, 12, "This should stay inside chapter 1."),
                    ],
                    [
                        _text_command(72, 724, 20, "References"),
                        _text_command(72, 670, 12, "This should stay inside chapter 1."),
                    ],
                    [
                        _text_command(72, 724, 22, "Chapter 2 Learning Systems"),
                        _text_command(72, 670, 12, "Second main chapter."),
                    ],
                    [
                        _text_command(72, 724, 22, "Appendix A Metrics"),
                        _text_command(72, 670, 12, "Appendix content."),
                    ],
                    [
                        _text_command(72, 724, 22, "Glossary"),
                        _text_command(72, 670, 12, "Glossary content."),
                    ],
                ],
                title="",
                author="Test Author",
                outline_entries=[
                    (1, "Dedication", 1),
                    (1, "Foreword", 2),
                    (1, "A Thought Leader's Perspective: Power and Responsibility", 3),
                    (1, "Introduction", 4),
                    (1, "Chapter 1 Durable Products", 5),
                    (1, "Key Takeaways", 6),
                    (1, "Conclusion", 7),
                    (1, "References", 8),
                    (1, "Chapter 2 Learning Systems", 9),
                    (1, "Appendix A Metrics", 10),
                    (1, "Glossary", 11),
                ],
            )

            parser = PDFParser(extractor=BasicPdfTextExtractor(), profiler=PdfFileProfiler(BasicPdfTextExtractor()))
            profile_payload = parser.profiler.profile(pdf_path).to_dict()
            profile_payload["recovery_lane"] = "outlined_book"
            parsed = parser.parse(pdf_path, profile=profile_payload)

        self.assertEqual(
            [chapter.title for chapter in parsed.chapters],
            [
                "Dedication",
                "Foreword",
                "Introduction",
                "Chapter 1 Durable Products",
                "Chapter 2 Learning Systems",
                "Appendix A Metrics",
                "Glossary",
            ],
        )
        self.assertEqual(
            [chapter.metadata["source_page_start"] for chapter in parsed.chapters],
            [1, 2, 4, 5, 9, 10, 11],
        )
        self.assertEqual(parsed.chapters[1].metadata["source_page_end"], 3)
        self.assertEqual(parsed.chapters[3].metadata["source_page_end"], 8)
        chapter_one_heading_texts = [
            block.text
            for block in parsed.chapters[3].blocks
            if block.block_type == BlockType.HEADING.value
        ]
        self.assertEqual(
            chapter_one_heading_texts,
            ["Chapter 1 Durable Products", "Key Takeaways", "Conclusion", "References"],
        )


class PdfApiWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        sqlite_path = Path(self.tempdir.name) / "book-agent.db"
        self.engine = build_engine(
            f"sqlite+pysqlite:///{sqlite_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.app = create_app()
        self.app.state.session_factory = self.session_factory
        self.app.state.export_root = str(Path(self.tempdir.name) / "exports")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_contract_advertises_text_pdf_support(self) -> None:
        response = self.client.get("/v1/documents/contract")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pdf_text", payload["supported_source_types"])
        self.assertEqual(payload["current_phase"], "p1_text_pdf_guarded_bootstrap")

    def test_bootstrap_document_returns_pdf_profile_for_text_pdf(self) -> None:
        pdf_path = Path(self.tempdir.name) / "sample.pdf"
        _write_low_risk_text_pdf(pdf_path)

        response = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["source_type"], "pdf_text")
        self.assertEqual(payload["pdf_profile"]["pdf_kind"], "text_pdf")
        self.assertEqual(payload["pdf_profile"]["layout_risk"], "low")
        self.assertEqual(payload["pdf_page_evidence"]["schema_version"], 1)
        self.assertEqual(payload["pdf_page_evidence"]["page_count"], 2)
        self.assertEqual(payload["pdf_page_evidence"]["pdf_pages"][0]["page_number"], 1)
        self.assertEqual(payload["chapters"][0]["risk_level"], "low")
        self.assertGreater(payload["chapters"][0]["parse_confidence"], 0.9)

    def test_bootstrap_document_accepts_high_risk_layout_pdf_with_explicit_risk(self) -> None:
        pdf_path = Path(self.tempdir.name) / "two-column.pdf"
        _write_high_risk_two_column_pdf(pdf_path)

        response = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["source_type"], "pdf_text")
        self.assertEqual(payload["pdf_profile"]["pdf_kind"], "text_pdf")
        self.assertEqual(payload["pdf_profile"]["layout_risk"], "high")
        self.assertEqual(payload["chapters"][0]["risk_level"], "critical")
        self.assertEqual(payload["pdf_page_evidence"]["page_count"], 2)
        self.assertEqual(
            [page["page_layout_risk"] for page in payload["pdf_page_evidence"]["pdf_pages"]],
            ["medium", "medium"],
        )

    def test_bootstrap_document_accepts_academic_paper_pdf_lane(self) -> None:
        pdf_path = Path(self.tempdir.name) / "academic-paper.pdf"
        _write_outlined_multi_column_academic_paper_pdf(pdf_path)

        response = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["pdf_profile"]["extractor_kind"], "pymupdf")
        self.assertEqual(payload["pdf_profile"]["layout_risk"], "medium")
        self.assertEqual(payload["pdf_profile"]["recovery_lane"], "academic_paper")
        self.assertTrue(payload["pdf_profile"]["academic_paper_candidate"])
        self.assertEqual(payload["pdf_profile"]["trailing_reference_page_count"], 1)
        self.assertGreaterEqual(len(payload["chapters"]), 4)
        self.assertIn("1 Introduction", [chapter["title_src"] for chapter in payload["chapters"]])
        self.assertEqual(payload["pdf_page_evidence"]["pdf_pages"][3]["page_family"], "references")

    def test_document_summary_exposes_pdf_image_summary(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Summary Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Summary Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "image_alt": "System overview diagram",
                                    },
                                    parse_confidence=0.97,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={
                        "pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"},
                        "pdf_page_evidence": {
                            "schema_version": 1,
                            "page_count": 1,
                            "pdf_pages": [{"page_number": 1, "page_family": "body"}],
                        },
                    },
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="55555555-5555-4555-8555-555555555555",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-image-summary-test",
            source_path=str(Path(self.tempdir.name) / "image-summary.pdf"),
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )

        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "image-summary.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            job_runs=[parse_artifacts.job_run],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

        response = self.client.get(f"/v1/documents/{document.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["pdf_image_summary"]["schema_version"], 1)
        self.assertEqual(payload["pdf_image_summary"]["image_count"], 1)
        self.assertEqual(payload["pdf_image_summary"]["page_count"], 1)
        self.assertEqual(payload["pdf_image_summary"]["page_numbers"], [1])
        self.assertEqual(payload["pdf_image_summary"]["stored_asset_count"], 0)
        self.assertEqual(payload["pdf_image_summary"]["unassigned_image_count"], 0)
        self.assertEqual(payload["pdf_image_summary"]["caption_linked_count"], 0)
        self.assertEqual(payload["pdf_image_summary"]["uncaptioned_image_count"], 1)
        self.assertEqual(payload["pdf_image_summary"]["image_type_counts"]["embedded_image"], 1)
        self.assertEqual(
            payload["pdf_image_summary"]["chapter_image_counts"][parse_artifacts.chapters[0].id],
            1,
        )
        self.assertEqual(payload["chapters"][0]["pdf_image_summary"]["image_count"], 1)
        self.assertEqual(payload["chapters"][0]["pdf_image_summary"]["page_count"], 1)
        self.assertEqual(payload["chapters"][0]["pdf_image_summary"]["caption_linked_count"], 0)
        self.assertEqual(payload["chapters"][0]["pdf_image_summary"]["uncaptioned_image_count"], 1)

    def test_review_package_export_includes_pdf_page_evidence(self) -> None:
        pdf_path = Path(self.tempdir.name) / "sample.pdf"
        _write_low_risk_text_pdf(pdf_path)

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(export.status_code, 200)

        review_package_path = Path(export.json()["chapter_results"][0]["file_path"])
        review_package = json.loads(review_package_path.read_text(encoding="utf-8"))

        self.assertIn("pdf_page_evidence", review_package)
        self.assertEqual(review_package["pdf_page_evidence"]["schema_version"], 1)
        self.assertEqual(review_package["pdf_page_evidence"]["document_page_count"], 2)
        self.assertEqual(review_package["pdf_page_evidence"]["page_range"], {"start": 1, "end": 2})
        self.assertEqual(review_package["pdf_page_evidence"]["page_count"], 2)
        self.assertEqual(review_package["pdf_page_evidence"]["pdf_pages"][0]["page_number"], 1)
        self.assertIn("pdf_preserve_evidence", review_package)
        self.assertEqual(review_package["pdf_preserve_evidence"]["schema_version"], 1)
        self.assertEqual(review_package["pdf_preserve_evidence"]["special_section_page_count"], 0)
        self.assertEqual(review_package["pdf_preserve_evidence"]["preserved_block_count"], 0)
        self.assertEqual(review_package["pdf_preserve_evidence"]["page_contracts"], [])
        self.assertIn("pdf_page_debug_evidence", review_package)
        self.assertEqual(review_package["pdf_page_debug_evidence"]["schema_version"], 1)
        self.assertEqual(review_package["pdf_page_debug_evidence"]["page_count"], 0)
        self.assertEqual(review_package["pdf_page_debug_evidence"]["pages"], [])

    def test_review_package_export_surfaces_page_layout_risk_debug_evidence(self) -> None:
        pdf_path = Path(self.tempdir.name) / "academic-asymmetric-first-page.pdf"
        _write_asymmetric_first_page_academic_paper_pdf(pdf_path)
        now = datetime.now(timezone.utc)
        document = Document(
            id="66666666-6666-4666-8666-666666666666",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-academic-layout-risk-review-package",
            source_path=str(pdf_path),
            status=DocumentStatus.INGESTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService().parse(document, pdf_path)
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            job_runs=[parse_artifacts.job_run],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            review_package = export_service._build_review_package(chapter_bundle)

        page = review_package["pdf_page_evidence"]["pdf_pages"][0]
        self.assertEqual(page["page_number"], 1)
        self.assertEqual(page["page_layout_risk"], "high")
        self.assertIn("academic_first_page_asymmetric", page["page_layout_reasons"])

        debug_evidence = review_package["pdf_page_debug_evidence"]
        self.assertEqual(debug_evidence["page_count"], 1)
        debug_page = debug_evidence["pages"][0]
        self.assertEqual(debug_page["page_number"], 1)
        self.assertEqual(debug_page["page_layout_risk"], "high")
        self.assertIn("academic_first_page_asymmetric", debug_page["page_layout_reasons"])
        self.assertIn("page_layout_risk", debug_page["debug_reasons"])

    def test_review_package_export_includes_backmatter_preserve_evidence(self) -> None:
        pdf_path = Path(self.tempdir.name) / "inline-index-heading.pdf"
        _write_inline_index_heading_backmatter_pdf(pdf_path)

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(export.status_code, 200)

        review_package_path = Path(export.json()["chapter_results"][-1]["file_path"])
        review_package = json.loads(review_package_path.read_text(encoding="utf-8"))
        preserve = review_package["pdf_preserve_evidence"]
        self.assertEqual(preserve["chapter_section_family"], "backmatter")
        self.assertEqual(preserve["special_section_page_count"], 1)
        self.assertEqual(preserve["special_section_page_family_counts"]["backmatter"], 1)
        self.assertEqual(preserve["preserved_block_count"], 1)
        self.assertEqual(preserve["source_only_block_count"], 1)
        contract = preserve["page_contracts"][0]
        self.assertEqual(contract["page_family"], "backmatter")
        self.assertEqual(contract["preserve_policy"], "source_only")
        self.assertEqual(contract["source_only_block_count"], 1)
        self.assertGreaterEqual(contract["render_mode_counts"]["source_artifact_full_width"], 1)
        self.assertIn("尾部资料页保留原样", contract["notices"])
        self.assertGreaterEqual(len(contract["source_sentence_ids"]), 1)
        debug_evidence = review_package["pdf_page_debug_evidence"]
        self.assertEqual(debug_evidence["schema_version"], 1)
        self.assertEqual(debug_evidence["page_count"], 1)
        debug_page = debug_evidence["pages"][0]
        self.assertEqual(debug_page["page_number"], 4)
        self.assertEqual(debug_page["page_family"], "backmatter")
        self.assertEqual(debug_page["preserve_policy"], "source_only")
        self.assertIn("special_section", debug_page["debug_reasons"])
        self.assertIn("preserve_contract", debug_page["debug_reasons"])
        body_debug_block = next(
            block for block in debug_page["blocks"] if block["pdf_block_role"] == "body"
        )
        self.assertEqual(body_debug_block["render_mode"], "source_artifact_full_width")
        self.assertTrue(body_debug_block["expected_source_only"])
        self.assertEqual(body_debug_block["nontranslatable_reason"], "pdf_backmatter")
        self.assertIn("Upcoming Titles", body_debug_block["source_excerpt"])
        self.assertEqual(body_debug_block["notice"], "尾部资料页保留原样")

    def test_review_package_export_includes_appendix_backmatter_cue_evidence(self) -> None:
        pdf_path = Path(self.tempdir.name) / "appendix-backmatter-cue.pdf"
        _write_appendix_backmatter_cue_pdf(pdf_path)

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(export.status_code, 200)

        review_package_path = Path(export.json()["chapter_results"][-1]["file_path"])
        review_package = json.loads(review_package_path.read_text(encoding="utf-8"))
        page = review_package["pdf_page_evidence"]["pdf_pages"][0]
        self.assertEqual(page["page_family"], "backmatter")
        self.assertEqual(page["page_family_source"], "backmatter_cue")
        self.assertEqual(page["backmatter_cue"], "Upcoming Titles")
        self.assertEqual(page["backmatter_cue_source"], "heading_title")
        preserve = review_package["pdf_preserve_evidence"]["page_contracts"][0]
        self.assertEqual(preserve["page_family"], "backmatter")
        self.assertEqual(preserve["family_source"], "backmatter_cue")
        self.assertEqual(preserve["backmatter_cue"], "Upcoming Titles")
        debug_page = review_package["pdf_page_debug_evidence"]["pages"][0]
        self.assertEqual(debug_page["page_family"], "backmatter")
        self.assertEqual(debug_page["family_source"], "backmatter_cue")
        self.assertEqual(debug_page["backmatter_cue"], "Upcoming Titles")
        self.assertIn("backmatter_cue", debug_page["debug_reasons"])

    def test_review_package_export_includes_relocated_footnote_debug_evidence(self) -> None:
        pdf_path = Path(self.tempdir.name) / "cross-page-multiparagraph-footnote.pdf"
        _write_cross_page_multiparagraph_footnote_pdf(pdf_path)

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(export.status_code, 200)

        review_package_path = Path(export.json()["chapter_results"][0]["file_path"])
        review_package = json.loads(review_package_path.read_text(encoding="utf-8"))
        debug_evidence = review_package["pdf_page_debug_evidence"]
        self.assertEqual(debug_evidence["page_count"], 2)
        self.assertEqual(
            [page["page_number"] for page in debug_evidence["pages"]],
            [1, 2],
        )
        for debug_page in debug_evidence["pages"]:
            self.assertIn("footnote_relocated", debug_page["debug_reasons"])
            self.assertEqual(debug_page["relocated_footnote_count"], 1)
            self.assertEqual(debug_page["max_footnote_segment_count"], 3)
        footnote_debug_block = next(
            block
            for block in debug_evidence["pages"][0]["blocks"]
            if block["pdf_block_role"] == "footnote"
        )
        self.assertIn("footnote_multisegment_repaired", footnote_debug_block["recovery_flags"])
        self.assertIn("footnote_continuation_repaired", footnote_debug_block["recovery_flags"])

    def test_review_package_export_includes_nested_appendix_subheading_candidates(self) -> None:
        pdf_path = Path(self.tempdir.name) / "appendix-nested-subheading.pdf"
        _write_appendix_nested_subheading_signal_pdf(pdf_path)

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "review_package"},
        )
        self.assertEqual(export.status_code, 200)

        review_package_path = Path(export.json()["chapter_results"][-1]["file_path"])
        review_package = json.loads(review_package_path.read_text(encoding="utf-8"))
        debug_evidence = review_package["pdf_page_debug_evidence"]
        nested_page = next(page for page in debug_evidence["pages"] if page["page_number"] == 4)
        self.assertIn("nested_appendix_subheading_candidate", nested_page["debug_reasons"])
        self.assertEqual(
            nested_page["appendix_nested_subheadings"],
            [
                {
                    "label": "K.2.5",
                    "depth": 2,
                    "title": "Context Awareness",
                    "full_title": "Appendix K.2.5 Context Awareness",
                }
            ],
        )

    def test_backmatter_exports_as_expected_source_only(self) -> None:
        pdf_path = Path(self.tempdir.name) / "inline-index-heading.pdf"
        _write_inline_index_heading_backmatter_pdf(pdf_path)

        bootstrap = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})
        self.assertEqual(bootstrap.status_code, 201)
        document_id = bootstrap.json()["document_id"]

        translate = self.client.post(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(translate.status_code, 200)

        review = self.client.post(f"/v1/documents/{document_id}/review")
        self.assertEqual(review.status_code, 200)

        export = self.client.post(
            f"/v1/documents/{document_id}/export",
            json={"export_type": "bilingual_html"},
        )
        self.assertEqual(export.status_code, 200)

        summary = self.client.get(f"/v1/documents/{document_id}")
        self.assertEqual(summary.status_code, 200)
        backmatter_summary = next(
            chapter for chapter in summary.json()["chapters"] if chapter["title_src"] == "Back Matter"
        )
        self.assertEqual(backmatter_summary["packet_count"], 0)

        chapter_export = export.json()["chapter_results"][-1]
        manifest_path = Path(chapter_export["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["chapter_title"], "Back Matter")
        self.assertEqual(manifest["pdf_page_evidence"]["pdf_pages"][0]["page_family"], "backmatter")
        self.assertEqual(manifest["pdf_preserve_evidence"]["chapter_section_family"], "backmatter")
        self.assertEqual(manifest["pdf_preserve_evidence"]["special_section_page_family_counts"]["backmatter"], 1)
        self.assertEqual(manifest["render_summary"]["expected_source_only_block_count"], 1)
        self.assertGreaterEqual(manifest["render_summary"]["render_mode_counts"]["source_artifact_full_width"], 1)
        contract = manifest["pdf_preserve_evidence"]["page_contracts"][0]
        self.assertEqual(contract["page_number"], 4)
        self.assertEqual(contract["preserve_policy"], "source_only")
        self.assertEqual(contract["source_only_block_count"], 1)
        html_path = Path(chapter_export["file_path"])
        html_text = html_path.read_text(encoding="utf-8")
        self.assertIn("尾部资料页保留原样", html_text)
        self.assertIn("artifact-note", html_text)
        self.assertNotIn("<table border='1'", html_text)


class PdfReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def test_medium_risk_pdf_creates_structure_review_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "medium-risk.pdf"
            _write_medium_risk_two_column_pdf(pdf_path)
            artifacts = BootstrapOrchestrator().bootstrap_document(pdf_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

        chapter_id = next(chapter.id for chapter in artifacts.chapters if chapter.title_src == "Abstract")
        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)

            misordering_issue = next(
                issue for issue in review_artifacts.issues if issue.issue_type == "MISORDERING"
            )
            misordering_action = next(
                action for action in review_artifacts.actions if action.issue_id == misordering_issue.id
            )

            self.assertEqual(misordering_issue.root_cause_layer.value, "structure")
            self.assertEqual(misordering_issue.severity.value, "high")
            self.assertTrue(misordering_issue.blocking)
            self.assertEqual(misordering_issue.evidence_json["layout_risk"], "medium")
            self.assertEqual(misordering_action.action_type, ActionType.REPARSE_CHAPTER)
            self.assertEqual(misordering_action.scope_type, JobScopeType.CHAPTER)

            refreshed_bundle = ReviewRepository(session).load_chapter_bundle(chapter_id)
            self.assertEqual(refreshed_bundle.chapter.risk_level.value, "high")

    def test_academic_paper_medium_risk_creates_advisory_structure_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-paper.pdf"
            _write_academic_paper_pdf(pdf_path)
            extractor = BasicPdfTextExtractor()
            pipeline = BootstrapPipeline(
                ingest_service=IngestService(pdf_profiler=PdfFileProfiler(extractor)),
                parse_service=ParseService(
                    pdf_parser=PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor))
                )
            )
            artifacts = BootstrapOrchestrator(pipeline).bootstrap_document(pdf_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
            DocumentWorkflowService(session).translate_document(artifacts.document.id)
            session.commit()

        chapter_id = next(chapter.id for chapter in artifacts.chapters if chapter.title_src == "Abstract")
        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)

            misordering_issue = next(
                issue for issue in review_artifacts.issues if issue.issue_type == "MISORDERING"
            )
            misordering_action = next(
                action for action in review_artifacts.actions if action.issue_id == misordering_issue.id
            )

            self.assertEqual(misordering_issue.root_cause_layer.value, "structure")
            self.assertEqual(misordering_issue.severity.value, "medium")
            self.assertFalse(misordering_issue.blocking)
            self.assertEqual(misordering_issue.evidence_json["layout_risk"], "medium")
            self.assertEqual(misordering_issue.evidence_json["recovery_lane"], "academic_paper")
            self.assertEqual(
                misordering_issue.evidence_json["review_policy"],
                "academic_paper_medium_layout_advisory",
            )
            self.assertEqual(misordering_action.action_type, ActionType.REPARSE_CHAPTER)
            self.assertEqual(misordering_action.scope_type, JobScopeType.CHAPTER)

            refreshed_bundle = ReviewRepository(session).load_chapter_bundle(chapter_id)
            self.assertEqual(refreshed_bundle.chapter.status, ChapterStatus.QA_CHECKED)
            self.assertEqual(refreshed_bundle.chapter.risk_level.value, "high")

    def test_academic_paper_references_chapter_skips_misordering_without_local_suspicious_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-paper.pdf"
            _write_academic_paper_pdf(pdf_path)
            extractor = BasicPdfTextExtractor()
            pipeline = BootstrapPipeline(
                ingest_service=IngestService(pdf_profiler=PdfFileProfiler(extractor)),
                parse_service=ParseService(
                    pdf_parser=PDFParser(extractor=extractor, profiler=PdfFileProfiler(extractor))
                )
            )
            artifacts = BootstrapOrchestrator(pipeline).bootstrap_document(pdf_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
            DocumentWorkflowService(session).translate_document(artifacts.document.id)
            session.commit()

        chapter_id = next(chapter.id for chapter in artifacts.chapters if chapter.title_src == "References")
        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            self.assertFalse(any(issue.issue_type == "MISORDERING" for issue in review_artifacts.issues))

    def test_academic_paper_skips_misordering_when_suspicious_pages_are_structurally_anchored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-inline-sections.pdf"
            _write_academic_paper_with_inline_sections_pdf(pdf_path)
            artifacts = BootstrapOrchestrator().bootstrap_document(pdf_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
            DocumentWorkflowService(session).translate_document(artifacts.document.id)
            session.commit()

        chapter_id = artifacts.chapters[0].id
        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            self.assertFalse(any(issue.issue_type == "MISORDERING" for issue in review_artifacts.issues))
            refreshed_bundle = ReviewRepository(session).load_chapter_bundle(chapter_id)
            self.assertEqual(refreshed_bundle.chapter.status, ChapterStatus.QA_CHECKED)

    def test_academic_paper_medium_many_suspicious_pages_stays_advisory(self) -> None:
        with self.session_factory() as session:
            service = ReviewService(ReviewRepository(session))
            bundle = SimpleNamespace(
                document=SimpleNamespace(metadata_json={"pdf_profile": {"recovery_lane": "academic_paper"}})
            )
            policy = service._pdf_layout_review_policy(
                bundle,
                "medium",
                0.82,
                [3, 4, 6],
                [3, 4, 6],
                [3, 4, 6],
                [],
                [
                    {
                        "page_number": 3,
                        "layout_suspect": True,
                        "recovery_flags": ["academic_section_heading_recovered"],
                        "role_counts": {"heading": 1, "body": 2},
                    },
                    {
                        "page_number": 4,
                        "layout_suspect": True,
                        "recovery_flags": [],
                        "role_counts": {"body": 4, "caption": 1, "table_like": 1},
                    },
                    {
                        "page_number": 6,
                        "layout_suspect": True,
                        "recovery_flags": [],
                        "role_counts": {"body": 3, "table_like": 1},
                    },
                ],
            )

        self.assertEqual(policy["reason"], "academic_paper_medium_wide_layout_advisory")
        self.assertTrue(policy["emit_issue"])
        self.assertFalse(policy["blocking"])
        self.assertEqual(policy["severity"], Severity.MEDIUM)

    def test_academic_paper_local_high_page_layout_risk_creates_low_advisory_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-asymmetric-first-page.pdf"
            _write_asymmetric_first_page_academic_paper_pdf(pdf_path)
            artifacts = BootstrapOrchestrator().bootstrap_document(pdf_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
            DocumentWorkflowService(session).translate_document(artifacts.document.id)
            session.commit()

        chapter_id = artifacts.chapters[0].id
        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            misordering_issue = next(
                issue for issue in review_artifacts.issues if issue.issue_type == "MISORDERING"
            )

        self.assertEqual(misordering_issue.severity, Severity.LOW)
        self.assertFalse(misordering_issue.blocking)
        self.assertEqual(misordering_issue.evidence_json["layout_risk"], "medium")
        self.assertEqual(misordering_issue.evidence_json["recovery_lane"], "academic_paper")
        self.assertEqual(
            misordering_issue.evidence_json["review_policy"],
            "academic_paper_local_page_layout_advisory",
        )
        self.assertEqual(misordering_issue.evidence_json["page_layout_risk_pages"], [1])
        self.assertEqual(misordering_issue.evidence_json["high_layout_risk_pages"], [1])
        self.assertEqual(
            misordering_issue.evidence_json["page_layout_reasons_by_page"]["1"],
            ["academic_first_page_asymmetric"],
        )

    def test_academic_paper_captioned_artifact_missing_group_context_creates_structure_issue(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Academic Table Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Academic Table Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.TABLE.value,
                                    text="Model  BLEU  Cost\nTransformer  27.5  1.0x10^19",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-tbl1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [84.0, 244.0, 420.0, 314.0]}
                                            ]
                                        },
                                        "reading_order_index": 1,
                                        "pdf_block_role": "table_like",
                                        "linked_caption_text": "Table 1. Translation quality and training cost.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Table 1. Translation quality and training cost.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [96.0, 330.0, 420.0, 352.0]}
                                            ]
                                        },
                                        "reading_order_index": 2,
                                        "pdf_block_role": "caption",
                                        "caption_for_source_anchor": "pdf://page/1#p1-tbl1",
                                    },
                                    parse_confidence=0.94,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="67676767-6767-4676-8676-676767676767",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-review-artifact-group",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={
                "pdf_profile": {
                    "pdf_kind": "text_pdf",
                    "layout_risk": "medium",
                    "recovery_lane": "academic_paper",
                },
                "pdf_page_evidence": {
                    "pdf_pages": [
                        {
                            "page_number": 1,
                            "page_layout_risk": "high",
                            "page_layout_reasons": ["academic_first_page_asymmetric"],
                            "layout_suspect": False,
                            "role_counts": {"table_like": 1, "caption": 1, "body": 0},
                            "recovery_flags": [],
                        }
                    ]
                },
            },
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            job_runs=[parse_artifacts.job_run],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

        chapter_id = parse_artifacts.chapters[0].id
        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            issue = next(
                artifact_issue
                for artifact_issue in review_artifacts.issues
                if artifact_issue.issue_type == "ARTIFACT_GROUP_RECOVERY_REQUIRED"
            )
            action = next(action for action in review_artifacts.actions if action.issue_id == issue.id)

        self.assertEqual(issue.root_cause_layer, RootCauseLayer.STRUCTURE)
        self.assertEqual(issue.severity, Severity.LOW)
        self.assertFalse(issue.blocking)
        self.assertEqual(issue.evidence_json["captioned_artifact_count"], 1)
        self.assertEqual(issue.evidence_json["grouped_artifact_count"], 0)
        self.assertEqual(issue.evidence_json["missing_group_context_count"], 1)
        self.assertEqual(issue.evidence_json["missing_group_context_page_numbers"], [1])
        self.assertEqual(
            issue.evidence_json["review_policy"],
            "academic_paper_captioned_artifact_missing_group_context",
        )
        self.assertEqual(action.action_type, ActionType.REPARSE_CHAPTER)
        self.assertEqual(action.scope_type, JobScopeType.CHAPTER)

    def test_pdf_structure_refresh_updates_caption_links_and_group_context_in_place(self) -> None:
        class _OldStubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Academic Table Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Academic Table Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="Model  BLEU  Cost",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-tbl1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "body",
                                    },
                                    parse_confidence=0.91,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Table 1. Translation quality and training cost.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "caption",
                                    },
                                    parse_confidence=0.92,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="Table 1 reports the translation quality and training cost trade-off.",
                                    source_path="pdf://page/1",
                                    ordinal=3,
                                    anchor="p1-body1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "body",
                                    },
                                    parse_confidence=0.96,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.TABLE.value,
                                    text="Instances allocated to\nClassifier F\nID = 357\n97.73 (± 1.76)",
                                    source_path="pdf://page/1",
                                    ordinal=4,
                                    anchor="p1-stale1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "table_like",
                                    },
                                    parse_confidence=0.9,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        class _NewStubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Academic Table Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Academic Table Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.TABLE.value,
                                    text="Model  BLEU  Cost\nTransformer  27.5  1.0x10^19",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-tbl1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "table_like",
                                        "linked_caption_text": "Table 1. Translation quality and training cost.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                        "artifact_group_context_source_anchors": ["pdf://page/1#p1-body1"],
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Table 1. Translation quality and training cost.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "caption",
                                        "caption_for_source_anchor": "pdf://page/1#p1-tbl1",
                                    },
                                    parse_confidence=0.95,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="Table 1 reports the translation quality and training cost trade-off.",
                                    source_path="pdf://page/1",
                                    ordinal=3,
                                    anchor="p1-body1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "body",
                                        "artifact_group_source_anchor": "pdf://page/1#p1-tbl1",
                                        "artifact_group_role": "table",
                                    },
                                    parse_confidence=0.98,
                                ),
                            ],
                            metadata={
                                "source_page_start": 1,
                                "source_page_end": 1,
                                "pdf_layout_risk": "medium",
                            },
                        )
                    ],
                    metadata={
                        "pdf_page_evidence": {
                            "pdf_pages": [
                                {
                                    "page_number": 1,
                                    "page_layout_risk": "high",
                                    "page_layout_reasons": ["academic_first_page_asymmetric"],
                                }
                            ]
                        }
                    },
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "structure-refresh.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            now = datetime.now(timezone.utc)
            document = Document(
                id="78787878-7878-4787-8787-787878787878",
                source_type=SourceType.PDF_TEXT,
                file_fingerprint="fingerprint-structure-refresh",
                source_path=str(pdf_path),
                status=DocumentStatus.INGESTED,
                metadata_json={
                    "pdf_profile": {
                        "pdf_kind": "text_pdf",
                        "layout_risk": "medium",
                        "recovery_lane": "academic_paper",
                    }
                },
                created_at=now,
                updated_at=now,
            )
            parse_artifacts = ParseService(pdf_parser=_OldStubPdfParser()).parse(document, str(pdf_path))
            artifacts = BootstrapArtifacts(
                document=parse_artifacts.document,
                chapters=parse_artifacts.chapters,
                blocks=parse_artifacts.blocks,
                sentences=[],
                job_runs=[parse_artifacts.job_run],
            )

            with self.session_factory() as session:
                BootstrapRepository(session).save(artifacts)
                session.commit()

                refresh_artifacts = PdfStructureRefreshService(
                    session,
                    BootstrapRepository(session),
                    parse_service=ParseService(pdf_parser=_NewStubPdfParser()),
                ).refresh_document(parse_artifacts.document.id, chapter_ids=[parse_artifacts.chapters[0].id])
                session.commit()

                bundle = BootstrapRepository(session).load_document_bundle(parse_artifacts.document.id)
                chapter_bundle = bundle.chapters[0]
                table_block = next(block for block in chapter_bundle.blocks if block.source_anchor.endswith("p1-tbl1"))
                caption_block = next(block for block in chapter_bundle.blocks if block.source_anchor.endswith("p1-cap1"))
                context_block = next(block for block in chapter_bundle.blocks if block.source_anchor.endswith("p1-body1"))
                stale_block = session.scalar(
                    select(Block).where(Block.source_anchor == "pdf://page/1#p1-stale1")
                )

        self.assertEqual(refresh_artifacts.matched_chapter_count, 1)
        self.assertEqual(refresh_artifacts.refreshed_block_count, 3)
        self.assertEqual(refresh_artifacts.skipped_block_count, 1)
        self.assertEqual(table_block.block_type, BlockType.TABLE)
        self.assertEqual(table_block.protected_policy, ProtectedPolicy.PROTECT)
        self.assertIn("Transformer  27.5  1.0x10^19", table_block.source_text)
        self.assertEqual(table_block.source_span_json["linked_caption_block_id"], caption_block.id)
        self.assertEqual(table_block.source_span_json["artifact_group_context_block_ids"], [context_block.id])
        self.assertEqual(caption_block.source_span_json["caption_for_block_id"], table_block.id)
        self.assertEqual(context_block.source_span_json["artifact_group_block_id"], table_block.id)
        self.assertNotIn("pdf://page/1#p1-stale1", {block.source_anchor for block in chapter_bundle.blocks})
        self.assertIsNotNone(stale_block)
        self.assertEqual(stale_block.status, ArtifactStatus.INVALIDATED)
        self.assertIn("pdf_page_evidence", bundle.document.metadata_json)
        self.assertEqual(
            bundle.document.metadata_json["pdf_structure_refresh"]["refreshed_block_count"],
            3,
        )
        self.assertEqual(
            bundle.document.metadata_json["pdf_structure_refresh"]["invalidated_block_count"],
            1,
        )

    def test_pdf_structure_refresh_persists_split_trailing_prose_fragments_on_code_blocks(self) -> None:
        class _OldStubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Prompting Chapter",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-002",
                            href="pdf://page/1",
                            title="Prompting Chapter",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.CODE.value,
                                    text=(
                                        "{\n"
                                        '"trends": [\n'
                                        '{"trend_name": "AI-Powered Personalization"}\n'
                                        "]\n"
                                        "} This structured format ensures that the data is machine-readable."
                                    ),
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-b1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "code_like",
                                    },
                                    parse_confidence=0.91,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        class _NewStubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Prompting Chapter",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-002",
                            href="pdf://page/1",
                            title="Prompting Chapter",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.CODE.value,
                                    text='{\n"trends": [\n{"trend_name": "AI-Powered Personalization"}\n]\n}',
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-b1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "code_like",
                                    },
                                    parse_confidence=0.95,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="This structured format ensures that the data is machine-readable.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-b1-trailing-prose",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "body",
                                        "pdf_mixed_code_prose_split": "trailing_prose_suffix",
                                        "pdf_split_source_anchor_base": "pdf://page/1#p1-b1",
                                    },
                                    parse_confidence=0.95,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "structure-refresh-split-fragment.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            now = datetime.now(timezone.utc)
            document = Document(
                id="90909090-9090-4090-8090-909090909090",
                source_type=SourceType.PDF_TEXT,
                file_fingerprint="fingerprint-structure-refresh-split-fragment",
                source_path=str(pdf_path),
                status=DocumentStatus.INGESTED,
                metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "medium"}},
                created_at=now,
                updated_at=now,
            )
            parse_artifacts = ParseService(pdf_parser=_OldStubPdfParser()).parse(document, str(pdf_path))
            artifacts = BootstrapArtifacts(
                document=parse_artifacts.document,
                chapters=parse_artifacts.chapters,
                blocks=parse_artifacts.blocks,
                sentences=[],
                job_runs=[parse_artifacts.job_run],
            )

            with self.session_factory() as session:
                BootstrapRepository(session).save(artifacts)
                session.commit()

                refresh_artifacts = PdfStructureRefreshService(
                    session,
                    BootstrapRepository(session),
                    parse_service=ParseService(pdf_parser=_NewStubPdfParser()),
                ).refresh_document(parse_artifacts.document.id, chapter_ids=[parse_artifacts.chapters[0].id])
                session.commit()

                bundle = BootstrapRepository(session).load_document_bundle(parse_artifacts.document.id)
                code_block = bundle.chapters[0].blocks[0]

        self.assertEqual(refresh_artifacts.refreshed_block_count, 1)
        self.assertEqual(code_block.block_type, BlockType.CODE)
        self.assertNotIn("This structured format ensures", code_block.source_text)
        fragments = list(code_block.source_span_json.get("refresh_split_render_fragments") or [])
        self.assertEqual(len(fragments), 1)
        self.assertEqual(fragments[0]["split_kind"], "trailing_prose_suffix")
        self.assertIn("This structured format ensures", fragments[0]["source_text"])

    def test_pdf_structure_refresh_updates_stale_code_block_to_refreshed_paragraph_text(self) -> None:
        class _OldStubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Parallelization",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-003",
                            href="pdf://page/1",
                            title="Parallelization",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.CODE.value,
                                    text="What: Many agentic workflows involve multiple sub-tasks that must be completed to",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-b1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "body",
                                    },
                                    parse_confidence=0.91,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        class _NewStubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Parallelization",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-003",
                            href="pdf://page/1",
                            title="Parallelization",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text=(
                                        "What: Many agentic workflows involve multiple sub-tasks that must be completed to\n"
                                        "achieve a final goal. A purely sequential execution is often inefficient and slow."
                                    ),
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-b1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "body",
                                    },
                                    parse_confidence=0.97,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "structure-refresh-demote-code.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            now = datetime.now(timezone.utc)
            document = Document(
                id="91919191-9191-4191-8191-919191919191",
                source_type=SourceType.PDF_TEXT,
                file_fingerprint="fingerprint-structure-refresh-demote-code",
                source_path=str(pdf_path),
                status=DocumentStatus.INGESTED,
                metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "medium"}},
                created_at=now,
                updated_at=now,
            )
            parse_artifacts = ParseService(pdf_parser=_OldStubPdfParser()).parse(document, str(pdf_path))
            artifacts = BootstrapArtifacts(
                document=parse_artifacts.document,
                chapters=parse_artifacts.chapters,
                blocks=parse_artifacts.blocks,
                sentences=[],
                job_runs=[parse_artifacts.job_run],
            )

            with self.session_factory() as session:
                BootstrapRepository(session).save(artifacts)
                session.commit()

                refresh_artifacts = PdfStructureRefreshService(
                    session,
                    BootstrapRepository(session),
                    parse_service=ParseService(pdf_parser=_NewStubPdfParser()),
                ).refresh_document(parse_artifacts.document.id, chapter_ids=[parse_artifacts.chapters[0].id])
                session.commit()

                bundle = BootstrapRepository(session).load_document_bundle(parse_artifacts.document.id)
                refreshed_block = bundle.chapters[0].blocks[0]

        self.assertEqual(refresh_artifacts.refreshed_block_count, 1)
        self.assertEqual(refreshed_block.block_type, BlockType.PARAGRAPH)
        self.assertIn("achieve a final goal", refreshed_block.source_text)
        self.assertEqual(refreshed_block.protected_policy, ProtectedPolicy.TRANSLATE)

    def test_reparse_chapter_followup_refreshes_structure_and_resolves_artifact_group_issue(self) -> None:
        class _OldStubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Academic Table Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Academic Table Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.TABLE.value,
                                    text="Model  BLEU  Cost\nTransformer  27.5  1.0x10^19",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-tbl1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "table_like",
                                        "linked_caption_text": "Table 1. Translation quality and training cost.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Table 1. Translation quality and training cost.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "caption",
                                        "caption_for_source_anchor": "pdf://page/1#p1-tbl1",
                                    },
                                    parse_confidence=0.94,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="Table 1 reports the translation quality and training cost trade-off.",
                                    source_path="pdf://page/1",
                                    ordinal=3,
                                    anchor="p1-body1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "body",
                                    },
                                    parse_confidence=0.93,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        class _NewStubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Academic Table Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Academic Table Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.TABLE.value,
                                    text="Model  BLEU  Cost\nTransformer  27.5  1.0x10^19",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-tbl1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "table_like",
                                        "linked_caption_text": "Table 1. Translation quality and training cost.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                        "artifact_group_context_source_anchors": ["pdf://page/1#p1-body1"],
                                    },
                                    parse_confidence=0.98,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Table 1. Translation quality and training cost.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "caption",
                                        "caption_for_source_anchor": "pdf://page/1#p1-tbl1",
                                    },
                                    parse_confidence=0.95,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="Table 1 reports the translation quality and training cost trade-off.",
                                    source_path="pdf://page/1",
                                    ordinal=3,
                                    anchor="p1-body1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "body",
                                        "artifact_group_source_anchor": "pdf://page/1#p1-tbl1",
                                        "artifact_group_role": "table",
                                    },
                                    parse_confidence=0.96,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "reparse-chapter.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            now = datetime.now(timezone.utc)
            document = Document(
                id="79797979-7979-4797-8797-797979797979",
                source_type=SourceType.PDF_TEXT,
                file_fingerprint="fingerprint-reparse-chapter-refresh",
                source_path=str(pdf_path),
                status=DocumentStatus.INGESTED,
                metadata_json={
                    "pdf_profile": {
                        "pdf_kind": "text_pdf",
                        "layout_risk": "medium",
                        "recovery_lane": "academic_paper",
                    },
                    "pdf_page_evidence": {
                        "pdf_pages": [
                            {
                                "page_number": 1,
                                "page_layout_risk": "high",
                                "page_layout_reasons": ["academic_first_page_asymmetric"],
                                "layout_suspect": False,
                                "role_counts": {"table_like": 1, "caption": 1, "body": 1},
                                "recovery_flags": [],
                            }
                        ]
                    },
                },
                created_at=now,
                updated_at=now,
            )
            parse_artifacts = ParseService(pdf_parser=_OldStubPdfParser()).parse(document, str(pdf_path))
            artifacts = BootstrapArtifacts(
                document=parse_artifacts.document,
                chapters=parse_artifacts.chapters,
                blocks=parse_artifacts.blocks,
                sentences=[],
                job_runs=[parse_artifacts.job_run],
            )

            with self.session_factory() as session:
                BootstrapRepository(session).save(artifacts)
                session.commit()

                review_service = ReviewService(ReviewRepository(session))
                review_artifacts = review_service.review_chapter(parse_artifacts.chapters[0].id)
                issue = next(
                    artifact_issue
                    for artifact_issue in review_artifacts.issues
                    if artifact_issue.issue_type == "ARTIFACT_GROUP_RECOVERY_REQUIRED"
                )
                action = next(action for action in review_artifacts.actions if action.issue_id == issue.id)
                action_execution = IssueActionExecutor(OpsRepository(session)).execute(action.id)

                rerun_execution = RerunService(
                    OpsRepository(session),
                    TranslationService(TranslationRepository(session)),
                    review_service,
                    TargetedRebuildService(session, BootstrapRepository(session)),
                    RealignService(OpsRepository(session)),
                    PdfStructureRefreshService(
                        session,
                        BootstrapRepository(session),
                        parse_service=ParseService(pdf_parser=_NewStubPdfParser()),
                    ),
                ).execute(action_execution.rerun_plan)
                session.commit()

                final_review = ReviewService(ReviewRepository(session)).review_chapter(parse_artifacts.chapters[0].id)
                refreshed_bundle = BootstrapRepository(session).load_document_bundle(parse_artifacts.document.id)
                table_block = next(block for block in refreshed_bundle.chapters[0].blocks if block.source_anchor.endswith("p1-tbl1"))
                context_block = next(block for block in refreshed_bundle.chapters[0].blocks if block.source_anchor.endswith("p1-body1"))

        self.assertEqual(len(action_execution.invalidations), 0)
        self.assertIsNotNone(rerun_execution.structure_refresh_artifacts)
        self.assertEqual(rerun_execution.translation_run_ids, [])
        self.assertTrue(rerun_execution.issue_resolved)
        self.assertEqual(table_block.source_span_json["artifact_group_context_block_ids"], [context_block.id])
        self.assertFalse(
            any(issue.issue_type == "ARTIFACT_GROUP_RECOVERY_REQUIRED" for issue in final_review.issues)
        )

    def test_orphaned_pdf_footnotes_create_structure_review_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "footnotes.pdf"
            _write_footnote_pdf(pdf_path)
            artifacts = BootstrapOrchestrator().bootstrap_document(pdf_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

        chapter_id = artifacts.chapters[0].id
        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)

            footnote_issue = next(
                issue
                for issue in review_artifacts.issues
                if issue.issue_type == "FOOTNOTE_RECOVERY_REQUIRED"
            )
            footnote_action = next(
                action for action in review_artifacts.actions if action.issue_id == footnote_issue.id
            )

            self.assertEqual(footnote_issue.root_cause_layer.value, "structure")
            self.assertEqual(footnote_issue.severity.value, "medium")
            self.assertFalse(footnote_issue.blocking)
            self.assertEqual(footnote_issue.evidence_json["orphaned_footnote_count"], 1)
            self.assertEqual(footnote_issue.evidence_json["orphaned_footnote_labels"], ["2"])
            self.assertEqual(footnote_action.action_type, ActionType.REPARSE_CHAPTER)

    def test_academic_paper_uncaptioned_pdf_images_create_advisory_structure_issue(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Academic Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Academic Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "pdf_block_role": "image",
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                    },
                                    parse_confidence=0.95,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="12121212-1212-4212-8212-121212121212",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-review-image-caption-academic",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={
                "pdf_profile": {
                    "pdf_kind": "text_pdf",
                    "layout_risk": "low",
                    "recovery_lane": "academic_paper",
                }
            },
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            job_runs=[parse_artifacts.job_run],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

        chapter_id = parse_artifacts.chapters[0].id
        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)

            image_issue = next(
                issue
                for issue in review_artifacts.issues
                if issue.issue_type == "IMAGE_CAPTION_RECOVERY_REQUIRED"
            )
            image_action = next(
                action for action in review_artifacts.actions if action.issue_id == image_issue.id
            )

            self.assertEqual(image_issue.root_cause_layer.value, "structure")
            self.assertEqual(image_issue.severity.value, "medium")
            self.assertFalse(image_issue.blocking)
            self.assertEqual(image_issue.evidence_json["image_count"], 1)
            self.assertEqual(image_issue.evidence_json["caption_linked_count"], 0)
            self.assertEqual(image_issue.evidence_json["uncaptioned_image_count"], 1)
            self.assertEqual(image_issue.evidence_json["uncaptioned_page_numbers"], [1])
            self.assertEqual(
                image_issue.evidence_json["review_policy"],
                "academic_paper_uncaptioned_images",
            )
            self.assertEqual(image_action.action_type, ActionType.REPARSE_CHAPTER)
            self.assertEqual(image_action.scope_type, JobScopeType.CHAPTER)

    def test_plain_pdf_images_without_caption_context_do_not_create_structure_issue(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "pdf_block_role": "image",
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                    },
                                    parse_confidence=0.95,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="13131313-1313-4313-8313-131313131313",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-review-image-caption-plain",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            job_runs=[parse_artifacts.job_run],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

        chapter_id = parse_artifacts.chapters[0].id
        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            self.assertFalse(
                any(
                    issue.issue_type == "IMAGE_CAPTION_RECOVERY_REQUIRED"
                    for issue in review_artifacts.issues
                )
            )

    def test_image_caption_recovery_issue_drives_worklist_queue(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Academic Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Academic Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "pdf_block_role": "image",
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                    },
                                    parse_confidence=0.95,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="17171717-1717-4717-8717-171717171717",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-review-image-caption-worklist",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={
                "pdf_profile": {
                    "pdf_kind": "text_pdf",
                    "layout_risk": "low",
                    "recovery_lane": "academic_paper",
                }
            },
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            job_runs=[parse_artifacts.job_run],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
            ReviewService(ReviewRepository(session)).review_chapter(parse_artifacts.chapters[0].id)
            session.commit()

            workflow_service = DocumentWorkflowService(session)
            worklist = workflow_service.get_document_chapter_worklist(document.id)
            self.assertEqual(len(worklist.entries), 1)
            self.assertEqual(worklist.entries[0].queue_priority, "high")
            self.assertEqual(worklist.entries[0].queue_driver, "pdf_image_caption_gap")
            self.assertTrue(worklist.entries[0].owner_ready)
            self.assertEqual(
                worklist.entries[0].owner_ready_reason,
                "pdf_image_caption_issue_detected",
            )
            self.assertEqual(
                worklist.entries[0].dominant_issue_type,
                "IMAGE_CAPTION_RECOVERY_REQUIRED",
            )

            detail = workflow_service.get_document_chapter_worklist_detail(
                document.id,
                parse_artifacts.chapters[0].id,
            )
            self.assertIsNotNone(detail.queue_entry)
            self.assertEqual(detail.queue_entry.queue_priority, "high")
            self.assertEqual(detail.queue_entry.queue_driver, "pdf_image_caption_gap")
            self.assertEqual(
                detail.queue_entry.owner_ready_reason,
                "pdf_image_caption_issue_detected",
            )


class PdfDocumentImagePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _build_scanned_bootstrap_artifacts(self) -> BootstrapArtifacts:
        class _StubIngestService:
            def __init__(self, document: Document, job_run: JobRun):
                self._document = document
                self._job_run = job_run

            def ingest(self, _file_path):
                return self._document, self._job_run

        class _StubOcrPdfParser:
            def parse(self, _file_path, profile=None):
                self.profile = profile
                return ParsedDocument(
                    title="Scanned Sample",
                    author="OCR Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-scan-chapter-001",
                            href="pdf://page/12",
                            title="Scanned Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="Scanned prose survives OCR and should enter translation packets.",
                                    source_path="pdf://page/12",
                                    ordinal=1,
                                    anchor="p12-body1",
                                    metadata={
                                        "source_page_start": 12,
                                        "source_page_end": 12,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 12, "bbox": [72.0, 120.0, 520.0, 178.0]}
                                            ]
                                        },
                                        "reading_order_index": 1,
                                        "pdf_page_family": "body",
                                        "pdf_block_role": "body",
                                        "artifact_group_source_anchor": "pdf://page/12#p12-img1",
                                    },
                                    parse_confidence=0.88,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CODE.value,
                                    text="uv run python -m book_agent.cli",
                                    source_path="pdf://page/12",
                                    ordinal=2,
                                    anchor="p12-code1",
                                    metadata={
                                        "source_page_start": 12,
                                        "source_page_end": 12,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 12, "bbox": [72.0, 192.0, 520.0, 236.0]}
                                            ]
                                        },
                                        "reading_order_index": 2,
                                        "pdf_page_family": "body",
                                        "pdf_block_role": "code_like",
                                        "protected_artifact": True,
                                    },
                                    parse_confidence=0.81,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/12",
                                    ordinal=3,
                                    anchor="p12-img1",
                                    metadata={
                                        "source_page_start": 12,
                                        "source_page_end": 12,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 12, "bbox": [84.0, 252.0, 332.0, 424.0]}
                                            ]
                                        },
                                        "reading_order_index": 3,
                                        "pdf_page_family": "body",
                                        "pdf_block_role": "image",
                                        "image_type": "scanned_figure",
                                        "image_ext": "png",
                                        "image_width_px": 640,
                                        "image_height_px": 480,
                                        "linked_caption_text": "Figure 1. Scanned pipeline overview figure.",
                                        "linked_caption_source_anchor": "pdf://page/12#p12-cap1",
                                        "artifact_group_context_source_anchors": [
                                            "pdf://page/12#p12-body1"
                                        ],
                                    },
                                    parse_confidence=0.79,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Figure 1. Scanned pipeline overview figure.",
                                    source_path="pdf://page/12",
                                    ordinal=4,
                                    anchor="p12-cap1",
                                    metadata={
                                        "source_page_start": 12,
                                        "source_page_end": 12,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 12, "bbox": [96.0, 438.0, 332.0, 462.0]}
                                            ]
                                        },
                                        "reading_order_index": 4,
                                        "pdf_page_family": "body",
                                        "pdf_block_role": "caption",
                                        "caption_for_source_anchor": "pdf://page/12#p12-img1",
                                    },
                                    parse_confidence=0.83,
                                ),
                            ],
                            metadata={
                                "source_page_start": 12,
                                "source_page_end": 12,
                                "pdf_section_family": "body",
                                "pdf_layout_risk": "high",
                            },
                        )
                    ],
                    metadata={
                        "pdf_extractor": "surya_ocr",
                        "pdf_page_evidence": {
                            "schema_version": 1,
                            "page_count": 1,
                            "pdf_pages": [
                                {
                                    "page_number": 12,
                                    "page_family": "body",
                                    "page_layout_risk": "high",
                                    "page_layout_reasons": ["ocr_scanned_page"],
                                    "layout_suspect": True,
                                    "role_counts": {"body": 1, "code_like": 1, "image": 1, "caption": 1},
                                }
                            ],
                        },
                    },
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="34343434-3434-4343-8343-343434343434",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-pdf-scan-provenance-test",
            source_path="scan-sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={
                "pdf_profile": {
                    "pdf_kind": "scanned_pdf",
                    "layout_risk": "high",
                    "ocr_required": True,
                    "suspicious_page_numbers": [12],
                }
            },
            created_at=now,
            updated_at=now,
        )
        ingest_job = JobRun(
            id="35353535-3535-4353-8353-353535353535",
            job_type=JobType.INGEST,
            scope_type=JobScopeType.DOCUMENT,
            scope_id=document.id,
            status=JobStatus.SUCCEEDED,
            started_at=now,
            ended_at=now,
            created_at=now,
        )

        return BootstrapPipeline(
            ingest_service=_StubIngestService(document, ingest_job),
            parse_service=ParseService(ocr_pdf_parser=_StubOcrPdfParser()),
        ).run("scan-sample.pdf")

    def test_bootstrap_repository_persists_document_images(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "image_alt": "System overview diagram",
                                    },
                                    parse_confidence=0.97,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="22222222-2222-4222-8222-222222222222",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-image-persist-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document.id)

        self.assertEqual(len(bundle.document_images), 1)
        image = bundle.document_images[0]
        self.assertEqual(image.block_id, bundle.chapters[0].blocks[0].id)
        self.assertEqual(image.page_number, 1)
        self.assertEqual(image.storage_path, f"document-images/{document.id}/{image.block_id}.png")
        self.assertEqual(image.metadata_json["storage_status"], "logical_only")

    def test_pdf_scan_bootstrap_persists_structure_metadata_into_control_plane(self) -> None:
        artifacts = self._build_scanned_bootstrap_artifacts()
        document = artifacts.document

        self.assertEqual(artifacts.document.source_type, SourceType.PDF_SCAN)
        self.assertEqual(artifacts.document.status, DocumentStatus.ACTIVE)
        self.assertEqual(
            artifacts.document.metadata_json["pdf_page_evidence"]["pdf_pages"][0]["page_number"],
            12,
        )
        self.assertEqual(artifacts.chapters[0].risk_level, Severity.CRITICAL)
        self.assertEqual(artifacts.chapters[0].metadata_json["pdf_layout_risk"], "high")
        self.assertEqual(artifacts.chapters[0].metadata_json["pdf_role_counts"]["body"], 1)
        self.assertEqual(artifacts.chapters[0].metadata_json["pdf_role_counts"]["code_like"], 1)
        self.assertEqual(artifacts.chapters[0].metadata_json["pdf_role_counts"]["image"], 1)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document.id)

        chapter_bundle = bundle.chapters[0]
        persisted_blocks = {block.source_anchor: block for block in chapter_bundle.blocks}
        body_block = persisted_blocks["pdf://page/12#p12-body1"]
        code_block = persisted_blocks["pdf://page/12#p12-code1"]
        image_block = persisted_blocks["pdf://page/12#p12-img1"]

        self.assertEqual(chapter_bundle.chapter.risk_level, Severity.CRITICAL)
        self.assertEqual(chapter_bundle.chapter.metadata_json["pdf_layout_risk"], "high")
        self.assertEqual(body_block.source_span_json["pdf_block_role"], "body")
        self.assertEqual(
            body_block.source_span_json["source_bbox_json"]["regions"][0]["page_number"],
            12,
        )
        self.assertEqual(code_block.protected_policy, ProtectedPolicy.PROTECT)
        self.assertTrue(code_block.source_span_json["protected_artifact"])
        self.assertEqual(image_block.source_span_json["pdf_block_role"], "image")
        self.assertEqual(len(bundle.document_images), 1)
        self.assertEqual(bundle.document_images[0].page_number, 12)
        self.assertEqual(
            bundle.document_images[0].bbox_json["regions"][0]["bbox"],
            [84.0, 252.0, 332.0, 424.0],
        )

        sentences_by_id = {sentence.id: sentence for sentence in chapter_bundle.sentences}
        mapped_sentences = [
            sentences_by_id[mapping.sentence_id]
            for mapping in chapter_bundle.packet_sentence_maps
            if mapping.sentence_id in sentences_by_id
        ]
        mapped_block_ids = {sentence.block_id for sentence in mapped_sentences}
        self.assertEqual(len(chapter_bundle.translation_packets), 2)
        self.assertIn(body_block.id, mapped_block_ids)
        self.assertNotIn(code_block.id, mapped_block_ids)
        self.assertNotIn(image_block.id, mapped_block_ids)
        self.assertTrue(any(sentence.translatable for sentence in mapped_sentences if sentence.block_id == body_block.id))

    def test_pdf_scan_review_package_export_uses_shared_chapter_bundle(self) -> None:
        artifacts = self._build_scanned_bootstrap_artifacts()

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                BootstrapRepository(session).save(artifacts)
                session.commit()

                workflow = DocumentWorkflowService(session, export_root=outdir)
                translate_result = workflow.translate_document(artifacts.document.id)
                review_result = workflow.review_document(artifacts.document.id)
                session.commit()

                chapter_id = artifacts.chapters[0].id
                chapter_bundle = workflow.export_repository.load_chapter_bundle(chapter_id)
                export_result = workflow.export_document(artifacts.document.id, ExportType.REVIEW_PACKAGE)
                session.commit()

            review_package_path = Path(export_result.chapter_results[0].file_path)
            review_package = json.loads(review_package_path.read_text(encoding="utf-8"))

        self.assertEqual(translate_result.translated_packet_count, len(chapter_bundle.packets))
        self.assertIn(
            review_result.chapter_results[0].status,
            {ChapterStatus.REVIEW_REQUIRED.value, ChapterStatus.QA_CHECKED.value},
        )
        self.assertEqual(chapter_bundle.document.source_type, SourceType.PDF_SCAN)
        self.assertEqual(len(chapter_bundle.translation_runs), len(chapter_bundle.packets))
        self.assertEqual(len(chapter_bundle.document_images), 1)
        self.assertEqual(review_package["pdf_page_evidence"]["pdf_pages"][0]["page_number"], 12)
        self.assertEqual(review_package["pdf_page_evidence"]["pdf_pages"][0]["page_layout_risk"], "high")
        self.assertEqual(review_package["pdf_image_evidence"]["image_count"], 1)
        self.assertEqual(review_package["pdf_image_evidence"]["caption_linked_count"], 1)
        self.assertEqual(
            review_package["pdf_image_evidence"]["images"][0]["image_type"],
            "scanned_figure",
        )

    def test_pdf_scan_minimal_workflow_exports_bilingual_html_when_layout_is_locally_anchored(self) -> None:
        artifacts = self._build_scanned_bootstrap_artifacts()

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                BootstrapRepository(session).save(artifacts)
                session.commit()

                workflow = DocumentWorkflowService(session, export_root=outdir)
                translate_result = workflow.translate_document(artifacts.document.id)
                review_result = workflow.review_document(artifacts.document.id)
                export_result = workflow.export_document(artifacts.document.id, ExportType.BILINGUAL_HTML)
                session.commit()

                chapter = session.get(Chapter, artifacts.chapters[0].id)
                bilingual_path = Path(export_result.chapter_results[0].file_path)
                manifest_path = Path(export_result.chapter_results[0].manifest_path or "")

                self.assertEqual(translate_result.translated_packet_count, 2)
                self.assertEqual(review_result.chapter_results[0].status, ChapterStatus.QA_CHECKED.value)
                self.assertIsNotNone(chapter)
                self.assertEqual(chapter.status, ChapterStatus.EXPORTED)
                self.assertTrue(bilingual_path.exists())
                self.assertTrue(manifest_path.exists())

    def test_pdf_scan_final_export_gate_fails_closed_on_layout_validation(self) -> None:
        artifacts = self._build_scanned_bootstrap_artifacts()

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            workflow = DocumentWorkflowService(session)
            workflow.translate_document(artifacts.document.id)
            workflow.review_document(artifacts.document.id)
            chapter = session.get(Chapter, artifacts.chapters[0].id)
            assert chapter is not None
            chapter.status = ChapterStatus.QA_CHECKED
            session.commit()

            chapter_id = artifacts.chapters[0].id
            image_block_id = next(
                block.id
                for block in workflow.export_repository.load_chapter_bundle(chapter_id).blocks
                if (block.source_span_json or {}).get("pdf_block_role") == "image"
            )
            service = ExportService(ExportRepository(session))
            with patch.object(
                ExportService,
                "_render_blocks_for_chapter",
                autospec=True,
                return_value=[
                    MergedRenderBlock(
                        block_id=image_block_id,
                        chapter_id=chapter_id,
                        block_type=BlockType.IMAGE.value,
                        render_mode="image_anchor_with_translated_caption",
                        artifact_kind="figure",
                        title=None,
                        source_text="[Image]",
                        target_text=None,
                        source_metadata={},
                        source_sentence_ids=[],
                        target_segment_ids=[],
                        is_expected_source_only=True,
                        notice="图片锚点保留",
                    )
                ],
            ):
                with self.assertRaisesRegex(ExportGateError, "layout validation issues") as exc_info:
                    service.assert_chapter_exportable(chapter_id, ExportType.BILINGUAL_HTML)

            issue = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.chapter_id == chapter_id,
                    ReviewIssue.issue_type == "LAYOUT_VALIDATION_FAILURE",
                )
            ).one()
            action = session.scalars(select(IssueAction).where(IssueAction.issue_id == issue.id)).one()

        exc = exc_info.exception
        self.assertEqual(exc.chapter_id, chapter_id)
        self.assertEqual(exc.issue_ids, [issue.id])
        self.assertEqual(len(exc.followup_actions), 1)
        self.assertEqual(exc.followup_actions[0].action_type, ActionType.REPARSE_CHAPTER.value)
        self.assertEqual(issue.evidence_json["reason"], "export_layout_validation")
        self.assertEqual(issue.evidence_json["layout_issue_codes"], ["FIGURE_ASSET_MISSING"])
        self.assertEqual(action.action_type, ActionType.REPARSE_CHAPTER)
        self.assertEqual(action.scope_type, JobScopeType.CHAPTER)
        self.assertEqual(action.scope_id, chapter_id)

    def test_export_payloads_include_pdf_image_evidence(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "image_alt": "System overview diagram",
                                    },
                                    parse_confidence=0.97,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="33333333-3333-4333-8333-333333333333",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-image-export-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            review_package = export_service._build_review_package(chapter_bundle)
            self.assertEqual(review_package["pdf_image_evidence"]["image_count"], 1)
            self.assertEqual(review_package["pdf_image_evidence"]["caption_linked_count"], 0)
            self.assertEqual(
                review_package["pdf_image_evidence"]["images"][0]["image_type"],
                "embedded_image",
            )

            document_bundle = export_repository.load_document_bundle(document.id)
            merged_manifest = export_service._build_merged_document_manifest(document_bundle, Path("/tmp/merged.html"))
            self.assertEqual(merged_manifest["pdf_image_summary"]["image_count"], 1)
            self.assertEqual(merged_manifest["pdf_image_summary"]["caption_linked_count"], 0)
            self.assertEqual(
                merged_manifest["pdf_image_summary"]["chapter_image_counts"][parse_artifacts.chapters[0].id],
                1,
            )

    def test_export_prefers_persisted_document_image_assets_when_available(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "image_alt": "System overview diagram",
                                    },
                                    parse_confidence=0.97,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            materialized_asset = Path(tmpdir) / "materialized-image.png"
            materialized_asset.write_bytes(b"not-a-real-png-but-good-enough-for-copy")
            output_dir = Path(tmpdir) / "exports"

            now = datetime.now(timezone.utc)
            document = Document(
                id="44444444-4444-4444-8444-444444444444",
                source_type=SourceType.PDF_TEXT,
                file_fingerprint="fingerprint-image-asset-test",
                source_path=str(Path(tmpdir) / "missing-source.pdf"),
                status=DocumentStatus.INGESTED,
                metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
                created_at=now,
                updated_at=now,
            )
            parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
            parse_artifacts.document_images[0].storage_path = str(materialized_asset)
            parse_artifacts.document_images[0].metadata_json = {
                **(parse_artifacts.document_images[0].metadata_json or {}),
                "storage_status": "materialized",
            }
            artifacts = BootstrapArtifacts(
                document=parse_artifacts.document,
                chapters=parse_artifacts.chapters,
                blocks=parse_artifacts.blocks,
                sentences=[],
                document_images=parse_artifacts.document_images,
            )

            with self.session_factory() as session:
                BootstrapRepository(session).save(artifacts)
                session.commit()

                export_repository = ExportRepository(session)
                export_service = ExportService(export_repository, output_root=output_dir)
                chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
                asset_map = export_service._export_epub_assets_for_chapter_bundle(chapter_bundle, output_dir)

            block_id = parse_artifacts.blocks[0].id
            self.assertIn(block_id, asset_map)
            copied_asset = output_dir / asset_map[block_id]
            self.assertTrue(copied_asset.exists())
            self.assertEqual(copied_asset.read_bytes(), materialized_asset.read_bytes())

    def test_pdf_export_refreshes_stale_materialized_images_with_fresh_crop(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-image-refresh-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1280,
                                        "image_height_px": 960,
                                        "image_alt": "System overview diagram",
                                    },
                                    parse_confidence=0.97,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        class _FakePixmap:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload

            def save(self, path: str) -> None:
                Path(path).write_bytes(self.payload)

        class _FakePage:
            def get_pixmap(self, clip=None, alpha=False, matrix=None):
                return _FakePixmap(b"fresh-hires-pdf-crop" if matrix is not None else b"stale-crop")

        class _FakeDocument:
            page_count = 1

            def load_page(self, index: int):
                self.last_index = index
                return _FakePage()

            def get_image_rects(self, _xref: int):
                return []

            def extract_image(self, _xref: int):
                return {}

            def close(self) -> None:
                return None

        class _FakeRect:
            def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
                self.x0 = x0
                self.y0 = y0
                self.x1 = x1
                self.y1 = y1
                self.width = x1 - x0
                self.height = y1 - y0

        class _FakeMatrix:
            def __init__(self, sx: float, sy: float) -> None:
                self.sx = sx
                self.sy = sy

        class _FakeFitz:
            @staticmethod
            def open(_path: str):
                return _FakeDocument()

            @staticmethod
            def Rect(x0: float, y0: float, x1: float, y1: float):
                return _FakeRect(x0, y0, x1, y1)

            @staticmethod
            def Matrix(sx: float, sy: float):
                return _FakeMatrix(sx, sy)

        original_fitz = sys.modules.get("fitz")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                sys.modules["fitz"] = _FakeFitz()
                source_pdf = Path(tmpdir) / "source.pdf"
                source_pdf.write_bytes(b"%PDF-1.4 fake")
                stale_asset = Path(tmpdir) / "stale-image.png"
                stale_asset.write_bytes(b"old-lowres-image")
                output_dir = Path(tmpdir) / "exports"

                now = datetime.now(timezone.utc)
                document = Document(
                    id="55555555-5555-4555-8555-555555555555",
                    source_type=SourceType.PDF_TEXT,
                    file_fingerprint="fingerprint-image-refresh-test",
                    source_path=str(source_pdf),
                    status=DocumentStatus.INGESTED,
                    metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
                    created_at=now,
                    updated_at=now,
                )
                parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
                parse_artifacts.document_images[0].storage_path = str(stale_asset)
                parse_artifacts.document_images[0].metadata_json = {
                    **(parse_artifacts.document_images[0].metadata_json or {}),
                    "storage_status": "materialized",
                    "materialized_via": "pdf_export_crop",
                    "materialized_version": 1,
                }
                artifacts = BootstrapArtifacts(
                    document=parse_artifacts.document,
                    chapters=parse_artifacts.chapters,
                    blocks=parse_artifacts.blocks,
                    sentences=[],
                    document_images=parse_artifacts.document_images,
                )

                with self.session_factory() as session:
                    BootstrapRepository(session).save(artifacts)
                    session.commit()

                    export_repository = ExportRepository(session)
                    export_service = ExportService(export_repository, output_root=output_dir)
                    chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
                    asset_map = export_service._export_epub_assets_for_chapter_bundle(chapter_bundle, output_dir)
                    session.commit()

                    reloaded_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)

                block_id = parse_artifacts.blocks[0].id
                self.assertEqual(asset_map[block_id], f"assets/pdf-images/{block_id}.png")
                refreshed_asset = output_dir / asset_map[block_id]
                self.assertEqual(refreshed_asset.read_bytes(), b"fresh-hires-pdf-crop")
                self.assertEqual(Path(reloaded_bundle.document_images[0].storage_path).read_bytes(), b"fresh-hires-pdf-crop")
                self.assertEqual(reloaded_bundle.document_images[0].metadata_json["materialized_version"], 3)
                self.assertGreaterEqual(reloaded_bundle.document_images[0].metadata_json["materialized_render_scale"], 2.0)
        finally:
            if original_fitz is None:
                sys.modules.pop("fitz", None)
            else:
                sys.modules["fitz"] = original_fitz

    def test_pdf_export_prefers_original_embedded_image_bytes_when_available(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-original-asset-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "jpg",
                                        "image_width_px": 1200,
                                        "image_height_px": 900,
                                        "image_alt": "Embedded original figure",
                                    },
                                    parse_confidence=0.97,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        class _FakeRect:
            def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
                self.x0 = x0
                self.y0 = y0
                self.x1 = x1
                self.y1 = y1
                self.width = x1 - x0
                self.height = y1 - y0

        class _FakePage:
            def get_images(self, full=False):
                self.last_get_images_full = full
                return [(17,)]

            def get_image_rects(self, xref: int):
                assert xref == 17
                return [_FakeRect(72.0, 240.0, 420.0, 520.0)]

            def get_pixmap(self, clip=None, alpha=False, matrix=None):
                raise AssertionError("embedded original image should avoid pixmap fallback")

        class _FakeDocument:
            page_count = 1

            def load_page(self, index: int):
                self.last_index = index
                return _FakePage()

            def extract_image(self, xref: int):
                assert xref == 17
                return {"image": b"original-jpeg-binary", "ext": "jpg"}

            def close(self) -> None:
                return None

        class _FakeFitz:
            @staticmethod
            def open(_path: str):
                return _FakeDocument()

            @staticmethod
            def Rect(x0: float, y0: float, x1: float, y1: float):
                return _FakeRect(x0, y0, x1, y1)

        original_fitz = sys.modules.get("fitz")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                sys.modules["fitz"] = _FakeFitz()
                source_pdf = Path(tmpdir) / "source.pdf"
                source_pdf.write_bytes(b"%PDF-1.4 fake")
                output_dir = Path(tmpdir) / "exports"

                now = datetime.now(timezone.utc)
                document = Document(
                    id="67676767-6767-4676-8676-676767676767",
                    source_type=SourceType.PDF_TEXT,
                    file_fingerprint="fingerprint-original-image-test",
                    source_path=str(source_pdf),
                    status=DocumentStatus.INGESTED,
                    metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
                    created_at=now,
                    updated_at=now,
                )
                parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, str(source_pdf))
                artifacts = BootstrapArtifacts(
                    document=parse_artifacts.document,
                    chapters=parse_artifacts.chapters,
                    blocks=parse_artifacts.blocks,
                    sentences=[],
                    document_images=parse_artifacts.document_images,
                )

                with self.session_factory() as session:
                    BootstrapRepository(session).save(artifacts)
                    session.commit()

                    export_repository = ExportRepository(session)
                    export_service = ExportService(export_repository, output_root=output_dir)
                    chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
                    asset_map = export_service._export_epub_assets_for_chapter_bundle(chapter_bundle, output_dir)
                    session.commit()

                    reloaded_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)

                block_id = parse_artifacts.blocks[0].id
                self.assertEqual(asset_map[block_id], f"assets/pdf-images/{block_id}.jpg")
                exported_asset = output_dir / asset_map[block_id]
                self.assertTrue(exported_asset.exists())
                self.assertEqual(exported_asset.read_bytes(), b"original-jpeg-binary")

                reloaded_image = reloaded_bundle.document_images[0]
                self.assertTrue(Path(reloaded_image.storage_path).exists())
                self.assertTrue(str(reloaded_image.storage_path).endswith(".jpg"))
                self.assertEqual(Path(reloaded_image.storage_path).read_bytes(), b"original-jpeg-binary")
                self.assertEqual(reloaded_image.metadata_json["materialized_via"], "pdf_original_image")
                self.assertEqual(
                    reloaded_image.metadata_json["original_asset_availability"],
                    "single_embedded_image",
                )
                self.assertEqual(reloaded_image.metadata_json["materialized_version"], 3)
                self.assertNotIn("materialized_render_scale", reloaded_image.metadata_json)
        finally:
            if original_fitz is None:
                sys.modules.pop("fitz", None)
            else:
                sys.modules["fitz"] = original_fitz

    def test_pdf_export_materializes_document_images_for_reuse(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "image_alt": "System overview diagram",
                                    },
                                    parse_confidence=0.97,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        class _FakePixmap:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload

            def save(self, path: str) -> None:
                Path(path).write_bytes(self.payload)

        class _FakePage:
            def get_images(self, full=False):
                return []

            def get_image_rects(self, _xref: int):
                return []

            def get_drawings(self):
                return [object()]

            def get_pixmap(self, clip=None, alpha=False):
                return _FakePixmap(b"fake-pdf-crop")

        class _FakeDocument:
            page_count = 1

            def load_page(self, index: int):
                self.last_index = index
                return _FakePage()

            def extract_image(self, _xref: int):
                return {}

            def close(self) -> None:
                return None

        class _FakeRect:
            def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
                self.x0 = x0
                self.y0 = y0
                self.x1 = x1
                self.y1 = y1
                self.width = x1 - x0
                self.height = y1 - y0

        class _FakeFitz:
            @staticmethod
            def open(_path: str):
                return _FakeDocument()

            @staticmethod
            def Rect(x0: float, y0: float, x1: float, y1: float):
                return _FakeRect(x0, y0, x1, y1)

        original_fitz = sys.modules.get("fitz")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                sys.modules["fitz"] = _FakeFitz()
                source_pdf = Path(tmpdir) / "source.pdf"
                source_pdf.write_bytes(b"%PDF-1.4 fake")
                output_dir = Path(tmpdir) / "exports"

                now = datetime.now(timezone.utc)
                document = Document(
                    id="66666666-6666-4666-8666-666666666666",
                    source_type=SourceType.PDF_TEXT,
                    file_fingerprint="fingerprint-image-materialize-test",
                    source_path=str(source_pdf),
                    status=DocumentStatus.INGESTED,
                    metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
                    created_at=now,
                    updated_at=now,
                )
                parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, str(source_pdf))
                artifacts = BootstrapArtifacts(
                    document=parse_artifacts.document,
                    chapters=parse_artifacts.chapters,
                    blocks=parse_artifacts.blocks,
                    sentences=[],
                    document_images=parse_artifacts.document_images,
                )

                with self.session_factory() as session:
                    BootstrapRepository(session).save(artifacts)
                    session.commit()

                    export_repository = ExportRepository(session)
                    export_service = ExportService(export_repository, output_root=output_dir)
                    chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
                    asset_map = export_service._export_epub_assets_for_chapter_bundle(chapter_bundle, output_dir)
                    session.commit()

                    reloaded_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)

                block_id = parse_artifacts.blocks[0].id
                self.assertEqual(asset_map[block_id], f"assets/pdf-images/{block_id}.png")
                exported_asset = output_dir / asset_map[block_id]
                self.assertTrue(exported_asset.exists())
                self.assertEqual(exported_asset.read_bytes(), b"fake-pdf-crop")

                materialized_path = Path(reloaded_bundle.document_images[0].storage_path)
                self.assertTrue(materialized_path.exists())
                self.assertEqual(materialized_path.read_bytes(), b"fake-pdf-crop")
                self.assertEqual(reloaded_bundle.document_images[0].metadata_json["storage_status"], "materialized")
                self.assertEqual(
                    reloaded_bundle.document_images[0].metadata_json["materialized_via"],
                    "pdf_export_crop",
                )
                self.assertEqual(
                    reloaded_bundle.document_images[0].metadata_json["original_asset_availability"],
                    "vector_only_page_artifact",
                )
        finally:
            if original_fitz is None:
                sys.modules.pop("fitz", None)
            else:
                sys.modules["fitz"] = original_fitz

    def test_export_merges_linked_pdf_image_caption_into_single_render_block(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "linked_caption_text": "Figure 1. System overview diagram.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Figure 1. System overview diagram.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "caption_for_source_anchor": "pdf://page/1#p1-img1",
                                    },
                                    parse_confidence=0.94,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="88888888-8888-4888-8888-888888888888",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-image-caption-render-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            render_blocks = export_service._render_blocks_for_chapter(chapter_bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_id, parse_artifacts.blocks[0].id)
        self.assertEqual(render_blocks[0].render_mode, "image_anchor_with_translated_caption")
        self.assertEqual(render_blocks[0].artifact_kind, "image")
        self.assertEqual(render_blocks[0].source_text, "Figure 1. System overview diagram.")

    def test_image_anchor_html_renders_image_before_translated_caption(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-image-order-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "linked_caption_text": "Figure 1. System overview diagram.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Figure 1. System overview diagram.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "caption_for_source_anchor": "pdf://page/1#p1-img1",
                                    },
                                    parse_confidence=0.94,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="89898989-8989-4898-8898-898989898989",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-image-caption-html-order-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            render_blocks = export_service._render_blocks_for_chapter(chapter_bundle)

        render_blocks[0].target_text = "图 1. 系统总览图。"
        html_block = export_service._render_block_html(
            render_blocks[0],
            {render_blocks[0].block_id: "assets/pdf-images/p1-img1.png"},
        )
        self.assertIn("artifact-source-caption", html_block)
        self.assertLess(html_block.index("<img"), html_block.index("图 1. 系统总览图。"))

    def test_export_does_not_promote_bullet_paragraph_with_parenthetical_label_to_code(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Bullet Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-bullet-paragraph-001",
                            href="pdf://page/1",
                            title="Bullet Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="• GPT (Byte Pair Encoding): GPT tokenizers use a technique called byte pair",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-bullet1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [123.0, 696.0, 504.0, 710.0]}
                                            ]
                                        },
                                        "reading_order_index": 1,
                                        "pdf_page_family": "body",
                                    },
                                    parse_confidence=0.96,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="8a8a8a8a-8a8a-48a8-88a8-8a8a8a8a8a8a",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-bullet-code-promotion-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            render_blocks = export_service._render_blocks_for_chapter(chapter_bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_type, BlockType.PARAGRAPH.value)
        self.assertIsNone(render_blocks[0].artifact_kind)
        self.assertNotEqual(render_blocks[0].render_mode, "source_artifact_full_width")
        self.assertNotEqual(render_blocks[0].notice, "代码保持原样")

    def test_export_treats_equation_blocks_as_equation_artifacts(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Equation Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-equation-001",
                            href="pdf://page/1",
                            title="Equation Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.EQUATION.value,
                                    text="p(y|x) = softmax(W_h h_t + b)",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-eq1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "equation",
                                    },
                                    parse_confidence=0.98,
                                )
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="12121212-1212-4212-8212-121212121212",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-equation-render-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            render_blocks = export_service._render_blocks_for_chapter(chapter_bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].render_mode, "source_artifact_full_width")
        self.assertEqual(render_blocks[0].artifact_kind, "equation")
        self.assertEqual(render_blocks[0].notice, "公式保持原样")

    def test_export_merges_linked_pdf_table_caption_into_single_render_block(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Table Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-table-001",
                            href="pdf://page/1",
                            title="Table Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.TABLE.value,
                                    text="Model  BLEU  Cost\nTransformer  27.5  1.0x10^19",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-tbl1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "table_like",
                                        "linked_caption_text": "Table 1. Translation quality and training cost.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Table 1. Translation quality and training cost.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "caption_for_source_anchor": "pdf://page/1#p1-tbl1",
                                    },
                                    parse_confidence=0.94,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="34343434-3434-4434-8434-343434343434",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-table-caption-render-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            render_blocks = export_service._render_blocks_for_chapter(chapter_bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_id, parse_artifacts.blocks[0].id)
        self.assertEqual(render_blocks[0].render_mode, "translated_wrapper_with_preserved_artifact")
        self.assertEqual(render_blocks[0].artifact_kind, "table")
        self.assertEqual(render_blocks[0].source_text, "Model  BLEU  Cost\nTransformer  27.5  1.0x10^19")
        self.assertEqual(
            render_blocks[0].source_metadata["linked_caption_text"],
            "Table 1. Translation quality and training cost.",
        )

    def test_table_artifact_html_uses_semantic_table_markup_when_structure_is_recoverable(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Table Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-table-html-001",
                            href="pdf://page/1",
                            title="Table Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.TABLE.value,
                                    text="Model  BLEU  Cost\nTransformer  27.5  1.0x10^19",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-tbl1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "table_like",
                                        "linked_caption_text": "Table 1. Translation quality and training cost.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Table 1. Translation quality and training cost.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "caption_for_source_anchor": "pdf://page/1#p1-tbl1",
                                    },
                                    parse_confidence=0.94,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="35353535-3535-4353-8535-353535353535",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-table-html-render-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            render_blocks = export_service._render_blocks_for_chapter(chapter_bundle)

        render_blocks[0].target_text = "表 1. 翻译质量与训练成本。"
        html_block = export_service._render_block_html(render_blocks[0])
        self.assertIn("<table class='artifact-table'>", html_block)
        self.assertIn("Model</th>", html_block)
        self.assertIn("Transformer</td>", html_block)

    def test_export_merges_linked_pdf_table_caption_and_adjacent_context_into_single_render_block(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Table Context Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-table-context-001",
                            href="pdf://page/1",
                            title="Table Context Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.TABLE.value,
                                    text="Model  BLEU  Cost\nTransformer  27.5  1.0x10^19",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-tbl1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [84.0, 244.0, 420.0, 314.0]}
                                            ]
                                        },
                                        "reading_order_index": 1,
                                        "pdf_block_role": "table_like",
                                        "linked_caption_text": "Table 1. Translation quality and training cost.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Table 1. Translation quality and training cost.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [96.0, 330.0, 420.0, 352.0]}
                                            ]
                                        },
                                        "reading_order_index": 2,
                                        "pdf_block_role": "caption",
                                        "caption_for_source_anchor": "pdf://page/1#p1-tbl1",
                                    },
                                    parse_confidence=0.94,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.PARAGRAPH.value,
                                    text="Table 1 reports the main translation results and shows that the Transformer improves BLEU at a higher training cost.",
                                    source_path="pdf://page/1",
                                    ordinal=3,
                                    anchor="p1-body1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [96.0, 366.0, 504.0, 410.0]}
                                            ]
                                        },
                                        "reading_order_index": 3,
                                        "pdf_block_role": "body",
                                    },
                                    parse_confidence=0.96,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="45454545-4545-4454-8454-454545454545",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-table-context-render-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={
                "pdf_profile": {
                    "pdf_kind": "text_pdf",
                    "layout_risk": "medium",
                    "recovery_lane": "academic_paper",
                }
            },
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            render_blocks = export_service._render_blocks_for_chapter(chapter_bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_id, parse_artifacts.blocks[0].id)
        self.assertEqual(render_blocks[0].render_mode, "translated_wrapper_with_preserved_artifact")
        self.assertEqual(
            render_blocks[0].source_metadata["artifact_group_context_block_ids"],
            [parse_artifacts.blocks[2].id],
        )

    def test_export_merges_linked_pdf_equation_caption_into_single_render_block(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Equation Caption Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-equation-caption-001",
                            href="pdf://page/1",
                            title="Equation Caption Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.EQUATION.value,
                                    text="p(y|x) = softmax(W_h h_t + b)",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-eq1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "pdf_block_role": "equation",
                                        "linked_caption_text": "Equation 1. Decoder token distribution.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.98,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Equation 1. Decoder token distribution.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "caption_for_source_anchor": "pdf://page/1#p1-eq1",
                                    },
                                    parse_confidence=0.94,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="56565656-5656-4565-8565-565656565656",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-equation-caption-render-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            render_blocks = export_service._render_blocks_for_chapter(chapter_bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_id, parse_artifacts.blocks[0].id)
        self.assertEqual(render_blocks[0].render_mode, "translated_wrapper_with_preserved_artifact")
        self.assertEqual(render_blocks[0].artifact_kind, "equation")
        self.assertEqual(
            render_blocks[0].source_metadata["linked_caption_text"],
            "Equation 1. Decoder token distribution.",
        )

    def test_export_suppresses_uncaptioned_inline_image_between_code_fragments(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Code Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-code-image-001",
                            href="pdf://page/1",
                            title="Code Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.CODE.value,
                                    text="def build_agent(prompt: str) -> str:\n    planner = load_planner()",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-code1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 180.0, 520.0, 252.0]}
                                            ]
                                        },
                                        "reading_order_index": 1,
                                        "pdf_page_family": "body",
                                        "pdf_block_role": "code_like",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [88.0, 258.0, 168.0, 314.0]}
                                            ]
                                        },
                                        "reading_order_index": 2,
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 320,
                                        "image_height_px": 180,
                                        "pdf_page_family": "body",
                                        "pdf_block_role": "image",
                                    },
                                    parse_confidence=0.82,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CODE.value,
                                    text="    return planner.run(prompt)",
                                    source_path="pdf://page/1",
                                    ordinal=3,
                                    anchor="p1-code2",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 320.0, 520.0, 364.0]}
                                            ]
                                        },
                                        "reading_order_index": 3,
                                        "pdf_page_family": "body",
                                        "pdf_block_role": "code_like",
                                    },
                                    parse_confidence=0.96,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={},
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="67676767-6767-4676-8676-676767676767",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-code-image-merge-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            render_blocks = export_service._render_blocks_for_chapter(chapter_bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].artifact_kind, "code")
        self.assertIn("def build_agent", render_blocks[0].source_text)
        self.assertIn("return planner.run", render_blocks[0].source_text)
        self.assertIn("export_inline_image_between_code_suppressed", render_blocks[0].source_metadata["recovery_flags"])

    def test_export_and_summary_report_linked_caption_counts(self) -> None:
        class _StubPdfParser:
            def parse(self, _file_path, profile=None):
                return ParsedDocument(
                    title="Image Sample",
                    author="Test Author",
                    language="en",
                    chapters=[
                        ParsedChapter(
                            chapter_id="pdf-chapter-001",
                            href="pdf://page/1",
                            title="Image Sample",
                            blocks=[
                                ParsedBlock(
                                    block_type=BlockType.IMAGE.value,
                                    text="[Image]",
                                    source_path="pdf://page/1",
                                    ordinal=1,
                                    anchor="p1-img1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "source_bbox_json": {
                                            "regions": [
                                                {"page_number": 1, "bbox": [72.0, 240.0, 420.0, 520.0]}
                                            ]
                                        },
                                        "image_type": "embedded_image",
                                        "image_ext": "png",
                                        "image_width_px": 1024,
                                        "image_height_px": 768,
                                        "linked_caption_text": "Figure 1. System overview diagram.",
                                        "linked_caption_source_anchor": "pdf://page/1#p1-cap1",
                                    },
                                    parse_confidence=0.97,
                                ),
                                ParsedBlock(
                                    block_type=BlockType.CAPTION.value,
                                    text="Figure 1. System overview diagram.",
                                    source_path="pdf://page/1",
                                    ordinal=2,
                                    anchor="p1-cap1",
                                    metadata={
                                        "source_page_start": 1,
                                        "source_page_end": 1,
                                        "caption_for_source_anchor": "pdf://page/1#p1-img1",
                                    },
                                    parse_confidence=0.94,
                                ),
                            ],
                            metadata={"source_page_start": 1, "source_page_end": 1},
                        )
                    ],
                    metadata={
                        "pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"},
                        "pdf_page_evidence": {
                            "schema_version": 1,
                            "page_count": 1,
                            "pdf_pages": [{"page_number": 1, "page_family": "body"}],
                        },
                    },
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="99999999-9999-4999-8999-999999999999",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-image-caption-summary-test",
            source_path="sample.pdf",
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_StubPdfParser()).parse(document, "sample.pdf")
        artifacts = BootstrapArtifacts(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=[],
            document_images=parse_artifacts.document_images,
        )

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

            export_repository = ExportRepository(session)
            export_service = ExportService(export_repository)
            chapter_bundle = export_repository.load_chapter_bundle(parse_artifacts.chapters[0].id)
            review_package = export_service._build_review_package(chapter_bundle)
            self.assertEqual(review_package["pdf_image_evidence"]["caption_linked_count"], 1)
            self.assertEqual(review_package["pdf_image_evidence"]["uncaptioned_image_count"], 0)
            self.assertTrue(review_package["pdf_image_evidence"]["images"][0]["caption_linked"])
            self.assertEqual(
                review_package["pdf_image_evidence"]["images"][0]["linked_caption_text"],
                "Figure 1. System overview diagram.",
            )

            document_bundle = export_repository.load_document_bundle(document.id)
            merged_manifest = export_service._build_merged_document_manifest(document_bundle, Path("/tmp/merged.html"))
            self.assertEqual(merged_manifest["pdf_image_summary"]["caption_linked_count"], 1)
            self.assertEqual(merged_manifest["pdf_image_summary"]["uncaptioned_image_count"], 0)

            workflow_summary = DocumentWorkflowService(session).get_document_summary(document.id)
            self.assertEqual(workflow_summary.pdf_image_summary["caption_linked_count"], 1)
            self.assertEqual(workflow_summary.pdf_image_summary["uncaptioned_image_count"], 0)
            self.assertEqual(workflow_summary.chapters[0].pdf_image_summary["image_count"], 1)
            self.assertEqual(workflow_summary.chapters[0].pdf_image_summary["caption_linked_count"], 1)
            self.assertEqual(workflow_summary.chapters[0].pdf_image_summary["uncaptioned_image_count"], 0)
