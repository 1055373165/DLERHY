"""Patterns, constants and small text helpers shared by the export renderers."""


from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from book_agent.domain.enums import (
    ExportType,
    Severity,
    SourceType,
)
from book_agent.domain.structure.epub import (
    _join_path,
    _local_name,
)
from book_agent.ingestion.pdf.classify import (
    _HEADING_CONTINUATION_START_WORDS,
    _LEADING_SECTION_NUMBER_PATTERN,
)

_SPECIAL_PDF_PAGE_FAMILIES = {"frontmatter", "appendix", "references", "index", "backmatter", "toc"}
_DOCUMENT_IMAGE_MATERIALIZATION_VERSION = 3
_PDF_IMAGE_MIN_RENDER_SCALE = 4.0
_PDF_IMAGE_MAX_RENDER_SCALE = 8.0
_PDF_IMAGE_TARGET_LONG_EDGE_PX = 1800
_SEVERITY_RANK = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class _FallbackEpubFigureIndexParser(HTMLParser):
    def __init__(self, *, base_dir: str, path_normalizer) -> None:
        super().__init__(convert_charrefs=True)
        self.base_dir = base_dir
        self.path_normalizer = path_normalizer
        self.archive_path_by_caption_signature: dict[str, str] = {}
        self._figure_stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        local_tag = _local_name(tag).casefold()
        attr_map = {str(key).lower(): value or "" for key, value in attrs}
        if local_tag == "figure":
            self._figure_stack.append({"src": None, "caption_parts": [], "figcaption_depth": 0})
            return
        if not self._figure_stack:
            return
        current = self._figure_stack[-1]
        if local_tag == "img" and not current.get("src"):
            src = str(attr_map.get("src") or "").strip()
            if src:
                current["src"] = src
            return
        if local_tag == "figcaption":
            current["figcaption_depth"] = int(current.get("figcaption_depth") or 0) + 1
            return
        if local_tag == "br" and int(current.get("figcaption_depth") or 0) > 0:
            caption_parts = current.get("caption_parts")
            if isinstance(caption_parts, list):
                caption_parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        local_tag = _local_name(tag).casefold()
        if not self._figure_stack:
            return
        current = self._figure_stack[-1]
        if local_tag == "figcaption":
            current["figcaption_depth"] = max(int(current.get("figcaption_depth") or 0) - 1, 0)
            return
        if local_tag != "figure":
            return
        figure = self._figure_stack.pop()
        src = str(figure.get("src") or "").strip()
        caption_parts = figure.get("caption_parts")
        caption_text = _normalize_render_text("".join(caption_parts)) if isinstance(caption_parts, list) else ""
        caption_signature = _normalize_figure_caption_signature(caption_text)
        if not src or not caption_signature:
            return
        archive_path = self.path_normalizer(_join_path(self.base_dir, src))
        if archive_path is None:
            return
        self.archive_path_by_caption_signature.setdefault(caption_signature, archive_path)

    def handle_data(self, data: str) -> None:
        if not self._figure_stack:
            return
        current = self._figure_stack[-1]
        if int(current.get("figcaption_depth") or 0) <= 0:
            return
        caption_parts = current.get("caption_parts")
        if isinstance(caption_parts, list):
            caption_parts.append(data)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _excerpt_text(text: str, *, limit: int = 220) -> str:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _normalize_render_text(text: str | None) -> str:
    sanitized = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff]", "", text or "")
    return re.sub(r"\s+", " ", sanitized).strip()


def _normalize_signature_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def _normalize_figure_caption_signature(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).strip().casefold()


def _leading_whitespace_width(text: str) -> int:
    expanded = (text or "").replace("\t", "    ")
    return len(expanded) - len(expanded.lstrip(" "))


def _looks_like_metadata_filename(value: str) -> bool:
    candidate = re.sub(r"\s+", " ", (value or "")).strip().casefold()
    if not candidate:
        return False
    if "/" in candidate or "\\" in candidate:
        return True
    return candidate.endswith((".html", ".xhtml", ".htm", ".xml", ".opf", ".ncx"))


def _document_export_label(export_type: ExportType) -> str:
    if export_type == ExportType.MERGED_HTML:
        return "中文阅读稿"
    if export_type == ExportType.MERGED_MARKDOWN:
        return "中文阅读稿-Markdown"
    if export_type == ExportType.ZH_EPUB:
        return "中文EPUB"
    if export_type == ExportType.REBUILT_EPUB:
        return "重建EPUB"
    if export_type == ExportType.REBUILT_PDF:
        return "重建PDF"
    return export_type.value


def _pdf_profile_payload(document) -> dict[str, object]:
    metadata = getattr(document, "metadata_json", None)
    if not isinstance(metadata, dict):
        return {}
    profile = metadata.get("pdf_profile")
    return profile if isinstance(profile, dict) else {}


