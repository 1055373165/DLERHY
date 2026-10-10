"""A blog article's title, excerpt and SEO fields for a book's reading copy, by rule (no model call).

Title: the book's original (English) title, the Chinese one when there is none (the bilingual
edition says so). Excerpt: who wrote the book and what it
is, then the opening of its preface or introduction, or failing that its chapters. SEO: the title
with the source title, the excerpt cut at a sentence, and keywords from the title, the authors and
the book's most used glossary terms.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

EXCERPT_MAX = 200
META_TITLE_MAX = 60
META_DESCRIPTION_MAX = 150
KEYWORD_COUNT = 10

# The sections that say what a book is about, in the order they are preferred.
_INTRO_TITLES = ("前言", "序言", "序", "简介", "导读", "引言", "关于本书", "本书简介", "内容简介")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_CHAPTER_PREFIX = re.compile(r"^第\s*[0-9一二三四五六七八九十百]+\s*章\s*")
_SENTENCE_END = re.compile(r"(?<=[。！？!?])")
_AUTHOR_SPLIT = re.compile(r"\s*(?:;|；|,|，|、|\band\b|&)\s*")


@dataclass(frozen=True, slots=True)
class BlogMetadata:
    title: str
    excerpt: str
    meta_title: str
    meta_description: str
    meta_keywords: str


def _plain(text: str) -> str:
    """Markdown inline marks out: links keep their text, emphasis and code marks go."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


def _sections(markdown: str) -> list[tuple[int, str, list[str]]]:
    """(heading level, heading text, body lines) of every heading, code blocks skipped."""
    sections: list[tuple[int, str, list[str]]] = []
    in_code = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = _HEADING.match(line)
        if match:
            sections.append((len(match.group(1)), _plain(match.group(2)), []))
        elif sections:
            sections[-1][2].append(line)
    return sections


def _paragraphs(lines: Sequence[str]) -> list[str]:
    """The body's prose paragraphs: no quotes, lists, tables, pictures or HTML."""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in [*lines, ""]:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(_plain(" ".join(current)))
                current = []
            continue
        if stripped.startswith((">", "-", "*", "+", "|", "!", "<")) or re.match(r"^\d+\.\s", stripped):
            if current:
                paragraphs.append(_plain(" ".join(current)))
                current = []
            continue
        current.append(stripped)
    return [paragraph for paragraph in paragraphs if len(paragraph) >= 20]


def _cut(text: str, limit: int) -> str:
    """At most ``limit`` characters, ending at a sentence when one ends in time."""
    text = text.strip()
    if len(text) <= limit:
        return text
    sentences = [piece for piece in _SENTENCE_END.split(text) if piece]
    out = ""
    for sentence in sentences:
        if len(out) + len(sentence) > limit:
            break
        out += sentence
    return out if len(out) >= limit // 2 else text[: limit - 1].rstrip("，、；：,;: ") + "…"


def _intro_text(sections: list[tuple[int, str, list[str]]]) -> str:
    for wanted in _INTRO_TITLES:
        for _level, heading, body in sections:
            if heading == wanted or heading.startswith(f"{wanted}：") or heading.startswith(f"{wanted} "):
                paragraphs = _paragraphs(body)
                if paragraphs:
                    return " ".join(paragraphs[:3])
    return ""


def _chapter_titles(sections: list[tuple[int, str, list[str]]]) -> list[str]:
    return [
        _CHAPTER_PREFIX.sub("", heading).strip()
        for _level, heading, _body in sections
        if _CHAPTER_PREFIX.match(heading)
    ]


def build_blog_metadata(
    *,
    markdown: str,
    title: str,
    source_title: str | None,
    authors: str | None,
    glossary_terms: Sequence[str] = (),
    bilingual: bool = False,
) -> BlogMetadata:
    title = " ".join(title.split())
    source_title = " ".join((source_title or "").split()) or None
    # The article is titled by the book's original name; the Chinese title leads the excerpt and SEO.
    article_title = source_title or title
    if source_title and source_title.casefold() == title.casefold():
        source_title = None
    author_names = [name for name in _AUTHOR_SPLIT.split(" ".join((authors or "").split())) if name]
    authors = "、".join(author_names) or None
    edition = "中英对照版" if bilingual else "中文版"
    sections = _sections(markdown)

    lead = f"《{title}》" + (f"（{source_title}）" if source_title else "")
    lead += f"{edition}，{authors} 著。" if authors else f"{edition}。"
    intro = _intro_text(sections)
    if not intro:
        chapters = _chapter_titles(sections)
        if chapters:
            intro = f"全书共 {len(chapters)} 章：" + "、".join(chapters) + "。"
    excerpt = _cut(lead + intro, EXCERPT_MAX)

    meta_title = f"《{title}》{edition}" + (f" | {source_title}" if source_title else "")
    if len(meta_title) > META_TITLE_MAX:
        meta_title = f"《{title}》{edition}"

    keywords: list[str] = []
    for keyword in [title, source_title, *author_names, *_ranked_terms(markdown, glossary_terms), edition, "书籍翻译"]:
        keyword = " ".join((keyword or "").split())
        if keyword and keyword.casefold() not in {k.casefold() for k in keywords}:
            keywords.append(keyword)
    return BlogMetadata(
        title=f"{article_title}（中英对照）" if bilingual else article_title,
        excerpt=excerpt,
        meta_title=meta_title,
        meta_description=_cut(excerpt, META_DESCRIPTION_MAX),
        meta_keywords=",".join(keywords[:KEYWORD_COUNT]),
    )


def _ranked_terms(markdown: str, terms: Sequence[str], count: int = 6) -> list[str]:
    """The glossary terms the book uses most (2-16 characters, not bare numbers)."""
    scored: list[tuple[int, str]] = []
    for term in dict.fromkeys(" ".join(term.split()) for term in terms):
        if not 2 <= len(term) <= 16 or term.isdigit():
            continue
        uses = markdown.count(term)
        if uses:
            scored.append((uses, term))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [term for _uses, term in scored[:count]]
