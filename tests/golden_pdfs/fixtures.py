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