def _is_academic_paper_document(document) -> bool:
    return str(_pdf_profile_payload(document).get("recovery_lane") or "").strip() == "academic_paper"


def _is_pdf_document(document) -> bool:
    return getattr(document, "source_type", None) in {
        SourceType.PDF_TEXT,
        SourceType.PDF_MIXED,
        SourceType.PDF_SCAN,
    }


def _looks_like_heading_continuation_fragment(text: str | None) -> bool:
    normalized = _normalize_render_text(text)
    if not normalized or len(normalized) > 80:
        return False
    if _MAIN_CHAPTER_TITLE_PATTERN.match(normalized) or _LEADING_SECTION_NUMBER_PATTERN.match(normalized):
        return False
    lead = normalized.split(" ", 1)[0].casefold()
    if lead in _HEADING_CONTINUATION_START_WORDS:
        return True
    return normalized[:1].islower()


_CODE_BLOCK_KEYWORD_PATTERN = re.compile(
    r"^(?:"
    r"async\s+def\b.+:"
    r"|def\b.+:"
    r"|class\b.+:"
    r"|import\b.+"
    r"|from\s+\S+\s+import\b.+"
    r"|return\b.+"
    r"|yield\b.+"
    r"|raise\b.+"
    r"|pass\b.*"
    r"|break\b.*"
    r"|continue\b.*"
    r"|try:"
    r"|finally:"
    r"|except\b.*:"
    r"|if\b.+:"
    r"|elif\b.+:"
    r"|else:"
    r"|for\b.+\bin\b.+:"
    r"|while\b.+:"
    r"|with\b.+:"
    r")$",
    re.IGNORECASE,
)
_CODE_ASSIGNMENT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,80}(?::\s*[A-Za-z_][A-Za-z0-9_\[\],. ]{0,80})?\s*=\s*.+$"
)
_OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_ ]{0,80}\s*=\s*.+$"
)
_MAIN_CHAPTER_TITLE_PATTERN = re.compile(r"^\s*chapter\s+(\d+)(?=\b|_)", re.IGNORECASE)
_APPENDIX_TITLE_PATTERN = re.compile(r"^\s*appendix\b", re.IGNORECASE)
_FIGURE_CAPTION_PATTERN = re.compile(r"^\s*(?:fig(?:ure)?\.?|image|diagram|chart)\b", re.IGNORECASE)
_ACADEMIC_CITATION_PATTERN = re.compile(r"\[[^\]]+\d{4}[^\]]*\]")
_ACADEMIC_FRONTMATTER_MARKER_PATTERN = re.compile(
    r"\b(?:abstract|keywords?)\b|@|"
    r"\b(?:university|institute|department|school|laboratory|center|centre|society|sciences?)\b",
    re.IGNORECASE,
)
_LIST_MARKER_PATTERN = re.compile(r"^[\s\u200b\ufeff]*(?:[-*+•●▪◦○◯])[\s\u200b\ufeff]+")
_ORDERED_LIST_MARKER_PATTERN = re.compile(r"^[\s\u200b\ufeff]*(?:\[\d+\]|\d+[.)])[\s\u200b\ufeff]+")
_UNORDERED_LIST_LINE_PATTERN = re.compile(
    r"^(?P<indent>[\s\u200b\ufeff]*)(?P<marker>[-*+•●▪◦○◯])[\s\u200b\ufeff]+(?P<body>.+)$"
)
_ORDERED_LIST_LINE_PATTERN = re.compile(
    r"^(?P<indent>[\s\u200b\ufeff]*)(?P<marker>\[\d+\]|\d+[.)])[\s\u200b\ufeff]+(?P<body>.+)$"
)
_CJK_CHAR_PATTERN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_REFERENCE_ENTRY_MARKER_PATTERN = re.compile(r"^[\s\u200b\ufeff]*\d+[.)][\s\u200b\ufeff]+")
_REFERENCE_LOCATOR_PATTERN = re.compile(r"(?:https?://|doi\.org/|arxiv:)\S+", re.IGNORECASE)
_URL_ONLY_PATTERN = re.compile(r"^[\s\u200b\ufeff]*https?://\S+[\s\u200b\ufeff]*$", re.IGNORECASE)
_REFERENCE_TARGET_ENTRY_PATTERN = re.compile(r"\d+[.)]\s+.*?(?=(?:\s+\d+[.)]\s+)|$)")
_REFERENCE_LOCATOR_CONTINUATION_PATTERN = re.compile(r"^[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")
_INLINE_CODE_LIKE_PATTERN = re.compile(
    r"(?:"
    r"\bdef\s+[A-Za-z_][A-Za-z0-9_]*\s*\("
    r"|\bclass\s+[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?\s*:"
    r"|\breturn\s+\S+"
    r"|\byield\s+\S+"
    r"|\braise\s+[A-Za-z_]"
    r"|\blambda\b[^\\n:]+:"
    r"|\bfrom\s+\S+\s+import\s+\S+"
    r"|\bimport\s+\S+(?:\s+as\s+\S+)?"
    r"|\btry:"
    r"|\bexcept\b.*:"
    r"|\bfinally:"
    r"|\bif\b.+:"
    r"|\belif\b.+:"
    r"|\belse:"
    r"|\bfor\b.+\bin\b.+:"
    r"|\bwhile\b.+:"
    r"|\bwith\b.+:"
    r"|==|!=|:=|->|=>|\{|\}|\[|\]|</?\w+>|`"
    r")",
    re.IGNORECASE,
)
_PROSE_ARTIFACT_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "them",
    "these",
    "this",
    "those",
    "through",
    "to",
    "was",
    "we",
    "what",
    "when",
    "which",
    "while",
    "with",
    "you",
    "your",
}
_FRONTMATTER_TITLES = {
    "acknowledgment",
    "acknowledgements",
    "dedication",
    "foreword",
    "preface",
    "prologue",
    "introduction",
}
_FRONTMATTER_TITLE_TRANSLATIONS = {
    "acknowledgment": "致谢",
    "acknowledgements": "致谢",
    "acknowledgments": "致谢",
    "dedication": "献词",
    "foreword": "前言",
    "preface": "前言",
    "prologue": "序章",
    "introduction": "引言",
    # Parser-made chapter for the pages before the first real chapter (cover, copyright, contents).
    "front matter": "卷首",
}
# A translated heading that kept the English chapter label ("CHAPTER 11：额外技巧").
_LEADING_ENGLISH_CHAPTER_LABEL = re.compile(r"^\s*chapter\s+(\d+)\s*[:：.\-–—]?\s*", re.IGNORECASE)
_PURE_CHAPTER_LABEL_PATTERN = re.compile(r"^\s*chapter\s+\d+[.)]?\s*$", re.IGNORECASE)
_CHAPTER_LOWERCASE_TAIL_PATTERN = re.compile(r"^\s*chapter\s+\d+[.):]?\s+[a-z]", re.IGNORECASE)
_CJK_MAIN_CHAPTER_TITLE_PATTERN = re.compile(
    r"^\s*第[一二三四五六七八九十百千万零〇两0-9]+(?:章|节|篇|部分)\s*[：:].+"
)
_CJK_APPENDIX_TITLE_PATTERN = re.compile(r"^\s*附录\s*[A-Za-z一二三四五六七八九十百千万零〇两0-9]")
_BOOK_STRUCTURAL_HEADING_TITLES = {
    "acknowledgment",
    "acknowledgements",
    "dedication",
    "foreword",
    "preface",
    "introduction",
    "conclusion",
    "references",
    "appendix",
    "致谢",
    "前言",
    "引言",
    "结论",
    "参考文献",
}
_BOOK_ALLOWED_REFERENCE_HEADINGS = {
    "references",
    "参考文献",
    "conclusion",
    "key takeaways",
    "overview",
    "glossary",
    "index",
}
_BOOK_PROSE_HEADING_VERB_PATTERN = re.compile(
    r"\b(?:"
    r"is|are|was|were|be|being|been|include|includes|required|requires|requiring|"
    r"returns?|returned|provides?|provided|prints?|printed|demonstrates?|"
    r"allow(?:s|ed|ing)?|instantiate(?:s|d)?|initialized|creates?|created|"
    r"stored|saving|saved|contains?|containing|uses?|using|executes?|executed|"
    r"performs?|performing|organizing|organized|designed|named"
    r")\b",
    re.IGNORECASE,
)
_SINGLE_LINE_CODEISH_PATTERN = re.compile(
    r"(?:"
    r"^\s*#"
    r"|^\s*@"
    r"|^\s*(?:async\s+def|def|class|from|import|return|yield|raise)\b"
    r"|^\s*(?:if|elif|for|while|with|except)\b.+:\s*$"
    r"|^\s*else\s*:"
    r"|^\s*try\s*:"
    r"|(?:^|\s)(?:print|invoke|Agent|LlmAgent|Runner|Runnable|ChatPromptTemplate|StrOutputParser)\s*\("
    r"|session\.state\s*\["
    r"|```"
    r"|->"
    r"|=>"
    r")",
    # Case-sensitive: keywords are lowercase in code, while prose opens
    # sentences with "If ...:", "From ...", "Return ...".
)
_GLOSSARY_DEFINITION_LINE_PATTERN = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9/&'(). -]{0,80}?):\s+(?P<body>.+)$"
)
_GLOSSARY_CODEISH_LABEL_STARTERS = {
    "async",
    "await",
    "break",
    "case",
    "class",
    "continue",
    "def",
    "elif",
    "else",
    "except",
    "finally",
    "for",
    "from",
    "if",
    "import",
    "match",
    "pass",
    "raise",
    "return",
    "try",
    "while",
    "with",
    "yield",
}
