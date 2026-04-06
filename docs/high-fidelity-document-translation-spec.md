# High-Fidelity Document Translation — Master Engineering Specification

## 0. Problem & Cognitive Foundation

### Core Problem

The book-agent system's end-user — a reader receiving the translated book — expects a Chinese edition that is visually indistinguishable from the English original except for the language of the text. Today, the system produces readable bilingual markdown and reconstructed HTML/PDF that visibly differ from the source in layout, typography, image placement, page structure, and visual rhythm. This gap makes the output unsuitable for direct delivery as a "published translation." The problem belongs to the book-agent project owner who needs production-grade deliverables now, because the translation pipeline (parse → translate → export) is functionally complete but the last-mile reconstruction destroys the visual fidelity that signals professional quality.

Success looks like this: a reader opens the Chinese PDF side-by-side with the English PDF and cannot identify any difference except the language of the text — same page breaks, same figure positions, same heading sizes, same margins, same visual weight on every page.

### Design North Star

**Modify the source PDF in place; do not reconstruct it from an intermediate representation.**

Every design decision in this specification is judged by a single question: does this change operate on the original PDF canvas, or does it discard the canvas and try to rebuild it? The former preserves fidelity; the latter destroys it.

### Domain Essence

This problem belongs to the domain of **Document-Level Localization with Layout Preservation** — the same domain that professional Desktop Publishing (DTP) teams in the translation industry have worked in for three decades. The core principle is deceptively simple: a document's visual identity is encoded in thousands of micro-decisions (kerning, leading, margin ratios, figure anchoring, whitespace rhythm) that were made by a human typesetter or an authoring tool. No amount of algorithmic reconstruction from extracted text can recover these decisions. The only way to preserve them is to not destroy them in the first place.

The cognitive gap between a senior localization engineer and a junior one is this: the junior sees "text that needs translating." The senior sees a **spatial contract** — every text run occupies a specific rectangle on a specific page, and the translated text must honor that rectangle or explicitly negotiate a controlled violation (font size reduction, line count increase, margin adjustment). The senior never thinks about translation and layout as separate problems; they are one problem with two faces.

### Core Metaphor

The current system treats the source PDF like a house it demolishes to sort the bricks, then tries to rebuild the house from the sorted bricks. The bricks are all there, but the house looks different. The correct approach is **renovation, not reconstruction** — walk through every room, replace only the text on the walls, and leave everything else untouched.

### Non-Goals

- **Re-implementing the PDF parser.** The 20-stage recovery pipeline is excellent for structure extraction. We keep it as-is for translation packet assembly.
- **Translating text embedded in images.** Raster image content (screenshots, photos, diagrams with baked-in text) is not modified. This is a separate, much harder problem (image inpainting + text rendering).
- **Supporting right-to-left or vertical CJK layout.** We target horizontal LTR Chinese (Simplified) only.
- **Pixel-perfect fidelity for scanned/OCR PDFs.** Scanned PDFs have no modifiable text layer; they require a different strategy (overlay approach) that is out of scope for Phase 1.
- **Replacing the existing export pipeline.** The HTML/Markdown/EPUB exports remain for review and bilingual reading. The new PDF-in-place pipeline is an additional export path, not a replacement.

---

## 1. Judgment Rules

These rules are the "cognitive operating system" of this specification. They don't appear in code, but every design decision and implementation choice should be checked against them.

**Rule 1: The Canvas Rule**
> Never discard the source PDF's visual canvas. Every pixel that isn't text should survive into the output unchanged.

When you see a temptation to "extract content, transform it, and render it back," the instinct should not be "how do I render it faithfully" but "why am I leaving the canvas at all?" The entire history of PDF localization tools shows that round-tripping through HTML or any intermediate format always loses visual information — always, without exception. The losses may be subtle (slightly different letter spacing, shifted baselines, rasterized vector text) but they accumulate into a document that "feels wrong" even when no single element is obviously broken.

**Engineering Mapping**: In the PDF-in-place export module, operate exclusively via PyMuPDF's redaction + insertion API on the source PDF object. Never serialize to HTML or any other intermediate format.

**Rule 2: The Spatial Contract Rule**
> Translated text must fit in the original text's bounding box. If it doesn't fit, shrink the font — don't move the box.

The naive instinct when Chinese text is longer than English text is to let it flow to the next line or expand the text area. This destroys page layout downstream — figures shift, page breaks move, headers orphan. Professional typesetters know that a 10% font reduction is invisible to readers, but a shifted figure is immediately noticed. The font is the cheapest variable to sacrifice; position is the most expensive.

