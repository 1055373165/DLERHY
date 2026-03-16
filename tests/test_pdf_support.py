# ruff: noqa: E402

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.app.main import create_app
from book_agent.domain.enums import ActionType, BlockType, ChapterStatus, JobScopeType, SourceType
from book_agent.domain.structure.pdf import (
    BasicPdfTextExtractor,
    PDFParser,
    PdfExtraction,
    PdfFileProfiler,
    PdfPage,
    PdfTextBlock,
    _detect_backmatter_cue,
    _infer_appendix_intro_title,
    _infer_appendix_nested_subheading_title,
    _infer_appendix_subheading_title,
    _infer_intro_page_title,
)
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.review import ReviewService
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


class PdfBootstrapPipelineTests(unittest.TestCase):
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

    def test_bootstrap_pipeline_supports_academic_paper_pdf_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-paper.pdf"
            _write_academic_paper_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        profile = result.document.metadata_json["pdf_profile"]
        self.assertEqual(result.document.source_type, SourceType.PDF_TEXT)
        self.assertEqual(profile["layout_risk"], "medium")
        self.assertEqual(profile["recovery_lane"], "academic_paper")
        self.assertTrue(profile["academic_paper_candidate"])
        self.assertEqual(profile["trailing_reference_page_count"], 2)
        self.assertEqual([chapter.metadata_json["pdf_section_family"] for chapter in result.chapters], ["body", "references"])
        self.assertEqual(result.chapters[0].risk_level.value, "high")
        self.assertEqual(result.chapters[1].metadata_json["pdf_section_family"], "references")

    def test_bootstrap_pipeline_recovers_inline_academic_section_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-inline-sections.pdf"
            _write_academic_paper_with_inline_sections_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        heading_texts = [block.source_text for block in result.blocks if block.block_type == BlockType.HEADING]
        self.assertIn("Abstract", heading_texts)
        self.assertIn("1 Introduction", heading_texts)
        self.assertIn("2 Model Architecture", heading_texts)
        self.assertIn("3 Training", heading_texts)
        self.assertIn("4 Results", heading_texts)

        recovered_heading = next(
            block
            for block in result.blocks
            if block.block_type == BlockType.HEADING and block.source_text == "2 Model Architecture"
        )
        self.assertIn("academic_section_heading_recovered", recovered_heading.source_span_json["recovery_flags"])
        self.assertEqual(recovered_heading.source_span_json["pdf_academic_heading_kind"], "numbered")
        self.assertEqual(recovered_heading.source_span_json["pdf_academic_section_level"], 1)

    def test_bootstrap_pipeline_cleans_noisy_inline_academic_section_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "academic-noisy-inline-sections.pdf"
            _write_academic_paper_with_noisy_inline_sections_pdf(pdf_path)

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        heading_texts = [block.source_text for block in result.blocks if block.block_type == BlockType.HEADING]
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

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        heading_texts = [block.source_text for block in result.blocks if block.block_type == BlockType.HEADING]
        self.assertIn("3.2.1 Scaled Dot-Product Attention", heading_texts)
        self.assertIn("3.2.2 Multi-Head Attention", heading_texts)
        self.assertNotIn("3.2.1 Scaled Dot-Pr", heading_texts)
        broken_heading = next(
            block
            for block in result.blocks
            if block.block_type == BlockType.HEADING and block.source_text == "3.2.1 Scaled Dot-Product Attention"
        )
        self.assertIn("academic_section_heading_recovered", broken_heading.source_span_json["recovery_flags"])

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

            result = BootstrapOrchestrator().bootstrap_document(pdf_path)

        body_texts = [
            block.source_text
            for block in result.blocks
            if block.source_span_json.get("pdf_page_family") == "body"
            and block.block_type in {BlockType.HEADING, BlockType.PARAGRAPH}
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
        profile = result.document.metadata_json["pdf_profile"]
        self.assertEqual(profile["layout_risk"], "medium")
        self.assertEqual(profile["recovery_lane"], "academic_paper")
        self.assertGreaterEqual(profile["multi_column_page_count"], 2)

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

            parsed = PDFParser().parse(pdf_path)

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
        self.assertEqual(first_page["role_counts"]["heading"], 1)
        self.assertIn("cross_page_repaired", second_page["recovery_flags"])
        self.assertIn("dehyphenated", second_page["recovery_flags"])


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
        self.assertEqual(payload["current_phase"], "p1_text_pdf_bootstrap")

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

    def test_bootstrap_document_rejects_high_risk_layout_pdf(self) -> None:
        pdf_path = Path(self.tempdir.name) / "two-column.pdf"
        _write_high_risk_two_column_pdf(pdf_path)

        response = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})

        self.assertEqual(response.status_code, 400)
        self.assertIn("layout_risk=high", response.json()["detail"])

    def test_bootstrap_document_accepts_academic_paper_pdf_lane(self) -> None:
        pdf_path = Path(self.tempdir.name) / "academic-paper.pdf"
        _write_academic_paper_pdf(pdf_path)

        response = self.client.post("/v1/documents/bootstrap", json={"source_path": str(pdf_path)})

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["pdf_profile"]["layout_risk"], "medium")
        self.assertEqual(payload["pdf_profile"]["recovery_lane"], "academic_paper")
        self.assertTrue(payload["pdf_profile"]["academic_paper_candidate"])
        self.assertEqual(payload["pdf_profile"]["trailing_reference_page_count"], 2)
        self.assertEqual(len(payload["chapters"]), 2)
        self.assertEqual(payload["chapters"][0]["risk_level"], "high")
        self.assertEqual(payload["pdf_page_evidence"]["pdf_pages"][3]["page_family"], "references")

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

        chapter_id = artifacts.chapters[0].id
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
            artifacts = BootstrapOrchestrator().bootstrap_document(pdf_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
            DocumentWorkflowService(session).translate_document(artifacts.document.id)
            session.commit()

        chapter_id = artifacts.chapters[1].id
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
