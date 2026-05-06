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


def is_page_running_header(text_value: str) -> bool:
    """True if a paragraph block is a PDF page running-header artifact (F2)."""
    if not text_value:
        return False
    stripped = text_value.strip()
    if _PAGE_NUMBER_ONLY.match(stripped):
        return True
    if _PAGE_HEADER_WITH_CHAPTER.search(stripped):
        # Allow if there's substantial content after the running-header line.
        # Real chapter content would be much longer than the running-header line.
        lines = [ln for ln in stripped.splitlines() if ln.strip()]
        return len(lines) <= 3
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
    page_headers_filtered: int = 0  # F2
    heading_echoes_filtered: int = 0  # F3
    meta_commentary_stripped: int = 0  # F4
    code_to_prose_demotions: int = 0  # F5
    broken_heading_demotions: int = 0  # F6
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
                  c.title_src AS chapter_title
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
              NULL::text AS chapter_title
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
) -> tuple[list[str], str | None]:
    """Render one block to MD lines.

    Returns (lines, updated_last_heading_norm).
    Empty list ⇒ block was filtered out and produced nothing.
    """
    lines: list[str] = []

    # F1: prefer normalized_text for headings; fall back to source with
    # newlines collapsed.
    canonical_source = clean_text(source_normalized) or clean_text(source_raw).replace("\n", " ")
    canonical_source = re.sub(r"\s+", " ", canonical_source).strip()

    if not canonical_source:
        return lines, last_heading_norm

    # Block-type re-routing (F5, F6).
    effective_type = btype
    if btype == "code" and is_actually_prose(source_raw or canonical_source):
        effective_type = "paragraph"
        stats.code_to_prose_demotions += 1
    if btype == "heading" and looks_like_broken_list_item(canonical_source):
        effective_type = "paragraph"
        stats.broken_heading_demotions += 1

    # F2: drop page running-headers tagged as paragraphs.
    if effective_type == "paragraph" and is_page_running_header(source_raw or canonical_source):
        stats.page_headers_filtered += 1
        return lines, last_heading_norm

    # F3: heading echoes from page running-headers — strip trailing page
    # number; if the resulting heading is identical to the previous one,
    # drop it.
    if effective_type == "heading":
        de_paged = strip_trailing_page_number_echo(canonical_source)
        normalized_for_dedupe = re.sub(r"\s+", " ", de_paged).strip().lower()
        if last_heading_norm and normalized_for_dedupe == last_heading_norm:
            stats.heading_echoes_filtered += 1
            return lines, last_heading_norm
        canonical_source = de_paged
        new_last_heading = normalized_for_dedupe
    else:
        new_last_heading = last_heading_norm

    # ZH text from sentence-aligned targets, with F4 cleanup.
    # Translations are always treated as inline text — internal whitespace
    # is collapsed so that an embedded newline can never escape a
    # blockquote (footnote/caption) or shatter a paragraph.
    def zh_for_sentence(s_ord: int) -> str:
        tgt = targets.get(s_ord) if targets else None
        if not tgt or not tgt[1]:
            return ""
        before = clean_text(tgt[1])
        after = strip_llm_meta_commentary(before)
        if after != before:
            stats.meta_commentary_stripped += 1
        return re.sub(r"\s+", " ", after).strip()

    if effective_type == "heading":
        level = heading_level(canonical_source)
        hashes = "#" * level
        # First non-empty translated sentence wins for heading.
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
        return lines, new_last_heading

    if effective_type in {"figure", "image"}:
        # Don't emit anything for empty figure placeholders — they're
        # noise without the actual image asset.
        if not canonical_source:
            return lines, last_heading_norm
        lines.append(f"> _[图/{effective_type}]: {canonical_source[:160]}_\n\n")
        return lines, last_heading_norm

    if effective_type == "table":
        lines.append(f"> _[表/table]: {canonical_source[:200]}_\n\n")
        return lines, last_heading_norm

    if effective_type == "equation":
        lines.append(f"$$\n{canonical_source}\n$$\n\n")
        return lines, last_heading_norm

    if effective_type == "code":
        lines.append("```\n" + (source_raw or canonical_source).strip() + "\n```\n\n")
        return lines, last_heading_norm

    if effective_type == "caption":
        en = canonical_source
        zh_parts = [zh_for_sentence(s_ord) for s_ord, _, _ in sentences]
        zh_parts = [p for p in zh_parts if p]
        lines.append(f"> _Caption (EN):_ {en}\n>\n")
        if zh_parts:
            lines.append(f"> _图说 (ZH):_ {' '.join(zh_parts)}\n\n")
        else:
            lines.append("\n")
        return lines, last_heading_norm

    # paragraph / list_item / footnote / default — interleave per sentence.
    en_parts: list[str] = []
    zh_parts: list[str] = []
    for s_ord, s_src, _ in sentences:
        s_clean = clean_text(s_src)
        if not s_clean:
            continue
        en_parts.append(s_clean)
        zh = zh_for_sentence(s_ord)
        if zh:
            zh_parts.append(zh)
    en_block = " ".join(en_parts)
    zh_block = "".join(zh_parts)

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
    else:  # paragraph
        if not en_block:
            return lines, last_heading_norm
        lines.append(f"{en_block}\n\n")
        if zh_block:
            lines.append(f"{zh_block}\n\n")

    return lines, last_heading_norm


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

        for row in blocks:
            blk_id, ordinal, btype, source_text, normalized_text, _anchor = row[:6]
            chapter_ordinal = row[6] if len(row) > 6 else None
            chapter_title = row[7] if len(row) > 7 else None

            stats.blocks_seen += 1

            # Emit a chapter banner the first time we see each chapter, so the
            # final document has clear top-level structure when scoped by
            # multiple chapter_ids.
            if chapter_ordinal is not None and chapter_ordinal != prev_chapter_ordinal:
                title_clean = re.sub(r"\s+", " ", (chapter_title or "").strip())
                if title_clean:
                    sections.append(f"## {title_clean}\n\n")
                prev_chapter_ordinal = chapter_ordinal
                last_heading_norm = None  # don't dedupe across chapter boundaries

            run_id = fetch_run_for_block(conn, blk_id)
            sentences = fetch_sentences(conn, blk_id)
            targets = fetch_targets(conn, run_id) if run_id else {}

            had_translation = bool(targets)
            lines, last_heading_norm = render_block(
                btype=btype,
                source_raw=source_text or "",
                source_normalized=normalized_text,
                sentences=sentences,
                targets=targets,
                stats=stats,
                last_heading_norm=last_heading_norm,
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