**Engineering Mapping**: In the text replacement algorithm, implement a binary-search font-size reduction loop (from original size down to 60% floor) until text fits the target bbox. If it still doesn't fit at 60%, allow controlled line overflow with a logged warning.

**Rule 3: The Font Pairing Rule**
> CJK body text uses a matched-weight CJK font; never fall back to a generic sans-serif.

Readers unconsciously register font weight, x-height, and contrast ratio. English Garamond paired with Chinese SimSun feels "matched" because both are serif with similar stroke contrast. English Helvetica paired with SimSun feels jarring. The font pairing decision is not cosmetic — it's the single largest contributor to whether the translated document "feels professional" or "feels like a machine translation."

**Engineering Mapping**: Maintain a font pairing table (English font name → CJK font family) in the configuration. For each text replacement, look up the source font in the pairing table. Embed the paired CJK font into the output PDF. Default pairs: serif → Noto Serif CJK SC, sans-serif → Noto Sans CJK SC, monospace → Noto Sans Mono CJK SC.

**Rule 4: The Heading Hierarchy Rule**
> Headings, captions, and running headers are the skeleton. If these are wrong, nothing else matters.

Users scan headings first. A heading with wrong font size, wrong weight, or wrong position immediately signals "low quality" regardless of how good the body text looks. Headings also have the highest risk of bbox overflow (they're often short in English but long in Chinese) and the highest visual impact of font reduction.

**Engineering Mapping**: Process headings in a separate pass with tighter tolerances: headings get a 70% font floor (not 60%), and any heading that requires >15% reduction is flagged for human review.

**Rule 5: The Untouchable Artifacts Rule**
> Code blocks, mathematical equations, tables with numeric data, and URLs are never modified by the text replacement engine.

The instinct to "translate everything" is dangerous for artifacts. A code block with translated variable names is broken code. A mathematical equation with translated operators is wrong mathematics. These artifacts should pass through the replacement engine completely untouched — not "detected and skipped" (which implies they enter the engine), but structurally excluded before the engine runs.

**Engineering Mapping**: The block-to-text-run mapping step must tag each PDF text run with a `replace_policy` derived from the block's `protected_policy` in the database. Runs tagged `protect` are never passed to the redaction step.

**Rule 6: The Progressive Fidelity Rule**
> Ship 90% fidelity in week one. Ship 95% in week two. The last 5% takes months and may not matter.

The temptation in document fidelity work is to chase perfection before shipping anything. But the difference between "current markdown export" and "PDF with text replacement" is a 50-percentage-point improvement in perceived quality. The difference between "good text replacement" and "perfect text replacement" is 5 percentage points that most readers won't notice. Ship the large improvement first.

**Engineering Mapping**: Phase 1 targets body text and heading replacement only. Phase 2 adds caption/footnote/header refinement. Phase 3 handles edge cases (rotated text, text on paths, watermarks).

---

## 2. Core Design Decisions

### Decision 1: Direct PDF Text Replacement via Redact-and-Insert

- **Choice**: Use PyMuPDF's `add_redact_annot()` + `apply_redactions()` to remove source text, then `insert_textbox()` to place translated text in the same bbox.
- **Alternatives Considered**: (A) HTML intermediary → Playwright PDF render (current approach); (B) reportlab/fpdf full PDF reconstruction; (C) PDF.js browser-based rendering.
- **Analytical Rationale**: Approach A provably loses layout (acknowledged in current codebase as `not_page_faithful_to_source_pdf`). Approach B requires re-implementing all layout logic. Approach C has no server-side path. Direct manipulation is O(n) in text runs, preserves all non-text elements, and PyMuPDF's redaction API handles font subsetting, transparency, and CFF/TrueType text streams.
- **Cognitive Rationale**: This is the only approach consistent with the Canvas Rule. Every other approach destroys and rebuilds.
- **Cost**: Cannot handle text embedded in raster images. Cannot reflow text across page boundaries. Requires CJK font embedding (adds ~5-15MB to output file size).
- **Reversal Condition**: If the project needs to support scanned PDFs (no text layer) as a primary use case, this approach cannot work and an overlay strategy would be needed.

### Decision 2: Block-Level Mapping (Not Sentence-Level)

- **Choice**: Map translated text to PDF text runs at the **block** level (paragraph/heading), not at the individual sentence level.
- **Alternatives Considered**: Sentence-level replacement (find each sentence's exact position in the PDF and replace individually).
- **Analytical Rationale**: PDF text runs don't align with sentence boundaries. A single paragraph's text may span multiple PDF "blocks" (due to line breaks) or a single PDF block may contain multiple sentences. Block-level mapping gives us a clean bbox to work with (the paragraph's visual rectangle). Sentence-level would require sub-paragraph position detection that PyMuPDF doesn't reliably provide.
- **Cognitive Rationale**: Readers perceive paragraphs, not sentences. If the paragraph as a whole looks right (correct font, correct position, correct density), minor intra-paragraph spacing differences are invisible.
- **Cost**: Cannot apply different styles to different sentences within the same paragraph (e.g., if one sentence was translated differently in a review pass). All sentences in a block get the same formatting.
- **Reversal Condition**: If per-sentence quality indicators need to be visually marked in the output (e.g., low-confidence sentences in a different color), we'd need sentence-level positioning.

### Decision 3: Font-Size Reduction Over Reflow

- **Choice**: When translated text doesn't fit the original bbox, reduce font size progressively (binary search from original to 60% floor) rather than allowing text to reflow beyond the bbox.
- **Alternatives Considered**: (A) Allow overflow and shift subsequent elements; (B) Truncate with ellipsis; (C) Use footnote-style overflow links.
- **Analytical Rationale**: Option A cascades layout changes across the entire page and potentially the entire document. Option B loses content. Option C is non-standard and confusing. Font reduction within a reasonable range (down to ~60%) is visually acceptable — professional translators routinely accept 80-90% scaling for CJK in localized materials.
- **Cognitive Rationale**: Consistent with the Spatial Contract Rule. The bbox is sacred; the font size is negotiable.
- **Cost**: Text readability decreases with reduction. At 60% of a 10pt font (=6pt), text becomes difficult to read. Body text below 7pt should be flagged.
- **Reversal Condition**: If the source document uses very small fonts (< 8pt body text), reduction may be infeasible and an overflow strategy would be needed.

### Decision 4: CJK Font Embedding with Noto Family

- **Choice**: Embed Noto CJK fonts (Serif, Sans, Mono) as the default CJK font family, with a configurable pairing table.
- **Alternatives Considered**: (A) System fonts (SimSun, etc.); (B) Source Han fonts; (C) FandolSong (lightweight).
- **Analytical Rationale**: Noto CJK is freely licensed (OFL), has complete GB2312/GBK/GB18030 coverage, includes all weight variants (Light through Black), and has matched Latin metrics designed for mixed CJK-Latin typesetting. Noto Serif CJK SC Regular is ~17MB (subsettable to ~2-5MB per document).
- **Cognitive Rationale**: Consistent with the Font Pairing Rule. Noto was specifically designed by Google and Adobe for cross-script harmony.
- **Cost**: Larger output files (~5-15MB overhead per embedded font). Subsetting reduces this but adds processing time.
- **Reversal Condition**: If output file size is critical (e.g., mobile delivery), switch to FandolSong (~1MB) with reduced glyph coverage.

### Decision 5: Two-Pass Architecture (Map, Then Replace)

- **Choice**: Separate the pipeline into (1) a mapping pass that pairs each PDF text run with its translation and replacement policy, and (2) a replacement pass that executes all redactions and insertions atomically.
- **Alternatives Considered**: Single-pass (replace as you find each text run).
- **Analytical Rationale**: Two-pass enables validation (check coverage, log unmapped runs, detect conflicts) before any destructive operation. Single-pass is simpler but provides no rollback or dry-run capability.
- **Cognitive Rationale**: The mapping pass is where all "intelligence" lives (text matching, block resolution, policy decisions). The replacement pass is purely mechanical. Separating them means bugs in the intelligence layer don't corrupt PDFs.
- **Cost**: Two passes over the PDF (roughly 2x processing time). For a 200-page book, this is seconds, not minutes.
- **Reversal Condition**: If real-time/streaming export is needed, a single-pass pipeline would be more appropriate.

---

## 3. Technical Architecture

### 3.1 System Overview

The new PDF-in-place export module sits alongside the existing export pipeline as an additional export path. It reads from the same translation database but operates directly on the source PDF file instead of generating HTML.

```
Source PDF ──────────────────────────────────────────────┐
     │                                                   │
     ▼                                                   │
[Existing Parser] → DB (blocks, sentences, translations) │
     │                                                   │
     ▼                                                   ▼
[PDF Text Run Extractor] ← page.get_text("dict") ── [Source PDF via PyMuPDF]
     │
     ▼
[Block-to-Run Mapper] ← DB blocks + source_span_json bbox
     │                    Match text runs to translated blocks
     ▼
[Replacement Plan] ← list of (page, bbox, old_text, new_text, font, size)
     │
     ├─→ [Validation] → coverage report, warnings, dry-run output
     │
     ▼
[PDF Redact & Insert Engine]
     │  1. add_redact_annot() for each mapped run
     │  2. apply_redactions() to remove source text
     │  3. insert_textbox() with CJK translation
     │  4. Embed CJK fonts
     ▼
[Output PDF] ← source PDF with text replaced, everything else intact
```

### 3.2 Core Module Specifications

#### Module A: PDF Text Run Extractor

**Responsibility**: Extract all text runs from the source PDF with their exact page positions, font properties, and content.

**Input**: Source PDF file path (str)
**Output**: `list[PdfTextRun]` ordered by (page_number, y_position, x_position)

```python
@dataclass
class PdfTextRun:
    page_index: int           # 0-indexed
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    text: str                 # exact text content
    font_name: str            # PDF font name (e.g., "TimesNewRomanPSMT")
    font_size: float          # in points
    font_flags: int           # bold=1, italic=2, serif=4, mono=8
    color: tuple[float, ...]  # RGB or CMYK color values
    span_origin: tuple[float, float]  # (x, y) of text baseline origin
    block_index: int          # PyMuPDF block index on page
    line_index: int           # line index within block
    span_index: int           # span index within line
```

**Core Algorithm**:
```
for each page in doc:
    text_dict = page.get_text("dict", sort=True)
    for block in text_dict["blocks"]:
        if block["type"] != 0: continue  # skip image blocks
        for line in block["lines"]:
            for span in line["spans"]:
                yield PdfTextRun(
                    page_index=page.number,
                    bbox=span["bbox"],  # NOT block bbox — span-level precision
                    text=span["text"],
                    font_name=span["font"],
                    font_size=span["size"],
                    font_flags=span["flags"],
                    color=span["color"],
                    span_origin=span["origin"],
                    block_index=block["number"],
                    line_index=line_idx,
                    span_index=span_idx,
                )
```

**Master Note**: Use span-level bbox, not block-level. A block's bbox is the union of all its lines; a span's bbox is the exact rectangle of that text fragment. Span-level precision is essential for font-size-aware replacement and for handling multi-font paragraphs (e.g., bold keywords in regular text).

**Failure Degradation**: If `get_text("dict")` fails on a page (corrupt page object), skip that page and log a warning. The output PDF will retain original text on that page.

**Performance**: O(pages * blocks_per_page * spans_per_block). For a typical 200-page book: ~200 * ~30 * ~5 = 30,000 spans. Extracts in < 2 seconds.

#### Module B: Block-to-Run Mapper

**Responsibility**: Match each DB block (with its translation) to the PDF text runs that contain its source text.

**Input**: `list[PdfTextRun]`, DB connection (blocks table with translations joined)
**Output**: `list[ReplacementPlan]`

```python
@dataclass
class ReplacementPlan:
    page_index: int
    bbox: tuple[float, float, float, float]       # merged bbox of all matched runs
    source_text: str                                # original text (for verification)
    target_text: str                                # translated Chinese text
    replace_policy: str                             # "replace" | "protect" | "skip"
    font_name: str                                  # source font name (for pairing)
    font_size: float                                # source font size
    font_flags: int                                 # bold/italic flags
    color: tuple[float, ...]                        # text color
    block_id: str                                   # DB block ID (for tracing)
    block_type: str                                 # heading, paragraph, code, etc.
    confidence: float                               # match confidence
```

**Core Algorithm**:

```
# Phase 1: Group text runs into page-level paragraphs
page_paragraphs = group_runs_into_paragraphs(all_runs)
  # Merge consecutive runs on same page with overlapping y-range
  # into logical paragraphs (similar to how PDF viewers reflow)

# Phase 2: For each DB block, find matching paragraph(s)
for block in db_blocks:
    db_page = block.source_span_json.source_page_start - 1  # DB is 1-indexed
    db_bbox = block.source_span_json.source_bbox_json.regions[0].bbox
    db_text = normalize(block.source_text)
    
    # Candidate paragraphs: same page, overlapping bbox
    candidates = [p for p in page_paragraphs[db_page]
                  if bbox_overlap_ratio(p.bbox, db_bbox) > 0.3]
    
    # Score by text similarity (normalized Levenshtein)
    best = max(candidates, key=lambda p: text_similarity(p.text, db_text))
    
    if best.score > 0.7:
        yield ReplacementPlan(
            page_index=db_page,
            bbox=best.bbox,
            source_text=best.text,
            target_text=get_translation(block),
            replace_policy=block.protected_policy,
            font_name=best.dominant_font,
            font_size=best.dominant_size,
            ...
        )
    else:
        log_warning(f"Low match for block {block.id}: {best.score}")
```

**Master Note**: Text matching must be fuzzy because the PDF text extraction may differ slightly from the parser's stored `source_text` (different Unicode normalization, ligature expansion, whitespace handling). A 0.7 threshold on normalized text is the right balance — lower catches more but risks false matches; higher misses legitimate variations.

**Failure Degradation**: Unmapped blocks are logged but don't fail the pipeline. The original text remains in the PDF for unmapped areas. Coverage percentage is reported.

#### Module C: PDF Redact & Insert Engine

**Responsibility**: Execute the replacement plan on the source PDF, producing the output PDF with translated text.

**Input**: Source PDF path, `list[ReplacementPlan]`, font configuration
**Output**: Modified PDF saved to output path

**Core Algorithm**:

```
doc = fitz.open(source_pdf_path)

# Step 1: Embed CJK fonts
cjk_serif = fitz.Font("notoserifsc", fontfile="NotoSerifCJKsc-Regular.otf")
cjk_sans  = fitz.Font("notosanssc", fontfile="NotoSansCJKsc-Regular.otf")
cjk_mono  = fitz.Font("notomonosc", fontfile="NotoSansMonoCJKsc-Regular.otf")
font_table = build_font_pairing_table(cjk_serif, cjk_sans, cjk_mono)

# Step 2: Group plans by page for batch processing
plans_by_page = group_by(replacement_plans, key=lambda p: p.page_index)

for page_idx, plans in plans_by_page.items():
    page = doc[page_idx]
    
    # Step 3: Add redaction annotations for all source text areas
    for plan in plans:
        if plan.replace_policy != "replace":
            continue
        page.add_redact_annot(
            quad=fitz.Rect(plan.bbox),
            fill=(1, 1, 1),  # white fill (matches most backgrounds)
        )
    
    # Step 4: Apply all redactions (removes source text, fills with white)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    # CRITICAL: images=NONE preserves all images on the page
    
    # Step 5: Insert translated text
    for plan in plans:
        if plan.replace_policy != "replace":
            continue
        
        cjk_font = font_table.get(plan.font_name, cjk_serif)
        target_size = compute_fitting_font_size(
            text=plan.target_text,
            bbox=plan.bbox,
            font=cjk_font,
            max_size=plan.font_size,
            min_size=plan.font_size * 0.6,  # 60% floor
        )
        
        rect = fitz.Rect(plan.bbox)
        rc = page.insert_textbox(
            rect,
            plan.target_text,
            fontname=cjk_font.name,
            fontfile=cjk_font.buffer,
            fontsize=target_size,
            color=plan.color if len(plan.color) == 3 else (0, 0, 0),
            align=fitz.TEXT_ALIGN_LEFT,  # or JUSTIFY for body text
        )
        
        if rc < 0:  # overflow
            log_warning(f"Text overflow on page {page_idx} block {plan.block_id}")

doc.save(output_path, garbage=4, deflate=True)
doc.close()
```

**Master Note**: The `images=fitz.PDF_REDACT_IMAGE_NONE` parameter is absolutely critical. Without it, `apply_redactions()` will remove any image that overlaps with a redaction area. This single parameter is the difference between preserving all figures and destroying them.

**Failure Degradation**: If font embedding fails (missing font file), fall back to PyMuPDF's built-in CJK font ("china-s"). If a page fails during redaction, skip it and log — partial output is better than no output.

**Performance**: For a 200-page book with ~1000 replaceable blocks: ~3-5 seconds for redaction, ~2-3 seconds for text insertion, ~1-2 seconds for save with garbage collection. Total: < 15 seconds.

#### Module D: Font Size Fitter

**Responsibility**: Find the largest font size that allows the translated text to fit within the target bbox.

**Input**: text (str), bbox (Rect), font (Font), max_size (float), min_size (float)
**Output**: optimal font size (float)

**Core Algorithm**:
```
def compute_fitting_font_size(text, bbox, font, max_size, min_size):
    # Binary search for optimal size
    lo, hi = min_size, max_size
    best = min_size
    
    for _ in range(10):  # 10 iterations gives <0.1pt precision
        mid = (lo + hi) / 2
        # Measure text layout at this size
        text_rect = fitz.Rect(bbox)
        # Use get_text_length for single-line, textbox simulation for multi-line
        rc = measure_textbox_fit(text, text_rect, font, mid)
        
        if rc >= 0:  # fits
            best = mid
            lo = mid
        else:  # overflow
            hi = mid
    
    return best
```

**Master Note**: Chinese text wrapping is character-based (any character can be a break point except after opening punctuation or before closing punctuation). PyMuPDF's `insert_textbox` handles this correctly for CJK text, but you must ensure the font is registered as a CJK font, not as a Latin font that happens to contain CJK glyphs.

### 3.3 Data Model Extensions

No new database tables are needed. The PDF-in-place export uses the existing data:

- `blocks.source_span_json` → provides page number and bbox for mapping
- `blocks.source_text` → for text matching against PDF runs
- `blocks.block_type` + `blocks.protected_policy` → for replace/protect decisions
- `target_segments.text_zh` via `alignment_edges` → translated text
- `documents.source_path` → original PDF file location

**New Configuration** (in `.env` or config):
```
BOOK_AGENT_CJK_FONT_SERIF_PATH=/path/to/NotoSerifCJKsc-Regular.otf
BOOK_AGENT_CJK_FONT_SANS_PATH=/path/to/NotoSansCJKsc-Regular.otf
BOOK_AGENT_CJK_FONT_MONO_PATH=/path/to/NotoSansMonoCJKsc-Regular.otf
BOOK_AGENT_PDF_REPLACE_MIN_FONT_RATIO=0.6
BOOK_AGENT_PDF_REPLACE_HEADING_MIN_FONT_RATIO=0.7
```

### 3.4 API Contract

**Function Signature** (integrated into existing ExportService):

```python
def export_document_zh_pdf_inplace(
    self,
    document_id: str,
    source_pdf_path: str,
    output_path: str,
    *,
    font_config: CjkFontConfig | None = None,
    dry_run: bool = False,
) -> PdfInplaceExportResult:
    """
    Produce a Chinese PDF by replacing text in the source PDF in-place.
    
    Returns:
        PdfInplaceExportResult with:
        - output_path: str (path to generated PDF)
        - total_blocks: int
        - replaced_blocks: int
        - protected_blocks: int
        - skipped_blocks: int (unmapped)
        - warnings: list[str]
        - coverage_pct: float (replaced / total translatable)
        - pages_modified: int
        - font_reductions: list[FontReductionRecord]  # blocks where font was shrunk
    """
```

**Error Cases**:
- Source PDF not found → `FileNotFoundError`
- Source PDF encrypted/password-protected → `PdfAccessError`
- No translations available → `ExportPreconditionError` with message
- CJK font files not found → falls back to built-in, logs warning
- Coverage < 50% → returns result with `low_coverage_warning=True`

---

## 4. Implementation Path

### Phase 1: Core Text Replacement (Target: 90% visual fidelity)

**Scope**:
- PDF Text Run Extractor (Module A)
- Block-to-Run Mapper (Module B) — body text and headings only
- PDF Redact & Insert Engine (Module C) — basic version
- Font Size Fitter (Module D)
- CJK font embedding (Noto family)
- Integration test with "How Large Language Models Work" as validation book

**Acceptance Criteria**:
- Body text paragraphs replaced with Chinese in correct position: >= 85% coverage
- Headings replaced with correct relative size: >= 90% coverage
- All images, figures, vector graphics preserved unchanged
- Code blocks, equations, tables untouched
- Output PDF opens correctly in Adobe Reader, Preview, Chrome
- Font reduction applied only where needed; average reduction < 10%
- Processing time < 30 seconds for 200-page book

**Estimated Effort**: 5-7 person-days

**Interfaces for Phase 2**: Replacement plan structure includes `block_type` field that Phase 2 will use to apply type-specific rendering (captions, footnotes, headers).

**Risks**: PyMuPDF's redaction API may handle complex text layouts (overlapping text, rotated text, text on paths) poorly. Mitigated by falling back to original text for any run that fails redaction.

### Phase 2: Refinement (Target: 95% visual fidelity)

**Scope**:
- Caption and footnote replacement with type-specific formatting
- Running headers/footers replacement
- Table header text replacement (keeping table structure)
- Background color detection for redaction fill (not always white)
- Font pairing refinement (weight matching, not just family matching)
- Multi-column layout awareness
- Coverage report with per-page visual diff (render both PDFs, compute SSIM)

**Acceptance Criteria**:
- Overall text replacement coverage >= 95%
- Captions and footnotes correctly styled
- Table headers translated without breaking table structure
- Background fill matches source page background
- Per-page SSIM between source and output > 0.85 (excluding text content areas)

**Estimated Effort**: 4-5 person-days

**Dependencies**: Phase 1 complete and validated on at least 3 books.

### Phase 3: Edge Cases & Polish (Target: 98% visual fidelity)

**Scope**:
- Rotated text handling
- Text on curved paths
- Watermark detection and preservation
- Form field text replacement
- Bookmarks/outline translation
- Metadata (dc:title, dc:creator) translation
- Font subsetting to reduce output file size
- Automated visual regression test suite

**Acceptance Criteria**:
- All edge cases documented and either handled or explicitly skipped with warnings
- Output file size within 2x of source PDF size
- Automated visual comparison pass rate > 95% across test corpus of 10 books

**Estimated Effort**: 5-8 person-days

### Priority Matrix

- High Impact, Low Effort: Body text + heading replacement, CJK font embedding, image preservation
- High Impact, High Effort: Font-size fitting algorithm, block-to-run fuzzy matching
- Low Impact, Low Effort: Metadata translation, bookmark translation
- Low Impact, High Effort: Rotated text, text on paths, watermarks — defer to Phase 3

---

## 5. Success Criteria

### 5.1 Core KPIs

**KPI 1: Text Replacement Coverage**
- Definition: (blocks successfully replaced / total translatable blocks) * 100
- Current Baseline: 0% (no PDF-in-place export exists)
- Target: Phase 1 >= 85%, Phase 2 >= 95%, Phase 3 >= 98%
- Measurement: Automated count from `PdfInplaceExportResult`
- Frequency: Every export run

**KPI 2: Visual Structure Preservation (SSIM)**
- Definition: Mean Structural Similarity Index between source PDF page renders and output PDF page renders, computed on non-text regions (masked by text bboxes)
- Current Baseline: ~0.4 (HTML-reconstructed PDF vs source)
- Target: >= 0.90
- Measurement: Render both PDFs at 150 DPI, compute per-page SSIM, report mean
- Frequency: Per-book validation

**KPI 3: Font Reduction Severity**
- Definition: Mean (original_size - used_size) / original_size across all replaced blocks
- Current Baseline: N/A
- Target: Mean < 8%, Max < 35%, no body text below 7pt
- Measurement: Aggregated from `font_reductions` in export result
- Frequency: Every export run

**KPI 4: Processing Time**
- Definition: Wall-clock time from export start to PDF save complete
- Current Baseline: N/A
- Target: < 1 second per page (200-page book < 30 seconds)
- Measurement: Timestamp logging
- Frequency: Every export run

### 5.2 Health Monitoring

- **Error Rate**: Percentage of blocks that fail redaction or insertion (target: < 2%)
- **Coverage Trend**: Track coverage KPI across books to detect systematic mapping issues
- **Font File Availability**: Check CJK font paths at startup, warn if missing
- **PDF Compatibility**: Test output in 3+ PDF viewers per release

---

## 6. Risks & Defenses

### 6.1 Failure Mode Registry

| Failure Mode | Root Cause | Early Signal | Structural Defense |
|---|---|---|---|
| Text runs don't match DB blocks | PDF parser extracts text differently from PyMuPDF's `get_text("dict")` — different Unicode normalization, ligature handling, or reading order | Coverage drops below 70% on a book where translation is 100% complete | Fuzzy matching with 0.7 threshold + multiple normalization passes (NFKD, whitespace collapse, ligature expansion) |
| Redaction removes images | `apply_redactions()` called without `images=PDF_REDACT_IMAGE_NONE` flag | Images disappear from output PDF | Hardcode the flag in the engine; add assertion that checks image count before/after redaction on each page |
| Chinese text unreadable (wrong font) | CJK font not properly embedded or wrong encoding | Tofu characters (□) in output PDF | Font validation step: render a test string "测试中文" to a scratch page, verify glyph rendering before proceeding |
| Layout destroyed by overflow | Block translations much longer than source, font reduction hits floor, text spills into adjacent elements | Warning count for "overflow" > 10% of blocks | Overflow blocks fall back to source text (original language preserved) rather than corrupting layout |
| Output file corrupt | PyMuPDF crash during save (out of memory, interrupted write) | Exception during `doc.save()` | Write to temp file first, then atomic rename. Keep source PDF read-only (never modify in place) |

### 6.2 Counter-Intuitive Design Choices

**"We reduce font size instead of reflowing text"**: This looks wrong because smaller text is harder to read. But reflow cascades are harder to detect and impossible to undo — a single reflowed paragraph can shift every subsequent element on the page. Font reduction is local, bounded, and visible in QA. Reflow failures are global, unbounded, and often invisible until a reader complains about a figure that ended up on the wrong page.

**"We skip untranslated blocks rather than inserting placeholder text"**: The instinct is to mark untranslated areas with "[UNTRANSLATED]" so reviewers can find them. But this destroys the visual rhythm worse than leaving the English in place. A Chinese document with occasional English paragraphs reads as "international" — a Chinese document with "[UNTRANSLATED]" blocks reads as "broken." Leave the English; the coverage metric tells reviewers where gaps are.

**"We operate on spans, not blocks"**: The PDF's block structure doesn't match the parser's block structure. Two systems looking at the same page will disagree about where one paragraph ends and another begins. Span-level extraction avoids this disagreement entirely — we work with what the PDF actually contains, not what we think it should contain.

**"We embed full CJK fonts, not subsets, in Phase 1"**: Subsetting saves space but adds complexity (must track which glyphs are used, regenerate subset per document). The 15MB overhead is acceptable for professional book delivery. Subsetting is deferred to Phase 3 when the core pipeline is stable.

### 6.3 Hard Prohibitions (Violation = Failure)

1. **Never modify the source PDF file.** Always copy first, modify the copy. The source is the single source of truth and must remain unchanged for re-export, debugging, and verification. Reason: one corrupted source file means all future exports of that book are impossible.

2. **Never call `apply_redactions()` without `images=fitz.PDF_REDACT_IMAGE_NONE`.** This is the most dangerous single line of code in the entire module. Without this flag, every image that overlaps with any redacted text area will be permanently deleted from the page. Reason: a 200-page book has hundreds of image-text overlaps; one missing flag destroys hundreds of figures.

3. **Never replace text in blocks with `protected_policy = "protect"`.** Code, equations, URLs, and other protected content must pass through completely untouched. "But the user wanted everything translated" is never a valid reason to override protection. Reason: translated code is broken code; translated equations are wrong mathematics.

4. **Never allow font size below 5pt for any text.** If the fitter algorithm reaches this floor and text still doesn't fit, fall back to source text. Reason: 5pt text is illegible in print and barely readable on screen. Delivering illegible text is worse than delivering untranslated text.

5. **Never assume PDF page numbering matches DB page numbering.** Always apply the offset correction (DB is 1-indexed, PyMuPDF is 0-indexed) and validate by checking that the text at the expected position matches. Reason: a page numbering mismatch means every replacement goes on the wrong page — the entire output is corrupted, not just one page.

---

## Appendix

### A. Glossary

- **Text Run / Span**: The smallest unit of text in a PDF — a string rendered in a single font at a single size and color. One paragraph typically contains multiple spans.
- **Redaction**: The PDF operation of permanently removing content from a specified rectangle and filling it with a solid color. Unlike annotation deletion, redaction removes the underlying content stream data.
- **Bbox**: Bounding box — the rectangular area (x0, y0, x1, y1) that encloses a text element on a page.
- **SSIM**: Structural Similarity Index Measure — a metric for comparing two images that correlates with human visual perception better than pixel-by-pixel comparison.
- **Font Pairing**: Selecting a CJK font that visually harmonizes with the source document's Latin font in weight, contrast, and x-height.
- **Spatial Contract**: The implicit agreement that a text element occupies a specific position and area on a page, and modifications to the text must respect this position.

### B. References

- **PyMuPDF Redaction API**: pymupdf.readthedocs.io — `Page.add_redact_annot()`, `Page.apply_redactions()`
- **Noto CJK Fonts**: github.com/googlefonts/noto-cjk — OFL-licensed CJK font family
- **TransPDF**: transpdf.iceni.com — Commercial PDF translation tool using overlay approach
- **XLIFF-based PDF Localization**: OASIS XLIFF 2.0 specification for document-level translation interchange
- **Adobe DTP Localization**: Adobe InDesign localization workflow (industry standard for layout-preserving translation)

### C. Invisible Dimensions Memo

**Time Dimension**: The current bilingual markdown deliverable works for review but becomes a liability at scale. As more books are translated, users will compare quality across books. One book delivered as a faithful PDF next to another delivered as reconstructed HTML will make the HTML version look broken by comparison, even if it was "fine" in isolation. This means the PDF-in-place pipeline, once shipped for one book, creates pressure to apply it to all books. Plan for batch processing from day one.
→ Engineering Impact: The export module must be stateless and parallelizable — no global state, no cross-document dependencies, each book exportable independently.

**Behavior Dimension**: Users receiving a "translated PDF" will instinctively compare it to the original by flipping between pages in a PDF viewer. They won't read linearly — they'll jump to figures, tables, and headings first. This means figures and headings have disproportionate impact on perceived quality, even though body text is the majority of content.
→ Engineering Impact: Heading replacement has higher priority and tighter quality tolerances than body text. Image preservation is non-negotiable (tested by assertion, not by trust).

**Counter-Intuitive Dimension**: The intuitive approach — "extract everything perfectly, translate it, reconstruct everything perfectly" — is a trap. Extraction is 95% accurate. Translation is 90% accurate. Reconstruction is 85% accurate. Multiplied: 0.95 * 0.90 * 0.85 = 72% end-to-end fidelity. The in-place approach skips reconstruction entirely: 0.95 * 0.90 * 1.00 = 85.5% fidelity, and the remaining 14.5% is in translation quality (which improves with better models), not in engineering artifacts.
→ Engineering Impact: This is the fundamental reason to choose in-place replacement over reconstruction. The math makes the decision obvious once you see it.
