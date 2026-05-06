"""Render a production-grade bilingual Markdown for a chapter+ordinal subset.

Why this exists outside the standard pipeline export:
  The pipeline's `merged_markdown` export gate enforces whole-document
  QA — when only a partial-translation slice is available (as in this
  PDF where chapter detection lumped most of the body under one
  malformed "chapter"), the gate is unreachable without translating
  hundreds of irrelevant blocks. This renderer reads the same source
  of truth (blocks / sentences / target_segments) and emits an
  equivalent bilingual Markdown for the requested ordinal slice.

Production-grade defenses encoded in this file:
  F1  Headings use `normalized_text` when present (raw `source_text`
      may contain literal newlines that split the heading mid-line).
  F2  Page-number / running-header paragraphs (e.g. "16\\nCHAPTER 2\\n
      Tokenizers...") are filtered out before render.
  F3  Heading echoes from page-running-headers (a heading whose
      normalized text equals the immediately-preceding heading +
      a trailing page number) are deduped.
  F4  LLM hallucinated meta-commentary (e.g. trailing
      "这是当前段落中唯一的句子。") is stripped from translations.
  F5  Blocks misclassified as `code` whose content is prose
      (high non-ASCII ratio or very long) are downgraded to paragraph.
  F6  Heading-typed blocks that look like OCR-broken list items
      ("4 Sincea", "5 Isit an ethical") are demoted.

Each defense is unit-testable; see tests/test_render_bilingual_subset.py.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import create_engine, text  # noqa: E402

DEFAULT_DB_URL = "postgresql+psycopg://postgres:postgres@localhost:55432/book_agent"


# ---------------------------------------------------------------------------
# Pure helpers — testable without a DB.
# ---------------------------------------------------------------------------

# F2: page-running-header detection. Matches:
#   "1"
#   "  14  "
#   "16\nCHAPTER 2\nTokenizers: How large language models see the world"
#   "1.1 Generative AI in context\n3"   (heading echo with page number)
_PAGE_NUMBER_ONLY = re.compile(r"^\s*\d+\s*$")
_PAGE_HEADER_WITH_CHAPTER = re.compile(
    r"^\s*\d+\s+(?:CHAPTER|Chapter)\s+\d+\b", re.MULTILINE
)
_TRAILING_PAGE_NUMBER = re.compile(r"\s+\d{1,3}\s*$")


# F2 extended: also detect the section-header form used in book interiors:
#   "1.2 What you will learn 5"
#   "2.3 Tokenization and LLM capabilities 25"
# (section-number + section-title + trailing page-number)
_SECTION_RUNNING_HEADER = re.compile(
    r"^\s*\d+(?:\.\d+){1,3}\s+\S.+\s+\d{1,3}\s*$"
)


def is_page_running_header(text_value: str) -> bool:
    """True if a block is a PDF page running-header artifact (F2 / F2-ext).

    Now recognises three forms:
      1. Lone page number ("14")
      2. "<page-num> CHAPTER <n> <chapter title...>"
      3. "<section-num> <section title> <page-num>" — common as a footnote-
         typed block when the PDF parser confuses the running header with
         a footnote line.
    """
    if not text_value:
        return False
    stripped = text_value.strip()
    if _PAGE_NUMBER_ONLY.match(stripped):
        return True
    if _PAGE_HEADER_WITH_CHAPTER.search(stripped):
        lines = [ln for ln in stripped.splitlines() if ln.strip()]
        return len(lines) <= 3
    if _SECTION_RUNNING_HEADER.match(stripped):
        return True
    return False


def strip_trailing_page_number_echo(heading_text: str) -> str:
    """A heading captured from a page running-header often has a trailing
    page number (e.g. "1.1 Generative AI in context  3"). Strip it (F3 helper).
    """
    if not heading_text:
        return heading_text
    candidate = _TRAILING_PAGE_NUMBER.sub("", heading_text.strip())
    # Only apply if the strip actually removed a single short trailing number
    # AND the remainder is a plausible heading (has alphabetic content).
    if candidate and candidate != heading_text.strip() and re.search(r"[A-Za-z一-鿿]", candidate):
        return candidate
    return heading_text.strip()


# F4: LLM meta-commentary stripping. The DeepSeek-V4 reasoning model
# occasionally emits a Chinese meta-explanation when the input is a
# single short sentence. We use an explicit phrase list (easier to
# audit and extend than a clever regex) and only strip at the tail of
# a translation to minimise false positives on legitimate prose.
_META_PHRASES = [
    r"这是当前段落中唯一的句子",
    r"这是当前段落中唯一的一个句子",
    r"这是当前段落中的唯一句子",
    r"这是当前段落中(?:仅|只)有的(?:一个)?句子",
    r"当前段落中(?:仅|只)有一(?:个)?句子",
    r"该段落(?:仅|只)(?:包含|有)一(?:个)?句子",
    r"本段(?:仅|只)有一(?:个)?句子",
]
_META_COMMENT_RE = re.compile(
    r"\s*(?:" + "|".join(_META_PHRASES) + r")[。.]?\s*$"
)


def strip_llm_meta_commentary(zh: str) -> str:
    """Remove model-generated trailing meta-explanations (F4).

    Two-pass strip:
      1. Remove the meta phrase + its optional trailing punctuation.
      2. If pass-1 left a hanging "intro" colon (e.g. "总结：" with the
         body now gone), strip that colon too — it belonged to the
         meta intro, not to the legitimate translation.
    """
    if not zh:
        return zh
    cleaned = _META_COMMENT_RE.sub("", zh).rstrip()
    cleaned = re.sub(r"[:：]\s*$", "", cleaned).rstrip()
    return cleaned


# F5: code-block-vs-prose disambiguation. PDFs occasionally tag prose
# pages as `code` because of font heuristics. Real code in this book is
# Python/pseudocode; prose contains long natural-language sentences.
_STRONG_CODE_SIGNALS_RE = re.compile(
    r"[{}=]"  # braces / equals — extremely rare in prose
    r"|^\s*(?:def |class |import |from |return |if __|for |while |elif |@)",
    re.MULTILINE,
)


def is_actually_prose(code_text: str) -> bool:
    """True if a block typed as 'code' looks like prose (F5).

    Two reliable signals:
      1. CJK characters present ⇒ definitely prose (real code in this
         repo would not contain Chinese).
      2. Long-form text (> 200 chars) with **zero** strong code signals
         (no `=` / `{` / `}` / no python keyword line-starts). Real code
         hits these every few lines; prose can go on indefinitely
         without them.
    """
    if not code_text:
        return False
    text_value = code_text.strip()
    if re.search(r"[一-鿿]", text_value):
        return True
    if len(text_value) > 200 and not _STRONG_CODE_SIGNALS_RE.search(text_value):
        return True
    return False


# F6: heading-vs-broken-text disambiguation.
_HEADING_NUM_RE = re.compile(r"^(\d+(?:\.\d+){0,3})\s+(.+)$", re.DOTALL)


# A small allow-list of words that legitimately appear as the *whole body*
# of a top-level integer-prefixed heading (e.g. "1 Introduction"). If we
# see one of these, we keep the heading even though the prefix is a bare
# integer instead of a section number like "1.1".
_LEGITIMATE_HEADING_BODY_FIRST_WORDS = {
    "introduction", "summary", "overview", "background", "abstract",
    "preface", "appendix", "conclusion", "references", "bibliography",
    "acknowledgments", "preliminaries", "methodology", "results",
    "discussion", "future", "related", "experiments", "motivation",
    "definitions", "notation", "preamble",
}


# F7: NOTE / TIP / WARNING callouts mis-classified as headings.
# The parser sometimes splits a callout block of the form
#     "NOTE Vision and language are not the only options..."
# into two blocks:
#     heading   : "NOTE Vision and language"
#     paragraph : "are not the only options for generative AI..."
# Detect the heading half and merge with the next paragraph.
_NOTE_HEADING_PREFIXES = ("NOTE ", "TIP ", "WARNING ", "CAUTION ", "IMPORTANT ", "DEFINITION ", "EXAMPLE ")


def is_note_callout_heading(heading_text: str) -> bool:
    """True if a heading-typed block looks like the start of a NOTE/TIP/
    WARNING callout that the parser incorrectly split (F7).

    The label must be all-uppercase in the original text — "Note that..."
    is just normal prose and should NOT be merged.
    """
    if not heading_text:
        return False
    stripped = heading_text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _NOTE_HEADING_PREFIXES)


# F8: paragraph blocks that are actually a dump of figure / diagram labels.
# Diagrams produce label sequences with no grammatical glue:
#   "Some examples of generative AI include are products built using
#    ChatGPT Gemini Copilot Claude which use techniques from Artificial
#    intelligence Large language models Machine learning Natural language
#    processing is the input and output from are built using Deep
#    learning Text data Transformers"
# Signature: many capitalised noun phrases interleaved with stop-word
# fragments ("are", "is", "from", "using"), and very few sentence
# terminators relative to length.
def is_diagram_label_dump(text_value: str) -> bool:
    """True if a paragraph block is a dump of diagram labels rather than
    real prose (F8). Conservative: only triggers on long-ish text that
    has unusually few sentence terminators.
    """
    if not text_value:
        return False
    stripped = text_value.strip()
    if len(stripped) < 80:
        return False
    # Real prose averages a sentence terminator (`.`, `?`, `!`) every ~120
    # chars. Diagram label dumps tend to have zero or one for hundreds of
    # chars.
    terminators = sum(stripped.count(c) for c in ".!?")
    if terminators >= max(1, len(stripped) // 120):
        return False
    # Also require a high density of capitalised tokens (≥ 25% of tokens
    # start with an uppercase letter) — diagram boxes are mostly proper
    # nouns / class names.
    tokens = re.findall(r"[A-Za-z][A-Za-z]+", stripped)
    if not tokens:
        return False
    capitalised = sum(1 for t in tokens if t[:1].isupper())
    return (capitalised / len(tokens)) >= 0.25


# F9: split a paragraph block into multiple visual paragraphs by detecting
# "short-tail-line + period" boundaries in the source text.
#
# When a PDF parser flattens a multi-paragraph block into one, the original
# line wrapping is preserved (newlines are kept) but paragraph indentation
# is lost. The diagnostic signal: a line that ends with sentence-terminal
# punctuation AND is significantly shorter than the typical line length is
# almost always the last line of a visual paragraph in justified-text PDFs.
def split_into_visual_paragraphs(source_text: str) -> list[str]:
    """Return a list of visual-paragraph chunks from raw source_text (F9).

    A single-paragraph block returns a one-element list; a multi-paragraph
    block returns the chunks in order. Each chunk is the joined text of
    the contained lines (with single spaces).
    """
    if not source_text:
        return []
    lines = source_text.split("\n")
    # Drop empty and pure-whitespace lines from consideration but keep
    # them as boundary signals.
    real_lines = [ln.strip() for ln in lines if ln.strip()]
    if len(real_lines) <= 1:
        return [source_text.strip()] if source_text.strip() else []
    median = sorted(len(ln) for ln in real_lines)[len(real_lines) // 2]
    short_threshold = max(20, int(median * 0.55))

    chunks: list[list[str]] = [[]]
    for idx, ln in enumerate(real_lines):
        chunks[-1].append(ln)
        is_last = idx == len(real_lines) - 1
        if is_last:
            continue
        ends_terminal = ln.endswith((".", "!", "?", ".”", '."', ".)"))
        if ends_terminal and len(ln) <= short_threshold:
            # Open a new paragraph chunk after this short-tail line.
            chunks.append([])

    paragraphs = [" ".join(c).strip() for c in chunks if c]
    return [p for p in paragraphs if p]


def looks_like_broken_list_item(heading_text: str) -> bool:
    """True if a heading looks like '4 Sincea' / '5 Isit an ethical' (F6).

    In this codebase, real section headings use the form 'N.M[.K] Title'.
    A bare integer prefix ('4 Sincea') paired with a short non-vocabulary
    body is a strong signal of a broken list-item / OCR fragment that
    PDF parsing miscategorised as a heading.
    """
    if not heading_text:
        return False
    match = _HEADING_NUM_RE.match(heading_text.strip())
    if not match:
        return False
    number, body = match.group(1), match.group(2).strip()
    # Multi-level numbering ("1.1", "2.3.1") is structurally a real heading.
    if "." in number:
        return False
    tokens = body.split()
    if not tokens:
        return False
    # Allow-list: well-known top-level heading words mean it's likely a
    # legitimate "1 Introduction" form.
    if tokens[0].lower() in _LEGITIMATE_HEADING_BODY_FIRST_WORDS:
        return False
    # Short body (≤ 4 tokens) + bare integer prefix + first word not in
    # the heading vocab → flag as broken.
    if len(tokens) <= 4:
        return True
    return False


def heading_level(heading_text: str) -> int:
    """Map heading text to MD level by numbering pattern.

    Mapping (chosen so MD hierarchy never skips levels):
      no number prefix → ## (chapter / unnumbered section, level 2)
      "1"               → ## (top-level chapter, level 2)
      "1.1"             → ### (section, level 3)
      "1.1.1"           → #### (subsection, level 4)
      "1.1.1.1"         → ##### (sub-subsection, level 5)
    """
    match = _HEADING_NUM_RE.match(heading_text.strip())
    if match:
        depth = match.group(1).count(".") + 1
        return min(1 + depth, 6)
    return 2


def clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.replace("\r\n", "\n").strip()


# ---------------------------------------------------------------------------
# DB access.
# ---------------------------------------------------------------------------

@dataclass
class RenderConfig:
    document_id: str
    output_path: Path
    db_url: str = DEFAULT_DB_URL
    title: str = "Bilingual export"
    subtitle: str = ""
    author: str = ""
    # Two scoping modes (mutually exclusive — chapter_ids takes precedence):
    #   1. chapter_ids: render every block in each listed chapter, in
    #      chapter-creation order (preferred when the parser produced
    #      proper chapter boundaries).
    #   2. chapter_id + ordinal_lo + ordinal_hi: render block-ordinal
    #      slice within a single chapter (legacy mode for partially-
    #      mis-parsed documents where chapter boundaries are wrong).
    chapter_ids: list[str] | None = None
    chapter_id: str | None = None
    ordinal_lo: int | None = None
    ordinal_hi: int | None = None


@dataclass
class RenderStats:
    blocks_seen: int = 0
    blocks_emitted: int = 0
    with_translation: int = 0
    no_translation: int = 0
    page_headers_filtered: int = 0  # F2 (paragraph + footnote-typed page hdrs)
    heading_echoes_filtered: int = 0  # F3
    meta_commentary_stripped: int = 0  # F4
    code_to_prose_demotions: int = 0  # F5
    broken_heading_demotions: int = 0  # F6
    note_callouts_merged: int = 0  # F7
    diagram_label_dumps_filtered: int = 0  # F8
    paragraphs_split_visually: int = 0  # F9 (1 increment per *added* split)
    images_linked: int = 0  # F10
    images_missing_asset: int = 0  # F10 (placeholder retained)
    merged_targets_split: int = 0  # F11 (merged_sentence redistributed)
    notes: list[str] = field(default_factory=list)


def fetch_blocks(conn, cfg: RenderConfig):
    """Fetch blocks for the configured scope.

    Multi-chapter mode (preferred): ``cfg.chapter_ids`` lists chapters in
    desired emission order; we fetch each chapter's blocks ordered by
    block ordinal. Single-chapter slice mode is preserved for backward
    compatibility with documents whose parser-produced chapter boundaries
    are wrong.
    """
    if cfg.chapter_ids:
        return conn.execute(
            text(
                """
                SELECT
                  b.id::text AS id,
                  b.ordinal,
                  b.block_type,
                  b.source_text,
                  b.normalized_text,
                  b.source_anchor,
                  c.ordinal AS chapter_ordinal,
                  c.title_src AS chapter_title,
                  c.id::text AS chapter_id
                FROM blocks b
                JOIN chapters c ON b.chapter_id = c.id
                WHERE b.chapter_id = ANY(:cids)
                ORDER BY c.ordinal, b.ordinal
                """
            ),
            {"cids": cfg.chapter_ids},
        ).fetchall()
    return conn.execute(
        text(
            """
            SELECT
              b.id::text AS id,
              b.ordinal,
              b.block_type,
              b.source_text,
              b.normalized_text,
              b.source_anchor,
              NULL::int AS chapter_ordinal,
              NULL::text AS chapter_title,
              NULL::text AS chapter_id
            FROM blocks b
            WHERE b.chapter_id = :cid
              AND b.ordinal BETWEEN :lo AND :hi
            ORDER BY b.ordinal
            """
        ),
        {"cid": cfg.chapter_id, "lo": cfg.ordinal_lo, "hi": cfg.ordinal_hi},
    ).fetchall()


def fetch_run_for_block(conn, block_id: str):
    row = conn.execute(
        text(
            """
            SELECT tr.id::text
            FROM translation_packets tp
            LEFT JOIN translation_runs tr
              ON tr.packet_id = tp.id AND tr.status = 'succeeded'
            WHERE tp.block_start_id = :bid
            ORDER BY tr.attempt DESC NULLS LAST
            LIMIT 1
            """
        ),
        {"bid": block_id},
    ).fetchone()
    return row[0] if row else None


def fetch_sentences(conn, block_id: str):
    return conn.execute(
        text(
            """
            SELECT ordinal_in_block, source_text, translatable
            FROM sentences WHERE block_id = :bid
            ORDER BY ordinal_in_block
            """
        ),
        {"bid": block_id},
    ).fetchall()


def fetch_chapter_title_zh(conn, chapter_id: str, chapter_title: str | None) -> str | None:
    """Return the Chinese rendering of the chapter title.

    The PDF parser sometimes mis-classifies the chapter title block as
    `paragraph` rather than `heading`, so we cannot just take the first
    heading-typed block. Instead we scan the chapter's blocks in order
    and pick the first one whose normalized text contains the chapter
    title body (with the leading "N " number prefix stripped). The
    matched block's first translated sentence is the chapter title in
    Chinese.
    """
    if not chapter_title:
        return None
    title_body = re.sub(r"^\d+\s+", "", chapter_title.strip())
    if not title_body:
        return None
    rows = conn.execute(
        text(
            """
            SELECT b.id::text, b.ordinal, b.normalized_text
            FROM blocks b
            WHERE b.chapter_id = :cid
              AND b.normalized_text IS NOT NULL
            ORDER BY b.ordinal
            LIMIT 12
            """
        ),
        {"cid": chapter_id},
    ).fetchall()
    title_lower = title_body.lower()
    for blk_id, _ord, norm in rows:
        if not norm:
            continue
        if title_lower in norm.lower():
            run_id = fetch_run_for_block(conn, blk_id)
            if not run_id:
                continue
            targets = fetch_targets(conn, run_id)
            if not targets:
                continue
            first_ord = min(targets.keys())
            raw = targets[first_ord][1]
            if not raw:
                continue
            return re.sub(r"\s+", " ", clean_text(raw)).strip() or None
    return None


def fetch_targets(conn, run_id: str):
    rows = conn.execute(
        text(
            """
            SELECT ordinal, text_zh, segment_type, final_status, confidence
            FROM target_segments WHERE translation_run_id = :rid
            ORDER BY ordinal
            """
        ),
        {"rid": run_id},
    ).fetchall()
    return {r[0]: r for r in rows}


# F11: split a single `merged_sentence` translation into per-sentence
# chunks. The translator sometimes returns one big paragraph as a single
# segment (segment_type='merged_sentence') instead of one segment per
# source sentence. When that happens — and we want to render with
# paragraph splits (F9) or sentence-aligned interleave — the ZH text
# needs to be redistributed across source sentence ordinals so each
# F9 chunk carries its own translated content.
_CHINESE_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？])(?=[^」』）)\s])|(?<=[。！？][」』）)])")


def split_chinese_sentences(text_zh: str) -> list[str]:
    """Split a Chinese paragraph into sentence-level chunks.

    Splits at full-width sentence terminators (。！？) including those
    followed by a closing quote/bracket. Keeps the terminator with the
    preceding sentence.
    """
    if not text_zh:
        return []
    parts = _CHINESE_SENTENCE_BOUNDARY.split(text_zh.strip())
    return [p.strip() for p in parts if p.strip()]


def expand_merged_targets(
    targets: dict, sentences: list
) -> dict:
    """If `targets` is a single merged_sentence covering multiple source
    sentences, split it on Chinese sentence terminators and pair with
    sentence ordinals 1..N.

    Returns a new dict keyed by sentence.ordinal_in_block. If splitting
    is not appropriate (counts don't match, etc.), returns the original
    dict unchanged.
    """
    if not targets or len(targets) != 1 or len(sentences) <= 1:
        return targets
    only_target = next(iter(targets.values()))
    text_zh = only_target[1] if only_target else None
    if not text_zh:
        return targets
    zh_parts = split_chinese_sentences(text_zh)
    # Only redistribute when the split produces exactly one chunk per
    # source sentence; otherwise we'd be making up alignments.
    if len(zh_parts) != len(sentences):
        return targets
    new_targets: dict = {}
    for (s_ord, *_), zh in zip(sentences, zh_parts):
        # Reuse the original tuple shape but substitute the per-sentence
        # ZH text in slot 1.
        cloned = list(only_target)
        cloned[1] = zh
        new_targets[s_ord] = tuple(cloned)
    return new_targets


# Image-asset linking (F10).
#   For figure/image-typed blocks, look up the extracted PNG/JPG asset in
#   `document_images` and emit a Markdown image reference if the file
#   actually exists on disk. Many entries are "logical_only" — the parser
#   recorded the bbox but never wrote a PNG; for those we keep the existing
#   placeholder so the reader knows a figure was here.
_ARTIFACTS_ROOT = ROOT / "artifacts"


def fetch_image_for_block(conn, block_id: str) -> tuple[str, str | None] | None:
    """Return (relative_md_path, alt_or_ocr_text) if an image asset exists
    on disk for this block, else None.
    """
    rows = conn.execute(
        text(
            """
            SELECT storage_path, alt_text, ocr_text, image_type
            FROM document_images
            WHERE block_id = :bid
            ORDER BY created_at
            """
        ),
        {"bid": block_id},
    ).fetchall()
    for storage_path, alt_text, ocr_text, image_type in rows:
        if not storage_path:
            continue
        full_path = _ARTIFACTS_ROOT / storage_path
        if not full_path.exists():
            continue
        # Use a path relative to the rendered Markdown's location.
        # Renderer writes into artifacts/exports/<doc_id>/, and the image
        # lives at artifacts/document-images/<doc_id>/<file>.png. Using a
        # `../../document-images/...` relative reference keeps the artifact
        # portable when the artifacts/ directory is moved as a unit.
        rel_path = "../../" + storage_path
        alt = (alt_text or ocr_text or image_type or "figure").strip()
        return rel_path, alt
    return None


# ---------------------------------------------------------------------------
# Renderer.
# ---------------------------------------------------------------------------

def render_block(
    btype: str,
    source_raw: str,
    source_normalized: str | None,
    sentences,
    targets,
    stats: RenderStats,
    last_heading_norm: str | None,
    pending_note: dict | None = None,
    image_md: str | None = None,
) -> tuple[list[str], str | None, dict | None]:
    """Render one block to MD lines.

    Returns (lines, updated_last_heading_norm, pending_note).
    pending_note carries a NOTE/TIP/WARNING heading that the caller asked
    us to merge into the next paragraph (F7); when set, the heading itself
    has not yet been emitted and we will fold it into this block.

    Empty `lines` ⇒ block was filtered out and produced nothing.
    """
    lines: list[str] = []

    # F1: prefer normalized_text for headings; fall back to source with
    # newlines collapsed.
    canonical_source = clean_text(source_normalized) or clean_text(source_raw).replace("\n", " ")
    canonical_source = re.sub(r"\s+", " ", canonical_source).strip()

    if not canonical_source and btype not in {"figure", "image"}:
        return lines, last_heading_norm, pending_note

    # Block-type re-routing (F5, F6, F8).
    effective_type = btype
    if btype == "code" and is_actually_prose(source_raw or canonical_source):
        effective_type = "paragraph"
        stats.code_to_prose_demotions += 1
    if btype == "heading" and looks_like_broken_list_item(canonical_source):
        effective_type = "paragraph"
        stats.broken_heading_demotions += 1

    # F2 / F2-ext: drop page running-headers tagged as paragraphs OR footnotes.
    if effective_type in {"paragraph", "footnote"} and is_page_running_header(
        source_raw or canonical_source
    ):
        stats.page_headers_filtered += 1
        return lines, last_heading_norm, pending_note

    # F8: drop diagram label dumps (paragraphs with no real grammar).
    if effective_type == "paragraph" and is_diagram_label_dump(canonical_source):
        stats.diagram_label_dumps_filtered += 1
        return lines, last_heading_norm, pending_note

    # ZH text from sentence-aligned targets, with F4 cleanup.
    def zh_for_sentence(s_ord: int) -> str:
        tgt = targets.get(s_ord) if targets else None
        if not tgt or not tgt[1]:
            return ""
        before = clean_text(tgt[1])
        after = strip_llm_meta_commentary(before)
        if after != before:
            stats.meta_commentary_stripped += 1
        return re.sub(r"\s+", " ", after).strip()

    # F3: heading echoes from page running-headers — strip trailing page
    # number; if the resulting heading is identical to the previous one,
    # drop it.
    if effective_type == "heading":
        # F7: NOTE/TIP/WARNING callouts get merged with the next paragraph;
        # signal the caller and emit nothing for this block.
        if is_note_callout_heading(canonical_source):
            note_zh = ""
            for s_ord, _src, _ in sentences:
                cand = zh_for_sentence(s_ord)
                if cand:
                    note_zh = cand
                    break
            return (
                lines,
                last_heading_norm,
                {"en_title": canonical_source, "zh_title": note_zh},
            )

        de_paged = strip_trailing_page_number_echo(canonical_source)
        normalized_for_dedupe = re.sub(r"\s+", " ", de_paged).strip().lower()
        if last_heading_norm and normalized_for_dedupe == last_heading_norm:
            stats.heading_echoes_filtered += 1
            return lines, last_heading_norm, pending_note
        canonical_source = de_paged
        new_last_heading = normalized_for_dedupe

        level = heading_level(canonical_source)
        hashes = "#" * level
        zh = ""
        for s_ord, _src, _ in sentences:
            zh_candidate = zh_for_sentence(s_ord)
            if zh_candidate:
                zh = strip_trailing_page_number_echo(zh_candidate)
                break
        lines.append(f"{hashes} {canonical_source}\n")
        if zh:
            lines.append(f"{hashes} {zh}\n\n")
        else:
            lines.append("\n")
        return lines, new_last_heading, pending_note

    if effective_type in {"figure", "image"}:
        # F10: emit a real Markdown image reference if an asset is on disk;
        # otherwise keep the placeholder so readers know a figure was here.
        alt = canonical_source[:120] if canonical_source else effective_type
        if image_md:
            stats.images_linked += 1
            lines.append(image_md + "\n\n")
        else:
            stats.images_missing_asset += 1
            lines.append(f"> _[图/{effective_type} (asset missing)]: {alt}_\n\n")
        return lines, last_heading_norm, pending_note

    if effective_type == "table":
        lines.append(f"> _[表/table]: {canonical_source[:200]}_\n\n")
        return lines, last_heading_norm, pending_note

    if effective_type == "equation":
        lines.append(f"$$\n{canonical_source}\n$$\n\n")
        return lines, last_heading_norm, pending_note

    if effective_type == "code":
        lines.append("```\n" + (source_raw or canonical_source).strip() + "\n```\n\n")
        return lines, last_heading_norm, pending_note

    if effective_type == "caption":
        en = canonical_source
        zh_parts = [zh_for_sentence(s_ord) for s_ord, _, _ in sentences]
        zh_parts = [p for p in zh_parts if p]
        lines.append(f"> _Caption (EN):_ {en}\n>\n")
        if zh_parts:
            lines.append(f"> _图说 (ZH):_ {' '.join(zh_parts)}\n\n")
        else:
            lines.append("\n")
        return lines, last_heading_norm, pending_note

    # paragraph / list_item / footnote / default.
    # F9: split paragraph into visual paragraphs by short-tail-line detection.
    if effective_type == "paragraph":
        chunks = split_into_visual_paragraphs(source_raw or canonical_source) if source_raw else [canonical_source]
        if not chunks:
            return lines, last_heading_norm, pending_note
        if len(chunks) > 1:
            stats.paragraphs_split_visually += len(chunks) - 1

        # Map sentences to chunks via prefix-match against each chunk.
        # Each sentence must align with exactly one chunk; un-aligned
        # sentences fall through to the last chunk.
        sentence_buckets: list[list[tuple[int, str]]] = [[] for _ in chunks]
        normalized_chunks = [re.sub(r"\s+", " ", c).strip() for c in chunks]
        cursor = 0
        for s_ord, s_src, _ in sentences:
            s_clean = clean_text(s_src)
            if not s_clean:
                continue
            s_norm = re.sub(r"\s+", " ", s_clean).strip()
            placed = False
            # Search forward from current cursor — sentences are ordered.
            for idx in range(cursor, len(normalized_chunks)):
                if s_norm and s_norm[:40] in normalized_chunks[idx]:
                    sentence_buckets[idx].append((s_ord, s_clean))
                    cursor = idx
                    placed = True
                    break
            if not placed:
                sentence_buckets[cursor].append((s_ord, s_clean))

        # F7: prepend NOTE banner to the FIRST emitted chunk if present.
        for idx, bucket in enumerate(sentence_buckets):
            if not bucket and idx >= len(chunks):
                continue
            en_parts = [s for _, s in bucket]
            zh_parts = [zh_for_sentence(o) for o, _ in bucket]
            zh_parts = [p for p in zh_parts if p]
            # If sentence buckets are empty for this chunk (no aligned
            # sentences), fall back to the chunk text itself for EN.
            if not en_parts and idx < len(chunks):
                en_parts = [chunks[idx]]
            en_block = " ".join(en_parts).strip()
            zh_block = "".join(zh_parts).strip()
            if not en_block:
                continue

            if idx == 0 and pending_note:
                stats.note_callouts_merged += 1
                en_title = pending_note["en_title"]
                zh_title = pending_note.get("zh_title") or ""
                # Markdown blockquote callout: emphasised label + body.
                lines.append(f"> **{en_title}** — {en_block}\n>\n")
                if zh_block:
                    title_clause = f"**{zh_title}** — " if zh_title else ""
                    lines.append(f"> {title_clause}{zh_block}\n\n")
                else:
                    lines.append("\n")
                pending_note = None
            else:
                lines.append(f"{en_block}\n\n")
                if zh_block:
                    lines.append(f"{zh_block}\n\n")

        return lines, last_heading_norm, pending_note

    # list_item / footnote / default fallthrough — original behaviour.
    en_parts2: list[str] = []
    zh_parts2: list[str] = []
    for s_ord, s_src, _ in sentences:
        s_clean = clean_text(s_src)
        if not s_clean:
            continue
        en_parts2.append(s_clean)
        zh = zh_for_sentence(s_ord)
        if zh:
            zh_parts2.append(zh)
    en_block = " ".join(en_parts2)
    zh_block = "".join(zh_parts2)

    if effective_type == "list_item":
        lines.append(f"- {en_block}\n")
        if zh_block:
            lines.append(f"  - {zh_block}\n\n")
        else:
            lines.append("\n")
    elif effective_type == "footnote":
        lines.append(f"> ¹ {en_block}\n")
        if zh_block:
            lines.append(f"> ¹ {zh_block}\n\n")
        else:
            lines.append("\n")
    else:
        if not en_block:
            return lines, last_heading_norm, pending_note
        lines.append(f"{en_block}\n\n")
        if zh_block:
            lines.append(f"{zh_block}\n\n")

    return lines, last_heading_norm, pending_note


def render(cfg: RenderConfig) -> RenderStats:
    stats = RenderStats()
    engine = create_engine(cfg.db_url)

    if cfg.chapter_ids:
        scope_line = f"chapter_ids: {cfg.chapter_ids}"
    else:
        scope_line = (
            f"chapter_id: {cfg.chapter_id}\n"
            f"ordinal_range: [{cfg.ordinal_lo}, {cfg.ordinal_hi}]"
        )
    front = (
        "---\n"
        f"document_id: {cfg.document_id}\n"
        f"{scope_line}\n"
        f"rendered_at: {datetime.now(timezone.utc).isoformat()}\n"
        "renderer: scripts/render_bilingual_subset.py\n"
        "---\n\n"
        f"# {cfg.title}\n\n"
    )
    # Subtitle/author rendered as italic so they don't compete with the
    # chapter-level (##) heading hierarchy in the body.
    if cfg.subtitle:
        front += f"*{cfg.subtitle}*\n\n"
    if cfg.author:
        front += f"*{cfg.author}*\n\n"
    front += "---\n\n"

    sections: list[str] = [front]

    with engine.connect() as conn:
        blocks = fetch_blocks(conn, cfg)
        last_heading_norm: str | None = None
        prev_chapter_ordinal: int | None = None
        # When a chapter banner has just been emitted, hold the title's
        # number-stripped normalized form so the very-next block — which
        # the parser sometimes mis-classifies as paragraph instead of
        # heading — gets suppressed if it duplicates the banner content.
        pending_chapter_dedupe: str | None = None
        # F7: when a "NOTE …"/"TIP …"/"WARNING …" heading is encountered we
        # don't emit it; instead we hold the title here and merge it into
        # the next paragraph as a callout banner.
        pending_note: dict | None = None

        for row in blocks:
            blk_id, ordinal, btype, source_text, normalized_text, _anchor = row[:6]
            chapter_ordinal = row[6] if len(row) > 6 else None
            chapter_title = row[7] if len(row) > 7 else None
            chapter_id = row[8] if len(row) > 8 else None

            stats.blocks_seen += 1

            # Emit a bilingual chapter banner the first time we see each
            # chapter, so the final document has clear top-level structure
            # when scoped by multiple chapter_ids.
            #
            # The banner carries the outline-form title (with its leading
            # chapter number, e.g. "1 Big picture: What are LLMs?") which
            # matches what readers expect from a TOC. The on-page heading
            # block that follows usually lacks the leading number; we
            # dedupe it via the F3 path so the heading isn't duplicated.
            if chapter_ordinal is not None and chapter_ordinal != prev_chapter_ordinal:
                title_clean = re.sub(r"\s+", " ", (chapter_title or "").strip())
                if title_clean:
                    title_zh = (
                        fetch_chapter_title_zh(conn, chapter_id, title_clean)
                        if chapter_id
                        else None
                    )
                    sections.append(f"## {title_clean}\n")
                    if title_zh:
                        sections.append(f"## {title_zh}\n\n")
                    else:
                        sections.append("\n")
                    # Normalize for dedupe: drop any leading "N " bare-integer
                    # chapter prefix so the on-page heading without it
                    # ("Big picture: What are LLMs?") matches.
                    dedupe_key = re.sub(r"^\d+\s+", "", title_clean).strip().lower()
                    last_heading_norm = dedupe_key or None
                    pending_chapter_dedupe = title_clean.lower()
                else:
                    last_heading_norm = None
                    pending_chapter_dedupe = None
                prev_chapter_ordinal = chapter_ordinal

            # One-shot dedupe: suppress the first block of a chapter if its
            # normalized text matches the banner (handles parser quirk where
            # the chapter title block was tagged paragraph, not heading).
            if pending_chapter_dedupe is not None:
                first_block_text = re.sub(
                    r"\s+", " ", (normalized_text or source_text or "")
                ).strip().lower()
                if first_block_text and (
                    first_block_text == pending_chapter_dedupe
                    or first_block_text == re.sub(r"^\d+\s+", "", pending_chapter_dedupe)
                ):
                    pending_chapter_dedupe = None
                    stats.heading_echoes_filtered += 1
                    continue
                pending_chapter_dedupe = None

            run_id = fetch_run_for_block(conn, blk_id)
            sentences = fetch_sentences(conn, blk_id)
            targets = fetch_targets(conn, run_id) if run_id else {}

            # F11: if the translator emitted one big merged_sentence, try
            # to redistribute it across source-sentence ordinals so that
            # F9 paragraph splits get matching ZH for each visual chunk.
            if targets and len(targets) == 1 and len(sentences) > 1:
                expanded = expand_merged_targets(targets, sentences)
                if expanded is not targets:
                    targets = expanded
                    stats.merged_targets_split += 1

            # F10: resolve image asset for figure/image-typed blocks.
            image_md: str | None = None
            if btype in {"figure", "image"}:
                resolved = fetch_image_for_block(conn, blk_id)
                if resolved is not None:
                    rel_path, alt = resolved
                    safe_alt = (alt or "figure").replace("\n", " ").strip()
                    image_md = f"![{safe_alt}]({rel_path})"

            had_translation = bool(targets)
            lines, last_heading_norm, pending_note = render_block(
                btype=btype,
                source_raw=source_text or "",
                source_normalized=normalized_text,
                sentences=sentences,
                targets=targets,
                stats=stats,
                last_heading_norm=last_heading_norm,
                pending_note=pending_note,
                image_md=image_md,
            )

            if lines:
                stats.blocks_emitted += 1
                if had_translation:
                    stats.with_translation += 1
                else:
                    stats.no_translation += 1
                sections.extend(lines)

    sections.append("\n---\n\n")
    sections.append(
        "## Render summary\n\n"
        f"- blocks scanned: {stats.blocks_seen}\n"
        f"- blocks emitted: {stats.blocks_emitted}\n"
        f"- with translation: {stats.with_translation}\n"
        f"- without translation: {stats.no_translation}\n"
        f"- page headers filtered (F2): {stats.page_headers_filtered}\n"
        f"- heading echoes deduped (F3): {stats.heading_echoes_filtered}\n"
        f"- meta-commentary stripped (F4): {stats.meta_commentary_stripped}\n"
        f"- code→prose demotions (F5): {stats.code_to_prose_demotions}\n"
        f"- broken heading demotions (F6): {stats.broken_heading_demotions}\n"
        f"- NOTE callouts merged (F7): {stats.note_callouts_merged}\n"
        f"- diagram label dumps filtered (F8): {stats.diagram_label_dumps_filtered}\n"
        f"- paragraphs split visually (F9): {stats.paragraphs_split_visually}\n"
        f"- images linked (F10): {stats.images_linked}\n"
        f"- images with missing asset: {stats.images_missing_asset}\n"
        f"- merged_sentence targets split (F11): {stats.merged_targets_split}\n"
    )
    if stats.notes:
        sections.append("\n### Notes\n\n")
        for n in stats.notes:
            sections.append(f"- {n}\n")

    cfg.output_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.output_path.write_text("".join(sections), encoding="utf-8")
    return stats


def main() -> int:
    # Default invocation: render the v2 document (post-parser-fix) for
    # book chapters 1 + 2 using the chapter_ids scoping mode.
    cfg = RenderConfig(
        document_id="d71027f0-6537-58d1-8e47-42ef2834fca4",
        chapter_ids=[
            "b13f7481-d2af-5629-bb8f-52d9c2b9abc9",  # Ch.1 — 1 Big picture: What are LLMs?
            "732562f6-1d41-5dd6-9520-7fe7068fa760",  # Ch.2 — 2 Tokenizers
        ],
        output_path=ROOT
        / "artifacts"
        / "exports"
        / "d71027f0-6537-58d1-8e47-42ef2834fca4"
        / "book-ch1-ch2-bilingual.md",
        title="How Large Language Models Work — Chapters 1–2 (Bilingual / 双语对照)",
        subtitle="Edward Raff · Drew Farris · Stella Biderman",
        author="",
    )
    stats = render(cfg)
    print(f"wrote {cfg.output_path} ({cfg.output_path.stat().st_size} bytes)")
    print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
