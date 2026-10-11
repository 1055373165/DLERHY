"""Citation and reference entries, and the English titles of cited works.

Technical books end a chapter with numbered notes such as

    4 Gabriel, Iason et al. 2024. "The Ethics of Advanced AI Assistants." *arXiv,* 2404.16244.

and the book's convention (kept by the translation) leaves a cited work's title in English. So
a locked term inside such a title is not a term to translate ("Fully Autonomous AI Agents Should
Not Be Developed." must not become "Fully Autonomous AI 智能体 …"), a "Not" in it is not a
negation the translation lost, and an entry that reads mostly English is not untranslated prose.

``altered_titles`` finds a title the translation kept in English but changed inside.

``is_citation_entry`` recognises an entry from its parts (a note number, an author list, a year,
a quoted or italic title, a venue, an arXiv id, a DOI or a URL); ``title_spans`` finds the quoted
English titles inside any text.
"""

from __future__ import annotations

import re

# "4 Gabriel, Iason et al." / "1 Russell, Stuart and Peter Norvig," / "Dao, Tri, ..." / "[3] Vaswani, A."
_NOTE_NUMBER_RE = re.compile(r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)]?|[•–\-])\s+")
_AUTHOR_RE = re.compile(
    # A note marker may be a letter set against the name ("aDastin, Jeffrey.").
    r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)]?|[•–\-]|[a-z](?=[A-Z][a-z]))?\s*"
    r"(?:"
    # "Gabriel, Iason et al." / "Russell, Stuart and Peter Norvig,"
    r"[A-Z][A-Za-z'’\-]+,\s+(?:[A-Z][A-Za-z'’\-]*\.?\s*){1,3}"
    r"(?:(?:et al\.?|等)|(?:,?\s*(?:and|&)\s+[A-Z])|,|\.)"
    r"|"
    # IEEE: "T. Mikolov, K. Chen" / "J. S. Park," / "S. Russell and P. Norvig,"
    r"(?:[A-Z]\.\s*){1,3}[A-Z][\w'’\-]+(?:,|\s+(?:and|&)\s+[A-Z]\.)"
    r")"
)
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}[a-z]?(?!\d)")
_ARXIV_RE = re.compile(r"\barXiv\b|(?<![\d.])\d{4}\.\d{4,5}(?:v\d+)?(?![\d.])", re.IGNORECASE)
_DOI_RE = re.compile(r"\bdoi\b|doi\.org/|\b10\.\d{4,9}/\S+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_VENUE_RE = re.compile(
    r"\b(?:Proceedings|Conference|Journal|Transactions|Workshop|Symposium|Nature|Science|NeurIPS|NIPS|ICLR|ICML|"
    r"ACL|EMNLP|NAACL|CVPR|AAAI|IJCAI|arXiv|preprint|Press|Publishing|Pearson|O[’']Reilly|Manning|ed\.|pp\.|vol\.)\b"
)
# A quoted title: “…”, "…" (not ‘…’, which is mostly an apostrophe), or an emphasised one (*…*).
_QUOTED_RE = re.compile(r"“(?P<a>[^“”]{3,300})”|\"(?P<b>[^\"]{3,300})\"")
_ITALIC_TITLE_RE = re.compile(r"(?<![A-Za-z0-9_*])\*(?P<c>[^*\n]{3,300}?)\*(?!\*)")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
# Small words a title leaves lower case.
_MINOR_WORDS = frozenset(
    "a an and as at but by for from in into nor of on or per so the to up via vs with without".split()
)


def is_english_title(text: str) -> bool:
    """Words set as a title: three or more English words, the significant ones capitalised.

    "Fully Autonomous AI Agents Should Not Be Developed." and "The Ethics of Advanced AI
    Assistants." are; a quoted sentence in prose ("the agent should not answer") is not.
    """
    words = _WORD_RE.findall(text or "")
    if len(words) < 3:
        return False
    if re.search(r"[㐀-鿿]", text):
        return False
    significant = [word for word in words if word.lower() not in _MINOR_WORDS]
    if not significant:
        return False
    capitalised = sum(1 for word in significant if word[0].isupper())
    return capitalised / len(significant) >= 0.75 and words[0][0].isupper()


