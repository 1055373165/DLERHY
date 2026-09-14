"""Line-level text heuristics: code, prose, list, table, equation and caption detection."""


from __future__ import annotations

import re
from collections import Counter

_TERMINAL_PUNCTUATION = (".", "!", "?", ":", ";", "\"", "'", "\u201d", "\u2019")
_HEADING_PATTERN = re.compile(r"^(chapter|part|appendix)\b", re.IGNORECASE)
_ARTIFACT_INDEX_PATTERN = r"(?:[A-Z]\d+(?:[.\-\u2013\u2014]\d+)*[A-Za-z]?|\(?\d+(?:[.\-\u2013\u2014]\d+)*[A-Za-z]?\)?|[A-Z])"
_FIGURE_CAPTION_PATTERN = re.compile(
    r"^(?:figure|fig\.?|image|diagram|chart)\s*"
    + _ARTIFACT_INDEX_PATTERN
    + r"(?:(?:[.:\-\u2013\u2014]\s+)\S+|\s+(?-i:[A-Z])[^\n]{2,})",
    re.IGNORECASE,
)
_TABLE_CAPTION_PATTERN = re.compile(
    r"^(?:table)\s+"
    + _ARTIFACT_INDEX_PATTERN
    + r"(?:(?:[.:\-\u2013\u2014]\s+)\S+|\s+(?-i:[A-Z])[^\n]{2,})",
    re.IGNORECASE,
)
_TABLE_HEADER_CUE_PATTERN = re.compile(
    r"\b(?:method|model|accuracy|team|individual|instances?|allocated|baseline(?:s)?|dataset|bleu|score(?:s)?|cost|layer(?:\s+type)?|complexity|operations?|path\s+length|maximum)\b",
    re.IGNORECASE,
)
_TABLE_BRACKETED_GROUP_PATTERN = re.compile(r"\[[^\]]+\]")
_TABLE_ID_EQUALS_PATTERN = re.compile(r"\bID\s*=\s*\d+\b", re.IGNORECASE)
_EQUATION_CAPTION_PATTERN = re.compile(
    r"^(?:eq(?:uation)?\.?)\s*(?:\(\s*\d+(?:\.\d+)*[A-Za-z]?\s*\)|\d+(?:\.\d+)*[A-Za-z]?)(?:[.:-]\s+)\S+",
    re.IGNORECASE,
)
_PAGE_NUMBER_PATTERN = re.compile(r"^(?:page\s+)?(?:\d+|[ivxlcdm]+)$", re.IGNORECASE)
_CODE_IMPORT_LINE_PATTERN = re.compile(
    r"^(?:"
    r"from\s+\S+\s+import\b.+"           # Python from-import
    r"|import\s+\S+.+"                     # Python/Java/Kotlin/Swift import
    r"|use\s+\S+.+"                        # Rust use / PHP use
    r"|#include\s*[<\"].+[>\"]"            # C/C++ #include
    r"|require(?:_relative|_once)?\s*[\s(].+" # Ruby require / PHP require
    r"|using\s+\S+.+"                      # C# using
    r"|include\s+\S+.+"                    # Ruby include / PHP include
    r"|package\s+\S+"                      # Go/Java/Kotlin package
    r"|module\s+\S+"                       # Rust/Ruby module
    r")$",
    re.IGNORECASE,
)
_CODE_CONTROL_LINE_PATTERN = re.compile(
    r"^(?:"
    # Python
    r"async\s+def\b.+:"
    r"|def\b.+:"
    r"|class\b.+:"
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
    # JavaScript/TypeScript
    r"|(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b.+"
    r"|(?:const|let|var)\s+[A-Za-z_]\w*\s*="
    r"|export\s+(?:default\s+)?(?:class|interface|type|enum|const|function)\b.+"
    # Go
    r"|func\b.+"
    r"|go\s+func\b.+"
    r"|select\s*\{"
    r"|case\b.+:"
    r"|defer\b.+"
    # Rust
    r"|(?:pub\s+)?fn\b.+"
    r"|(?:pub\s+)?struct\b.+"
    r"|(?:pub\s+)?enum\b.+"
    r"|(?:pub\s+)?trait\b.+"
    r"|(?:pub\s+)?mod\b.+"
    r"|impl\b.+"
    r"|match\b.+"
    r"|let\s+(?:mut\s+)?[A-Za-z_]\w*\s*[:=].+"
    # Java/C#/Kotlin
    r"|(?:public|private|protected|internal)\s+(?:static\s+)?(?:final\s+)?(?:class|interface|enum|void|abstract|record)\b.+"
    r"|(?:public|private|protected|internal)\s+(?:static\s+)?(?:final\s+)?\w+\s+\w+\s*\(.+"
    r"|switch\b.+"
    r"|(?:data\s+)?class\b.+\{"
    # C/C++
    r"|(?:int|void|char|double|float|bool|auto)\s+\w+\s*\(.+"
    r"|struct\s+\w+\s*\{"
    r"|typedef\b.+"
    r"|template\s*<.+"
    r"|namespace\s+\w+.+"
    r"|#define\b.+"
    r"|#ifdef\b.+"
    r"|#ifndef\b.+"
    r"|#endif\b.*"
    # Ruby
    r"|module\s+\w+.+"
    r"|begin\b.*"
    r"|rescue\b.*"
    r"|ensure\b.*"
    # Swift
    r"|guard\b.+"
    r"|(?:@\w+\s+)?(?:public\s+)?func\b.+"
    # PHP
    r"|(?:public|private|protected)\s+function\b.+"
    r"|namespace\s+\w+.+"
    r")$",
    re.IGNORECASE,
)
_SHELL_COMMAND_LINE_PATTERN = re.compile(
    r"^(?:"
    r"(?:(?:python|python3)\s+-m\s+)?pip(?:3)?\s+install\b.+"
    r"|uv\s+(?:run|sync|add|pip)\b.+"
    r"|npm\s+(?:install|run|exec)\b.+"
    r"|npx\b.+"
    r"|pnpm\s+(?:add|install|run|dlx)\b.+"
    r"|yarn\s+(?:add|install|run|dlx)\b.+"
    r"|bun\s+(?:add|install|run|runx)\b.+"
    r"|poetry\s+(?:add|install|run)\b.+"
    r"|conda\s+install\b.+"
    r"|brew\s+install\b.+"
    r"|apt(?:-get)?\s+install\b.+"
    r"|curl\b.+"
    r"|wget\b.+"
    r"|git\s+clone\b.+"
    r")$",
    re.IGNORECASE,
)
_SHELL_COMMAND_CONTINUATION_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9._/+:-]*)(?:\s+[A-Za-z0-9][A-Za-z0-9._/+:-]*)+$"
)
_PACKAGE_LIKE_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/+:-]*$")
_TABLE_SEPARATOR_PATTERN = re.compile(r"\S(?:\s{2,}|\t)\S")
_EQUATION_OPERATOR_PATTERN = re.compile(
    r"(?:=|≤|≥|≈|∝|×|÷|±|∑|∫|λ|α|β|γ|δ|θ|μ|σ|π|→|↔|\b(?:argmax|argmin|softmax|max|min)\b)",
    re.IGNORECASE,
)
_EQUATION_VARIABLE_PATTERN = re.compile(r"\b[A-Za-z](?:_[A-Za-z0-9]+)?\b")
_EQUATION_CODE_CUE_PATTERN = re.compile(
    r"\b(?:def|class|import|return|async|await|from|for|while|if|elif|else|print)\b",
    re.IGNORECASE,
)
_PROSE_LABEL_LINE_PATTERN = re.compile(r"^(?P<label>[A-Za-z][A-Za-z ]{0,40}?):\s+(?P<body>.+)$")
_PROSE_LABEL_TITLES = {
    "benefits",
    "best practice",
    "best practices",
    "context",
    "example",
    "examples",
    "how",
    "key takeaway",
    "key takeaways",
    "limitations",
    "overview",
    "problem",
    "rule of thumb",
    "solution",
    "summary",
    "use case",
    "use cases",
    "what",
    "when",
    "where",
    "who",
    "why",
}
_PROSE_CONTINUATION_STOPWORDS = {
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
_ACADEMIC_BODY_STARTER_WORDS = {
    "additionally",
    "a",
    "an",
    "as",
    "at",
    "by",
    "for",
    "from",
    "given",
    "however",
    "in",
    "instead",
    "it",
    "its",
    "on",
    "over",
    "most",
    "multiple",
    "our",
    "since",
    "similarly",
    "that",
    "the",
    "their",
    "these",
    "they",
    "this",
    "those",
    "to",
    "we",
    "when",
    "while",
    "with",
}


def _normalize_text(text: str) -> str:
    sanitized = re.sub(r"[\u00ad\u200b\u200c\u200d\u2060\ufeff]", "", text or "")
    return re.sub(r"\s+", " ", sanitized).strip()


def _normalize_multiline_text(text: str) -> str:
    lines = [_normalize_text(line) for line in text.splitlines()]
    normalized_lines: list[str] = []
    for line in lines:
        if not line:
            continue
        if normalized_lines:
            previous = normalized_lines[-1]
            line_start = line[:1]
            if line_start and (line_start.isalnum() or line_start == "("):
                if previous.endswith("\u2010"):
                    normalized_lines[-1] = previous[:-1] + line
                    continue
                if previous.endswith("-"):
                    normalized_lines[-1] = previous + line
                    continue
        normalized_lines.append(line)
    return "\n".join(normalized_lines)


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _bbox_to_json(bbox: tuple[float, float, float, float]) -> list[float]:
    return [round(value, 3) for value in bbox]


def _roman_to_int(value: str) -> int | None:
    numerals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    text = value.strip().casefold()
    if not text or any(char not in numerals for char in text):
        return None
    total = 0
    previous = 0
    for char in reversed(text):
        current = numerals[char]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total or None


def _is_name_like_token(token: str) -> bool:
    cleaned = re.sub(r"[^A-Za-z.'`-]", "", token).strip(".-")
    if not cleaned:
        return False
    return cleaned[:1].isupper() and (len(cleaned) == 1 or cleaned[1:].islower())


def _is_broken_word_fragment(text: str) -> bool:
    # A hyphen-split word tail with at most two letters ("Ar", "e") is a broken
    # extraction fragment, not the end of a heading title.
    return len(re.sub(r"[^A-Za-z]", "", text.rsplit("-", 1)[-1])) <= 2


def _looks_like_academic_prose_lead(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    tokens = normalized.split()
    if not tokens:
        return False

    first_token = tokens[0].strip("()[]{}.,;:")
    first_lowered = first_token.casefold()
    if first_lowered in _ACADEMIC_BODY_STARTER_WORDS:
        return True

    first_alpha = re.sub(r"[^A-Za-z]", "", first_token)
    if first_alpha and first_alpha.isupper() and len(first_alpha) >= 2:
        return False
    if any(char.isdigit() for char in first_token):
        return False

    sample = tokens[:8]
    long_lowercase = 0
    uppercaseish = 0
    numericish = 0
    for token in sample:
        stripped = token.strip("()[]{}.,;:")
        if not stripped:
            continue
        alpha_only = re.sub(r"[^A-Za-z]", "", stripped)
        if any(char.isdigit() for char in stripped):
            numericish += 1
            continue
        if alpha_only and alpha_only.isupper() and len(alpha_only) >= 2:
            uppercaseish += 1
            continue
        if alpha_only and alpha_only[:1].islower() and len(alpha_only) >= 3:
            long_lowercase += 1

    return uppercaseish + numericish <= 1 and long_lowercase >= 2


_MONOSPACE_FONT_PATTERNS = re.compile(
    r"(?i)(?:mono|courier|consol|menlo|fira\s*code|source\s*code|deja\s*vu\s*sans\s*mono"
    r"|liberation\s*mono|roboto\s*mono|inconsolata|ubuntu\s*mono|jetbrains\s*mono"
    r"|cascadia|hack|anonymous\s*pro|ibm\s*plex\s*mono|noto\s*sans\s*mono"
    r"|lucida\s*console|sf\s*mono|pragmata|iosevka|input\s*mono|droid\s*sans\s*mono)"
)


def _has_monospace_font(font_names: frozenset[str]) -> bool:
    """Return True if any font name looks like a monospace/code font."""
    return any(_MONOSPACE_FONT_PATTERNS.search(name) for name in font_names)


# Body-prose font name fragments commonly seen in published books. When
# one of these fonts is present alongside a monospace font, the block
# is body prose with inline-code highlights, not a code listing.
_PROSE_BODY_FONT_PATTERNS = re.compile(
    r"Baskerville|Garamond|Georgia|TimesNewRoman|Times-?New-?Roman"
    r"|FranklinGothic|Helvetica|Caslon|Sabon|Palatino|Minion|Bembo"
    r"|Lato|Roboto|SourceSans|SourceSerif|Charter|Cambria|PT-?Serif",
    re.IGNORECASE,
)


def _has_prose_body_font(font_names: frozenset[str]) -> bool:
    """Return True if the block uses any common book-prose body font.

    Used together with ``_has_monospace_font`` to distinguish real code
    blocks (mono-only) from body paragraphs that just happen to embed
    inline monospace identifiers.
    """
    return any(_PROSE_BODY_FONT_PATTERNS.search(name) for name in font_names)


_LIST_BULLET_PATTERN = re.compile(
    r"^(?:"
    r"[-•●◦▪▸►‣⁃∙◆◇○]\s+"                      # bullet chars
    r"|\d{1,3}[.)]\s+"                            # numbered: 1. or 1)
    r"|[a-z][.)]\s+"                               # lettered: a. or a)
    r"|[ivxlcdm]+[.)]\s+"                          # roman: i. ii. iii.
    r"|(?:step|item)\s+\d+[.:]\s+"                 # Step 1: / Item 2.
    r")",
    re.IGNORECASE,
)


def _looks_like_list_item(text: str, line_count: int) -> bool:
    """Return True if text starts with a list bullet/number pattern."""
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if _dense_toc_line_count(text) >= 3:
        return True
    if line_count > 8:
        return False
    return bool(_LIST_BULLET_PATTERN.match(normalized))


def _dense_toc_line_count(text: str) -> int:
    if "\n" not in text:
        return 0

    count = 0
    for raw_line in text.splitlines():
        line = _normalize_text(raw_line)
        if not line or len(line) > 180:
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        prefix, trailing_page = parts[0].strip(), parts[1].strip("()[]{}")
        if not prefix or not _PAGE_NUMBER_PATTERN.match(trailing_page):
            continue
        raw_trimmed = raw_line.strip()
        has_dotted_leader = raw_trimmed.count(".") >= 3
        has_dense_gap = bool(re.search(r"\S\s{2,}\S", raw_trimmed))
        if not has_dotted_leader and not has_dense_gap:
            continue
        if prefix.endswith(_TERMINAL_PUNCTUATION) and not has_dotted_leader:
            continue
        count += 1
        if count >= 3:
            return count
    return count


def _looks_like_code(text: str, line_count: int) -> bool:
    normalized_lines = _expanded_code_candidate_lines(text)
    effective_line_count = max(line_count, len(normalized_lines))
    if effective_line_count < 2 or len(normalized_lines) < 2:
        return False

    keyword_lines = sum(
        1
        for line in normalized_lines
        if _CODE_IMPORT_LINE_PATTERN.match(line) or _CODE_CONTROL_LINE_PATTERN.match(line)
    )
    punctuation_lines = sum(
        1
        for line in normalized_lines
        if any(marker in line for marker in ("{", "}", "=>", "::", "->"))
    )
    assignment_lines = sum(
        1
        for line in normalized_lines
        if "=" in line and "==" not in line and not re.search(r"\b(?:Table|Figure)\b", line)
    )
    semicolon_lines = sum(1 for line in normalized_lines if line.rstrip().endswith(";"))
    import_like_lines = sum(
        1
        for line in normalized_lines
        if _CODE_IMPORT_LINE_PATTERN.match(line)
    )
    command_lines = sum(1 for line in normalized_lines if _looks_like_shell_command_line(line))
    decorator_lines = sum(1 for line in normalized_lines if line.startswith("@"))
    comment_doc_lines = sum(
        1
        for line in normalized_lines
        if line.startswith("#") or '"""' in line or "'''" in line
    )
    embedded_code_lines = sum(1 for line in normalized_lines if _looks_like_embedded_code_line(line))
    prose_sentence_lines = sum(
        1
        for line in normalized_lines
        if len(line.split()) >= 6 and re.search(r"[.!?](?:[\"'\)\]\u201d\u2019])?$", line)
    )

    if (
        prose_sentence_lines >= 2
        and keyword_lines == 0
        and punctuation_lines == 0
        and assignment_lines == 0
        and decorator_lines == 0
        and import_like_lines == 0
        and embedded_code_lines < 2
    ):
        return False
    if import_like_lines >= 2:
        return True
    if command_lines >= 1 and (
        len(normalized_lines) <= 3
        or _looks_like_shell_command_continuation_line(normalized_lines[-1])
    ):
        return True
    if keyword_lines >= 2:
        return True
    if decorator_lines >= 1 and (keyword_lines >= 1 or embedded_code_lines >= 2):
        return True
    if comment_doc_lines >= 1 and (
        keyword_lines >= 1 or assignment_lines >= 1 or import_like_lines >= 1 or embedded_code_lines >= 2
    ):
        return True
    if embedded_code_lines >= max(2, len(normalized_lines) // 2) and prose_sentence_lines <= 1:
        return True
    if keyword_lines >= 1 and (assignment_lines >= 1 or punctuation_lines >= 1 or import_like_lines >= 1):
        return True
    if punctuation_lines >= 1 and (assignment_lines >= 1 or semicolon_lines >= 2 or import_like_lines >= 1):
        return True
    if assignment_lines >= 2 and any(re.match(r"^[A-Za-z_][A-Za-z0-9_.,\s]*\s*=", line) for line in normalized_lines):
        return True
    return False


def _split_inline_code_prose_line(text: str) -> tuple[str, str] | None:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) < 24:
        return None
    match = re.match(r"^(?P<code>.*?[}\]\)])\s+(?P<prose>[A-Z][A-Za-z].+)$", normalized)
    if match is None:
        return None
    code = match.group("code").strip()
    prose = match.group("prose").strip()
    if not prose or not _looks_like_sentence_prose_line(prose):
        return None
    if not (
        _CODE_IMPORT_LINE_PATTERN.match(code)
        or _CODE_CONTROL_LINE_PATTERN.match(code)
        or _looks_like_embedded_code_line(code)
        or code.endswith(("}", "]", ")"))
        or "=" in code
    ):
        return None
    return code, prose


def _split_inline_shell_command_prose_line(
    text: str,
    previous_lines: list[str] | None = None,
) -> tuple[str, str] | None:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 400:
        return None
    previous = ""
    for candidate in reversed(previous_lines or []):
        previous = _normalize_text(candidate)
        if previous:
            break
    if not (_looks_like_shell_command_line(normalized) or _looks_like_shell_command_line(previous)):
        return None
    tokens = normalized.split()
    if len(tokens) < 6:
        return None
    for index in range(2, len(tokens) - 3):
        command_tokens = tokens[:index]
        prose_tokens = tokens[index:]
        if len(command_tokens) < 2 or not all(_PACKAGE_LIKE_TOKEN_PATTERN.match(token) for token in command_tokens):
            continue
        prose = " ".join(prose_tokens).strip()
        if not _looks_like_sentence_prose_line(prose):
            continue
        return " ".join(command_tokens), prose
    return None


def _expanded_code_candidate_lines(text: str) -> list[str]:
    expanded: list[str] = []
    for line in text.splitlines():
        normalized = _normalize_text(line)
        if not normalized:
            continue
        split = _split_inline_code_prose_line(normalized)
        if split is None:
            shell_split = _split_inline_shell_command_prose_line(normalized, expanded)
            if shell_split is None:
                expanded.append(normalized)
                continue
            expanded.extend(part for part in shell_split if part)
            continue
        expanded.extend(part for part in split if part)
    return expanded


def _looks_like_labeled_prose_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 320:
        return False
    match = _PROSE_LABEL_LINE_PATTERN.match(normalized)
    if match is None:
        return False
    label = re.sub(r"\s+", " ", match.group("label")).strip().casefold()
    if label not in _PROSE_LABEL_TITLES:
        return False
    body = match.group("body").strip()
    if not body or any(marker in body for marker in ("{", "}", "[", "]", "=>", "::", "->")):
        return False
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", body.casefold())
    if len(tokens) < 8:
        return False
    stopword_hits = sum(1 for token in tokens if token in _PROSE_CONTINUATION_STOPWORDS)
    return stopword_hits >= max(2, len(tokens) // 7)


def _looks_like_structured_data_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 240:
        return False
    if _looks_like_labeled_prose_line(normalized):
        return False
    if re.fullmatch(r"[\[\]\{\}][,]?", normalized):
        return True
    return bool(
        re.match(
            r"^(?:[\"'][^\"'\n]{1,120}[\"']|[A-Za-z_][A-Za-z0-9_-]{0,80}|\d+)\s*:\s*\S+",
            normalized,
        )
    )


def _looks_like_shell_command_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 320:
        return False
    return bool(_SHELL_COMMAND_LINE_PATTERN.match(normalized))


def _looks_like_shell_command_continuation_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 240:
        return False
    if _looks_like_labeled_prose_line(normalized):
        return False
    if _looks_like_sentence_prose_line(normalized):
        return False
    if re.search(r"[.!?](?:[\"'\)\]\u201d\u2019])?$", normalized):
        return False
    return bool(_SHELL_COMMAND_CONTINUATION_PATTERN.match(normalized))


def _looks_like_splitworthy_single_line_code_fragment(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 800:
        return False
    if _looks_like_labeled_prose_line(normalized):
        return False
    if _looks_like_shell_command_line(normalized):
        return True
    if _looks_like_structured_data_line(normalized):
        return True
    if _CODE_IMPORT_LINE_PATTERN.match(normalized) or _CODE_CONTROL_LINE_PATTERN.match(normalized):
        return True
    if normalized.startswith(("@", "#", ">>>")):
        return True
    if not _looks_like_embedded_code_line(normalized):
        return False
    punctuation_hits = sum(
        1 for marker in ("{", "}", "[", "]", "(", ")", "=", ":", ",", "->", "=>", "::") if marker in normalized
    )
    return punctuation_hits >= 2


def _looks_like_embedded_code_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 220:
        return False
    if _looks_like_labeled_prose_line(normalized):
        return False
    if _looks_like_shell_command_line(normalized):
        return True
    if _looks_like_structured_data_line(normalized):
        return True
    if _CODE_IMPORT_LINE_PATTERN.match(normalized) or _CODE_CONTROL_LINE_PATTERN.match(normalized):
        return True
    if normalized.startswith(("@", "#", ">>>")):
        return True
    if re.match(r"^\|\s*[A-Za-z_{(\[\"']", normalized):
        return True
    if re.match(r"^(?:[\"'][^\"']+[\"']|\d+|[A-Za-z_][A-Za-z0-9_]*)\s*:\s*\S+", normalized):
        return True
    if any(marker in normalized for marker in ("{", "}", "=>", "::", "->")):
        return True
    if "=" in normalized and "==" not in normalized and not normalized.endswith((".", "?", "!")):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*\S+", normalized):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\(", normalized):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\([^()\n]*\)\s*$", normalized):
        return True
    if re.match(r"^(?:print|println|printf|fmt\.Print|console\.log|System\.out|await|yield|return)\(", normalized):
        return True
    return False


def _looks_like_code_continuation_line(text: str, previous_lines: list[str] | None = None) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 220:
        return False
    if _looks_like_labeled_prose_line(normalized):
        return False

    previous = ""
    for candidate in reversed(previous_lines or []):
        previous = _normalize_text(candidate)
        if previous:
            break
    if not previous:
        return False

    previous_joined = "\n".join(_normalize_text(candidate) for candidate in (previous_lines or []) if _normalize_text(candidate))

    if re.fullmatch(r"[\]\)\}][,]?", normalized):
        return True

    if normalized == "---" and previous.startswith("# ---"):
        return True

    if normalized.startswith("|"):
        recent_context = [
            _normalize_text(candidate)
            for candidate in (previous_lines or [])[-4:]
            if _normalize_text(candidate)
        ]
        if any(
            candidate.endswith(("(", "[", "{", ",", "\\", "+", "|", "="))
            or "=" in candidate
            or _looks_like_embedded_code_line(candidate)
            for candidate in recent_context
        ):
            return True

    if previous.endswith((",", "(", "[", "{")):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*[,\)]?$", normalized):
            return True
        if re.match(r"^[\"'][^\"'\n]{1,200}[\"'][,]?$", normalized):
            return True
        if normalized.startswith(("'", '"')):
            return True

    if previous.endswith(",") and re.match(
        r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$",
        normalized,
    ):
        return True

    if _looks_like_shell_command_line(previous) and _looks_like_shell_command_continuation_line(normalized):
        return True

    if _has_unterminated_triple_quoted_string(previous_joined):
        return True

    if _has_unterminated_quoted_string(previous_joined or previous, '"') or _has_unterminated_quoted_string(previous_joined or previous, "'"):
        return True

    if "#" in previous:
        token_count = len(re.findall(r"[A-Za-z][A-Za-z'-]*", normalized))
        if 1 <= token_count <= 10 and len(normalized) <= 72:
            return True

    return False


def _looks_like_sentence_prose_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized.split()) < 6:
        return False
    if _looks_like_embedded_code_line(normalized):
        return False
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
    if len(tokens) < 6:
        return False
    stopword_hits = sum(1 for token in tokens if token in _PROSE_CONTINUATION_STOPWORDS)
    if stopword_hits < max(2, len(tokens) // 6):
        return False
    return bool(re.search(r"[.!?](?:[\"'\)\]\u201d\u2019])?$", normalized) or normalized[:1].isupper())


def _looks_like_prose_line_group(lines: list[str]) -> bool:
    compact = " ".join(_normalize_text(line) for line in lines if _normalize_text(line))
    if not compact:
        return False
    return _looks_like_sentence_prose_line(compact)


def _looks_like_code_docstring_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if '"""' in normalized or "'''" in normalized:
        return True
    return bool(
        re.match(
            r"^(?:Args|Returns|Raises|Examples?|Parameters?|Yields|Attributes?)\b",
            normalized,
            re.IGNORECASE,
        )
    )


def _looks_like_code_docstring_text(text: str) -> bool:
    normalized_lines = _expanded_code_candidate_lines(text)
    if len(normalized_lines) < 3:
        return False
    quote_lines = sum(1 for line in normalized_lines if '"""' in line or "'''" in line)
    cue_lines = sum(1 for line in normalized_lines if _looks_like_code_docstring_line(line))
    typed_parameter_lines = sum(
        1
        for line in normalized_lines
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*:\s+\S+", line)
    )
    if quote_lines >= 1 and cue_lines >= 2:
        return True
    if cue_lines >= 2 and typed_parameter_lines >= 1:
        return True
    prose_lines = sum(1 for line in normalized_lines if len(line.split()) >= 4)
    return quote_lines >= 2 and prose_lines >= 3


def _has_unterminated_quoted_string(text: str, quote_char: str = '"') -> bool:
    if not text:
        return False
    quote_count = 0
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == quote_char:
            if (
                quote_char == "'"
                and index > 0
                and index + 1 < len(text)
                and text[index - 1].isalnum()
                and text[index + 1].isalnum()
            ):
                # Ignore apostrophes in prose/comment text like "LLM's" so they
                # do not masquerade as unterminated single-quoted strings.
                continue
            quote_count += 1
    return quote_count % 2 == 1


def _has_unterminated_triple_quoted_string(text: str) -> bool:
    if not text:
        return False
    return text.count('"""') % 2 == 1 or text.count("'''") % 2 == 1


def _looks_like_code_comment_text(text: str) -> bool:
    normalized_lines = _expanded_code_candidate_lines(text)
    if len(normalized_lines) < 2:
        return False
    comment_lines = sum(1 for line in normalized_lines if line.startswith("#"))
    if comment_lines < max(2, len(normalized_lines) - 1):
        return False
    alpha_tokens = sum(len(re.findall(r"[A-Za-z_][A-Za-z0-9_'-]*", line)) for line in normalized_lines)
    return alpha_tokens >= max(4, len(normalized_lines) * 2)


def _looks_like_table(line_count: int, lines: list[str]) -> bool:
    nonempty_lines = [line.rstrip("\n") for line in lines if _normalize_text(line)]
    if not nonempty_lines:
        return False
    if len(nonempty_lines) == 1:
        return _looks_like_flattened_table_text(nonempty_lines[0])
    if line_count < 2 or len(nonempty_lines) < 2:
        return False
    token_counts = [len(_normalize_text(line).split()) for line in nonempty_lines[:6]]
    if len(token_counts) < 2:
        return False
    prose_like_lines = sum(
        1
        for line in nonempty_lines[:6]
        if _looks_like_academic_prose_lead(line) and len(_normalize_text(line).split()) >= 5
    )
    separator_lines = sum(
        1
        for line in nonempty_lines[:6]
        if _TABLE_SEPARATOR_PATTERN.search(line) or "|" in line
    )
    numeric_lines = sum(
        1
        for line in nonempty_lines[:6]
        if len(re.findall(r"\b\d+(?:\.\d+)?\b", line)) >= 1
    )
    if separator_lines >= 2 and max(token_counts) - min(token_counts) <= 4:
        return True
    if separator_lines >= 1 and numeric_lines >= 2 and max(token_counts) - min(token_counts) <= 3:
        return True
    dense_numeric_lines = sum(
        1
        for line in nonempty_lines[:24]
        if (
            len(re.findall(r"\b\d+(?:\.\d+)?\b", line)) >= 2
            or "±" in line
            or _TABLE_BRACKETED_GROUP_PATTERN.search(line)
            or _TABLE_ID_EQUALS_PATTERN.search(line)
        )
    )
    if prose_like_lines >= 2 and separator_lines == 0:
        return False
    if dense_numeric_lines >= 3 and _looks_like_flattened_table_text(" ".join(nonempty_lines)):
        return True
    return False


def _looks_like_flattened_table_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized) < 80 or len(normalized) > 800:
        return False
    if _looks_like_caption_text(normalized) or _HEADING_PATTERN.match(normalized):
        return False
    if normalized.endswith((".", "?", "!")) and "±" not in normalized:
        return False
    leading_sample = " ".join(normalized.split()[:18])
    if _looks_like_academic_prose_lead(leading_sample):
        sentence_punctuation_hits = normalized.count(".") + normalized.count("?") + normalized.count("!")
        if sentence_punctuation_hits >= 1 and "±" not in normalized and "|" not in normalized:
            return False

    numeric_hits = len(re.findall(r"\b\d+(?:\.\d+)?\b", normalized))
    if numeric_hits < 6:
        return False

    plus_minus_hits = normalized.count("±")
    bracket_group_hits = len(_TABLE_BRACKETED_GROUP_PATTERN.findall(normalized))
    id_group_hits = len(_TABLE_ID_EQUALS_PATTERN.findall(normalized))
    percent_hits = normalized.count("%")
    header_cue = bool(_TABLE_HEADER_CUE_PATTERN.search(normalized))
    if not header_cue:
        return False

    if plus_minus_hits >= 2:
        return True
    if bracket_group_hits >= 2 and (percent_hits >= 1 or id_group_hits >= 1):
        return True
    if id_group_hits >= 2 and percent_hits >= 1:
        return True
    return False


def _looks_like_numeric_table_fragment(lines: list[str]) -> bool:
    nonempty_lines = [_normalize_text(line) for line in lines if _normalize_text(line)]
    if len(nonempty_lines) < 6:
        return False
    prose_like_lines = sum(
        1
        for line in nonempty_lines[:6]
        if _looks_like_academic_prose_lead(line) and len(line.split()) >= 5
    )
    if prose_like_lines >= 2:
        return False

    numeric_only_lines = sum(
        1 for line in nonempty_lines if re.fullmatch(r"\d+(?:\.\d+)?", line)
    )
    short_value_lines = sum(
        1
        for line in nonempty_lines
        if len(line.split()) <= 8 and len(re.findall(r"\b\d+(?:\.\d+)?\b", line)) >= 1
    )
    label_like_lines = sum(
        1
        for line in nonempty_lines
        if len(line.split()) <= 12 and bool(re.search(r"[A-Za-z]", line))
    )
    header_cue_lines = sum(
        1 for line in nonempty_lines[:8] if _TABLE_HEADER_CUE_PATTERN.search(line)
    )
    complexity_value_lines = sum(
        1
        for line in nonempty_lines
        if re.search(r"\bO\s*\(", line) or re.search(r"\b\d+(?:\.\d+)?\b", line)
    )

    if numeric_only_lines >= 4 and short_value_lines >= 6:
        return True
    if header_cue_lines >= 1 and short_value_lines >= 5 and label_like_lines >= 2:
        return True
    if header_cue_lines >= 2 and complexity_value_lines >= 2 and label_like_lines >= 4:
        return True
    return False


def _looks_like_equation(
    text: str,
    line_count: int,
    bbox: tuple[float, float, float, float],
    page_width: float,
) -> bool:
    normalized_lines = [_normalize_text(line) for line in text.splitlines() if _normalize_text(line)]
    if not normalized_lines:
        return False
    if not 1 <= len(normalized_lines) <= 3:
        return False
    if line_count > 4:
        return False
    if _looks_like_table(len(normalized_lines), normalized_lines):
        return False
    normalized = " ".join(normalized_lines)
    if normalized_lines and normalized_lines[0].startswith("#") and "=" in normalized:
        return False
    if _looks_like_code(text, line_count):
        return False
    if len(normalized) < 6 or len(normalized) > 120:
        return False
    if _looks_like_caption_text(normalized) or _HEADING_PATTERN.match(normalized):
        return False
    if normalized.endswith((".", "?", "!", ":", ";")):
        return False
    if _EQUATION_CODE_CUE_PATTERN.search(normalized):
        return False
    if re.search(r"[A-Za-z_]\.[A-Za-z_]", normalized):
        return False

    operator_hits = len(_EQUATION_OPERATOR_PATTERN.findall(normalized))
    math_punctuation_hits = sum(normalized.count(marker) for marker in "=+-*/^_()[]{}")
    if operator_hits < 1 or math_punctuation_hits < 3:
        return False

    tokens = normalized.split()
    if len(tokens) > 18:
        return False
    long_alpha_words = [
        token
        for token in (re.sub(r"[^A-Za-z]", "", part) for part in tokens)
        if len(token) >= 4
    ]
    if len(long_alpha_words) > 5:
        return False

    variable_hits = len(_EQUATION_VARIABLE_PATTERN.findall(normalized))
    digit_hits = len(re.findall(r"\b\d+(?:\.\d+)?\b", normalized))
    function_style_identity = bool(
        re.match(r"^[A-Za-z][A-Za-z0-9_]*(?:\([^)]{1,60}\))?\s*=\s*\S+", normalized)
    )
    if variable_hits < 3 and digit_hits < 1 and not function_style_identity:
        return False

    block_center = (bbox[0] + bbox[2]) / 2.0
    centered = abs(block_center - (page_width / 2.0)) <= page_width * 0.18
    inset = bbox[0] >= page_width * 0.15 and bbox[2] <= page_width * 0.85
    if not (centered or inset):
        return False
    return True


def _caption_matches_artifact_role(text: str, artifact_role: str) -> bool:
    normalized = _normalize_text(text)
    role = (artifact_role or "").strip().casefold()
    if role in {"image", "figure"}:
        # FIGURE blocks emitted by the clustering pass need caption matching
        # too — without this, the spatial fallback in `_link_artifact_captions`
        # rejects every candidate and the FIGURE stays orphan.
        return _looks_like_figure_caption(normalized)
    if role in {"table", "table_like"}:
        return _looks_like_table_caption(normalized)
    if role == "equation":
        return bool(_EQUATION_CAPTION_PATTERN.match(normalized))
    return False


def _looks_like_figure_caption(text: str) -> bool:
    return bool(_FIGURE_CAPTION_PATTERN.match(_normalize_text(text)))


def _looks_like_table_caption(text: str) -> bool:
    return bool(_TABLE_CAPTION_PATTERN.match(_normalize_text(text)))


def _looks_like_caption_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return (
        _looks_like_figure_caption(normalized)
        or _looks_like_table_caption(normalized)
        or bool(_EQUATION_CAPTION_PATTERN.match(normalized))
    )
