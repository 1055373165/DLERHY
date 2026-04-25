"""Deterministic PDF fixture generators for the M1 regression set.

Each function returns raw PDF bytes. Tests may choose to write these to
disk and feed them through the full parser, or (cheaper) call the parser
stages directly. The set covers five canonical failure-mode classes from
the PDF v2 spec §3.1:

  1. clean_book        — baseline single-column English prose.
  2. two_column_paper  — multi-column reading-order (failure 2).
  3. code_block_book   — code blocks must stay un-translated (failure 3).
  4. reference_list    — bibliography must NOT trip the sanity gate
                         (false-positive class we fixed in M1.2).
  5. corrupted_font    — PUA text must trip the sanity gate (failure 1).
"""

from __future__ import annotations

import io


def _require_fitz():  # pragma: no cover - import-guard
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Golden PDF fixtures need PyMuPDF. Run `uv sync` in dev."
        ) from exc
    return fitz


def make_clean_book() -> bytes:
    """Two-page single-column English prose book. Baseline."""
    fitz = _require_fitz()
    doc = fitz.open()
    prose_a = (
        "Chapter 1. Origin of Species\n\n"
        "This chapter examines how natural variation compounds under "
        "selection pressure over many generations. The reader is asked "
        "to hold two timescales in mind: the short horizon of an "
        "individual life and the long horizon of population change. "
        "Across the examples that follow the author returns to this "
        "dual view as an organizing lens, noting how observations at "
        "one scale can mislead when transposed to the other.\n"
    )
    prose_b = (
        "Chapter 2. Methods and Observations\n\n"
        "The methods described here are intentionally simple. They "
        "rely on careful counting and on repeated observation under "
        "controlled conditions. Readers familiar with modern "
        "statistics may find the presentation elementary; this is by "
        "design, to keep the reasoning transparent from first "
        "principles rather than buried behind machinery.\n"
    )
    page1 = doc.new_page(width=612, height=792)
    page1.insert_textbox(
        fitz.Rect(72, 72, 540, 720), prose_a, fontsize=11, fontname="helv"
    )
    page2 = doc.new_page(width=612, height=792)
    page2.insert_textbox(
        fitz.Rect(72, 72, 540, 720), prose_b, fontsize=11, fontname="helv"
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_two_column_paper() -> bytes:
    """Single-page two-column academic layout.

    Left and right column text is interleaved vertically so a naive
    top-down sort would visibly scramble reading order.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # Left column 72..288, Right column 324..540, 4 paragraphs per column.
    left_blocks = [
        (
            "LEFT-{i}: This paragraph belongs to the left column and "
            "contains enough prose to qualify as a column-candidate "
            "block. It must appear before any RIGHT paragraph in the "
            "recovered reading order.".format(i=i)
        )
        for i in (1, 2, 3, 4)
    ]
    right_blocks = [
        (
            "RIGHT-{i}: This paragraph belongs to the right column. "
            "It must appear only after all LEFT paragraphs in the "
            "recovered reading order, never interleaved."
            .format(i=i)
        )
        for i in (1, 2, 3, 4)
    ]
    y = 90
    dy = 160
    for left, right in zip(left_blocks, right_blocks):
        page.insert_textbox(fitz.Rect(72, y, 288, y + dy - 10), left, fontsize=10)
        # Slight y offset on the right so naive y-sort would interleave.
        page.insert_textbox(fitz.Rect(324, y + 5, 540, y + dy - 5), right, fontsize=10)
        y += dy
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_code_block_book() -> bytes:
    """Book page with a clearly monospace code block embedded in prose."""
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    intro = (
        "Chapter 3. Worked Example\n\n"
        "The following Python snippet computes the running sum of a "
        "sequence using an accumulator pattern. Read it line by line "
        "and trace the state of `total` on each iteration.\n"
    )
    code = (
        "def running_sum(values):\n"
        "    total = 0\n"
        "    for v in values:\n"
        "        total += v\n"
        "        yield total\n"
    )
    epilogue = (
        "The generator above preserves the intermediate state so that "
        "downstream consumers can see each partial sum without "
        "holding the whole sequence in memory."
    )
    page.insert_textbox(fitz.Rect(72, 72, 540, 200), intro, fontsize=11, fontname="helv")
    page.insert_textbox(fitz.Rect(72, 210, 540, 360), code, fontsize=10, fontname="cour")
    page.insert_textbox(fitz.Rect(72, 380, 540, 500), epilogue, fontsize=11, fontname="helv")
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_reference_list() -> bytes:
    """Single-page bibliography with proper-noun-heavy entries.

    Regression guard for the sanity gate false-positive we fixed in
    M1.2: such pages must STAY ok=True.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    body = (
        "References\n\n"
        "1. Devlin, J., Chang, M., Lee, K., & Toutanova, K. (2019). "
        "BERT: Pre-training of Deep Bidirectional Transformers for "
        "Language Understanding. NAACL.\n"
        "2. Brown, T., Mann, B., Ryder, N., et al. (2020). Language "
        "Models are Few-Shot Learners. NeurIPS.\n"
        "3. Vaswani, A., Shazeer, N., Parmar, N., et al. (2017). "
        "Attention Is All You Need. NeurIPS.\n"
        "4. LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. "
        "Nature, 521(7553), 436-444.\n"
        "5. Radford, A., Narasimhan, K., Salimans, T., & Sutskever, I. "
        "(2018). Improving Language Understanding by Generative "
        "Pre-Training. OpenAI Technical Report.\n"
        "6. Hinton, G., Deng, L., Yu, D., et al. (2012). Deep Neural "
        "Networks for Acoustic Modeling in Speech Recognition. IEEE "
        "Signal Processing Magazine, 29(6), 82-97.\n"
    )
    page.insert_textbox(fitz.Rect(72, 72, 540, 720), body, fontsize=10, fontname="helv")
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_three_column_newsletter() -> bytes:
    """Single-page three-column layout with vertically interleaved blocks.

    Tests that column-major ordering generalizes beyond two columns.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    cols = [(40, 220), (236, 416), (432, 572)]  # x ranges for cols 1/2/3
    for col_idx, (x0, x1) in enumerate(cols, start=1):
        for row in range(1, 4):
            text = (
                f"COL{col_idx}-R{row}: A column-major reading order test "
                "block that is long enough to qualify as a column "
                "candidate under the multi-column signature heuristic."
            )
            y0 = 80 + (row - 1) * 200
            y1 = y0 + 180
            page.insert_textbox(
                fitz.Rect(x0, y0, x1, y1), text, fontsize=10, fontname="helv"
            )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_figure_with_caption() -> bytes:
    """Page with a paragraph, an image placeholder, and a Figure caption.

    The recovery pipeline should pair the caption with the figure
    artifact via spatial proximity.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(
        fitz.Rect(72, 72, 540, 200),
        (
            "The introductory paragraph sets the stage for the figure "
            "below, which depicts the data flow through the pipeline."
        ),
        fontsize=11,
    )
    # Draw a placeholder rectangle to act as a "figure" region.
    page.draw_rect(fitz.Rect(150, 220, 462, 460), color=(0.5, 0.5, 0.5))
    page.insert_textbox(
        fitz.Rect(72, 470, 540, 510),
        "Figure 1.1: Data flow from ingestion through translation.",
        fontsize=10,
    )
    page.insert_textbox(
        fitz.Rect(72, 530, 540, 660),
        (
            "The discussion continues after the figure, referring "
            "back to it as Figure 1.1 to anchor the cross reference."
        ),
        fontsize=11,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_equation_block_book() -> bytes:
    """Page with a display equation surrounded by prose.

    The equation block should be classified as `equation` and inherit
    `translatability=translate_none`. Recovery's `_looks_like_equation`
    heuristic looks for `=`, `≤`, etc.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(
        fitz.Rect(72, 72, 540, 200),
        (
            "Energy and mass are related by the famous identity stated "
            "below. The reader should be familiar with the constants."
        ),
        fontsize=11,
    )
    page.insert_textbox(
        fitz.Rect(72, 220, 540, 280),
        "E = m c^2 + sum_{i=1}^{n} epsilon_i",
        fontsize=12,
        fontname="cour",
    )
    page.insert_textbox(
        fitz.Rect(72, 300, 540, 420),
        (
            "where epsilon_i denotes the per-particle correction terms. "
            "These are computed from the spectrum of observations."
        ),
        fontsize=11,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_inline_url_paragraph() -> bytes:
    """Page with prose containing inline URLs and DOIs that must survive
    translation verbatim. Tests the broader translatability protocol.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(
        fitz.Rect(72, 72, 540, 720),
        (
            "Further reading is available at https://arxiv.org/abs/1706.03762 "
            "and the source code repository at https://github.com/example/repo. "
            "The DOI 10.1162/neco.1997.9.8.1735 references the original LSTM "
            "paper. Reading these together gives a comprehensive overview."
        ),
        fontsize=11,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_acronym_definition_paper() -> bytes:
    """Page introducing several acronyms via the `Foo Bar (FB)` pattern.

    Terminology miner's definition-pattern boost should surface these
    as candidates regardless of overall frequency.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(
        fitz.Rect(72, 72, 540, 720),
        (
            "Retrieval-Augmented Generation (RAG) combines language models "
            "with a vector store. A Vector Store (VS) holds embeddings of "
            "external documents. The Large Language Model (LLM) consumes "
            "retrieved snippets as additional context. The term Agent "
            "Loop is defined as a repeating cycle of plan, act, and "
            "observe. We call Self-Reflection the process by which the "
            "agent evaluates its own outputs before final commit."
        ),
        fontsize=11,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_repeated_term_doc() -> bytes:
    """Two-page document where the term `attention mechanism` recurs
    enough to be mined without any definition pattern. Smoke test for
    the n-gram frequency path of `terminology_miner`.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    text = (
        "The attention mechanism scales the contribution of each token. "
        "An attention mechanism over keys and values produces context. "
        "Without an attention mechanism, sequences degrade quickly. "
        "Modern systems treat the attention mechanism as a primitive."
    )
    for _ in range(2):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(fitz.Rect(72, 72, 540, 720), text, fontsize=11)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_mixed_clean_and_corrupted() -> bytes:
    """A clean page followed by a "corrupted" page (text inserted as a
    monospace block to keep the bytes valid). Used by sanity-gate
    multi-page tests via the in-memory text path; the PDF itself still
    parses cleanly.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    clean_page = doc.new_page(width=612, height=792)
    clean_page.insert_textbox(
        fitz.Rect(72, 72, 540, 720),
        (
            "Chapter 1. The first page contains clean English prose "
            "covering the introduction to the subject and remaining "
            "comfortably above any sanity threshold."
        ),
        fontsize=11,
    )
    suspicious_page = doc.new_page(width=612, height=792)
    suspicious_page.insert_textbox(
        fitz.Rect(72, 72, 540, 720),
        (
            "Page two carries normal prose intentionally so PyMuPDF "
            "extraction stays valid; the multi-page sanity tests "
            "supply the corrupted text in-memory rather than embedding "
            "exotic font encodings here."
        ),
        fontsize=11,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_cross_page_paragraph() -> bytes:
    """Single paragraph that breaks across two pages.

    Recovery should stitch the two halves together via cross-page
    continuation logic. We just check that BOTH halves end up in the
    final ParsedDocument so no content is dropped.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    half_a = (
        "The first half of this paragraph ends mid sentence and the "
        "reader is expected to continue reading on the next page where "
        "the thought is completed without any indented break in tone or "
        "narrative voice. The recovery pipeline must recognize that the "
        "trailing line lacks a sentence terminator and that the next "
        "page's leading line begins with a lowercase continuation,"
    )
    half_b = (
        "which is a strong signal that the paragraph continues. After "
        "the merge, downstream consumers see one logical unit, and the "
        "translation worker receives the paragraph as a single packet."
    )
    page1 = doc.new_page(width=612, height=792)
    page1.insert_textbox(fitz.Rect(72, 600, 540, 720), half_a, fontsize=11)
    page2 = doc.new_page(width=612, height=792)
    page2.insert_textbox(fitz.Rect(72, 72, 540, 200), half_b, fontsize=11)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_low_density_figure_page() -> bytes:
    """A page consisting almost entirely of a figure with a tiny caption.

    Sanity gate must NOT trip on this: too little text to judge.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(fitz.Rect(72, 72, 540, 700), color=(0.4, 0.4, 0.4))
    page.insert_textbox(
        fitz.Rect(72, 720, 540, 760),
        "Fig. 2.1",
        fontsize=10,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_recurring_header_footer_book() -> bytes:
    """Three pages all carrying the same running header and page number.

    Recovery's repeated-edge-text detection should classify those bands
    as chrome (non-translatable). Pulled out as a fixture so future
    changes to the chrome heuristic have a stable ground truth.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    body_template = (
        "Chapter content for page {n}. The body prose differs across "
        "pages so the recurring header is the only repeating signal."
    )
    for n in range(1, 4):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(
            fitz.Rect(72, 36, 540, 56),
            "RUNNING HEAD: BOOK TITLE",
            fontsize=9,
        )
        page.insert_textbox(
            fitz.Rect(72, 100, 540, 700),
            body_template.format(n=n),
            fontsize=11,
        )
        page.insert_textbox(
            fitz.Rect(72, 740, 540, 760),
            f"— {n} —",
            fontsize=9,
        )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_numbered_section_paper() -> bytes:
    """Single page with numbered academic section headings.

    Exercises the recovery path that promotes lines like `1.1 Introduction`
    into headings — a common academic-paper structural cue.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    sections = [
        ("1 Introduction", "We introduce the topic and motivate the work that follows."),
        ("1.1 Background", "Prior literature has examined related questions in earlier eras."),
        ("1.2 Contributions", "Our contributions are summarized in three points outlined below."),
        ("2 Method", "The method follows a familiar template adapted to our setting."),
        ("2.1 Notation", "We adopt standard notation throughout to keep equations compact."),
    ]
    y = 80
    for heading, body in sections:
        page.insert_textbox(fitz.Rect(72, y, 540, y + 22), heading, fontsize=14)
        page.insert_textbox(fitz.Rect(72, y + 24, 540, y + 100), body, fontsize=11)
        y += 110
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def corrupted_text_sample() -> str:
    """PUA-encoded English sample (text fixture, not a PDF).

    The sanity gate is tested at the text level — a real corrupted-font
    PDF would need custom font embedding machinery that adds no value
    to the regression set. The text alone is enough to verify the gate.
    """
    clean = (
        "Chapter 4. Broken Font Demonstration\n\n"
        "This passage deliberately maps every ASCII letter into a "
        "codepoint in the Unicode Private Use Area so that downstream "
        "text layers appear as glyphs with no standard interpretation. "
        "Any sanity gate worth its name must reject this page and "
        "route it to OCR instead of letting it reach translation."
    )
    mapped: list[str] = []
    for ch in clean:
        if ch.isalpha():
            mapped.append(chr(0xE000 + (ord(ch) & 0xFF)))
        else:
            mapped.append(ch)
    return "".join(mapped)