def title_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each quoted English title in ``text``, quotes included."""
    spans: list[tuple[int, int]] = []
    for match in _QUOTED_RE.finditer(text or ""):
        inner = match.group("a") if match.group("a") is not None else match.group("b")
        if is_english_title(inner):
            spans.append((match.start(), match.end()))
    return spans


# "J. Kirkpatrick, R. Pascanu, N. Rabinowitz, J. Veness, G. Desjardins, A. A. Rusu, K." — an entry's
# author list on a line of its own (the rest of the entry set apart by the parse).
_AUTHOR_LIST_RE = re.compile(
    r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)]?|[•–\-])?\s*"
    r"(?:(?:[A-Z]\.\s*){1,3}[A-Z][\w'’\-]+,\s*){3,}(?:(?:and\s+)?(?:[A-Z]\.\s*){1,3}(?:[A-Z][\w'’\-]+)?[,.]?)?\s*$"
)


def is_citation_entry(text: str) -> bool:
    """A bibliographic entry: a note or reference of an author list, a year and a work.

    It opens with its author list ("4 Gabriel, Iason et al.") or with the work's title, and has
    at least three of: a note number, the author list, a year, a quoted or italic English title,
    a venue or publisher, an arXiv id, a DOI or URL. A sentence that cites a paper in passing
    ("Gabriel et al. (2024) showed …") opens with neither and is prose.
    """
    text = (text or "").strip()
    if not text or len(text) > 600:
        return False
    if _AUTHOR_LIST_RE.match(text):
        return True
    author = bool(_AUTHOR_RE.match(text))
    body = _NOTE_NUMBER_RE.sub("", text, count=1)
    lead_title = body[:1] in {"“", '"', "*"} and (
        any(start == 0 for start, _end in title_spans(body))
        or any(match.start() == 0 and is_english_title(match.group("c")) for match in _ITALIC_TITLE_RE.finditer(body))
    )
    if not (author or lead_title):
        return False
    title = bool(title_spans(text)) or any(is_english_title(match.group("c")) for match in _ITALIC_TITLE_RE.finditer(text))
    parts = [
        bool(_NOTE_NUMBER_RE.match(text)),
        author,
        bool(_YEAR_RE.search(text)),
        title,
        bool(_VENUE_RE.search(text)),
        bool(_ARXIV_RE.search(text)) or bool(_DOI_RE.search(text)) or bool(_URL_RE.search(text)),
    ]
    return sum(parts) >= 3


def _inner(quoted: str) -> str:
    return quoted[1:-1]


def altered_titles(source: str, target: str) -> list[tuple[str, str]]:
    """English titles the translation kept in English but changed inside: (source title, shown).

    "Fully Autonomous AI Agents Should Not Be Developed." came out as "Fully Autonomous AI 智能体
    Should Not Be Developed.": a term rendering forced into the title. A title translated as a
    whole (《…》 or a Chinese quote) or kept as it is, is fine.
    """
    altered: list[tuple[str, str]] = []
    target_quotes = [match.group(0) for match in _QUOTED_RE.finditer(target or "")]
    for start, end in title_spans(source or ""):
        title = source[start:end]
        if _inner(title) in (target or ""):
            continue
        words = {word.lower() for word in _WORD_RE.findall(title)}
        for quoted in target_quotes:
            if not re.search(r"[㐀-鿿]", quoted):
                continue
            shown = {word.lower() for word in _WORD_RE.findall(quoted)}
            # Mostly the title's own English words, with Chinese put in among them.
            if words and len(shown & words) >= max(3, int(0.6 * len(words))) and len(shown - words) <= 1:
                altered.append((title, quoted))
                break
    return altered
