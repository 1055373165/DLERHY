"""Code-versus-prose text heuristics and code artifact reflow for export rendering."""


from __future__ import annotations

import re

from book_agent.export.common import (
    _ACADEMIC_CITATION_PATTERN,
    _ACADEMIC_FRONTMATTER_MARKER_PATTERN,
    _APPENDIX_TITLE_PATTERN,
    _BOOK_STRUCTURAL_HEADING_TITLES,
    _CJK_APPENDIX_TITLE_PATTERN,
    _CJK_MAIN_CHAPTER_TITLE_PATTERN,
    _CODE_ASSIGNMENT_PATTERN,
    _CODE_BLOCK_KEYWORD_PATTERN,
    _FIGURE_CAPTION_PATTERN,
    _GLOSSARY_CODEISH_LABEL_STARTERS,
    _GLOSSARY_DEFINITION_LINE_PATTERN,
    _INLINE_CODE_LIKE_PATTERN,
    _LIST_MARKER_PATTERN,
    _MAIN_CHAPTER_TITLE_PATTERN,
    _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN,
    _ORDERED_LIST_MARKER_PATTERN,
    _PROSE_ARTIFACT_STOPWORDS,
    _REFERENCE_ENTRY_MARKER_PATTERN,
    _REFERENCE_LOCATOR_CONTINUATION_PATTERN,
    _REFERENCE_LOCATOR_PATTERN,
    _SINGLE_LINE_CODEISH_PATTERN,
    _URL_ONLY_PATTERN,
    _normalize_render_text,
)
from book_agent.export.models import (
    MergedRenderBlock,
)
from book_agent.ingestion.pdf.classify import (
    _PROSE_CONTINUATION_START_WORDS,
)
from book_agent.ingestion.text import (
    _TERMINAL_PUNCTUATION,
    _expanded_code_candidate_lines,
    _looks_like_code_docstring_line,
    _looks_like_embedded_code_line,
    _looks_like_labeled_prose_line,
    _looks_like_prose_line_group,
    _looks_like_sentence_prose_line,
    _looks_like_shell_command_continuation_line,
    _looks_like_shell_command_line,
    _looks_like_splitworthy_single_line_code_fragment,
    _looks_like_structured_data_line,
)


def normalize_markdown_code_artifact_text(
    text: str,
    *,
    block: MergedRenderBlock | None = None,
) -> str:
    return normalize_code_artifact_text(text, block=block)


def normalize_code_artifact_text(
    text: str,
    *,
    block: MergedRenderBlock | None = None,
) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if should_reflow_code_artifact_text(normalized):
        normalized = reflow_code_artifact_text(normalized)
    elif block is not None and should_preserve_code_artifact_layout(block, normalized):
        return normalized.rstrip("\n")
    return normalized.rstrip("\n")


def should_preserve_markdown_code_artifact_layout(
    block: MergedRenderBlock,
    text: str,
) -> bool:
    return should_preserve_code_artifact_layout(block, text)


def should_preserve_code_artifact_layout(
    block: MergedRenderBlock,
    text: str,
) -> bool:
    recovery_flags = {str(flag) for flag in block.source_metadata.get("recovery_flags") or []}
    if should_reflow_code_artifact_text(text):
        return False
    if {"cross_page_repaired", "export_refresh_split_code_restored"} & recovery_flags:
        return True
    return "export_code_blocks_merged" in recovery_flags and len(text.splitlines()) >= 8


def looks_like_prose_artifact_text(text: str, *, academic_paper: bool = False) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    normalized = " ".join(lines)
    if looks_like_reference_listing_text(text):
        return True
    if looks_like_mixed_code_prose_artifact_text(text):
        return False
    if looks_like_wrapped_prose_artifact_text(text):
        return True
    if looks_like_reference_literal(normalized):
        return False
    if looks_like_figure_caption(normalized):
        return False
    if looks_like_code_artifact_text(normalized, academic_paper=academic_paper):
        return False
    strong_codeish_lines = sum(
        1 for line in lines[:24] if looks_like_strong_codeish_line_for_artifact_rejection(line)
    )
    codeish_lines = sum(1 for line in lines[:24] if looks_like_codeish_line_for_artifact_rejection(line))
    if strong_codeish_lines >= 1 or codeish_lines >= 2:
        return False
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
    if len(tokens) < 8:
        return False
    stopword_hits = sum(1 for token in tokens if token in _PROSE_ARTIFACT_STOPWORDS)
    sentence_punctuation = len(re.findall(r"[.!?](?:[\"'\)\]\u201d\u2019])?(?:\s|$)", normalized))
    bullet_lines = sum(
        1
        for line in lines
        if line.startswith(("-", "*", "•")) and len(re.findall(r"[A-Za-z][A-Za-z'-]*", line)) >= 4
    )
    code_token_hits = len(_INLINE_CODE_LIKE_PATTERN.findall(normalized))
    if code_token_hits >= 2 and sentence_punctuation == 0 and bullet_lines == 0:
        return False
    if bullet_lines >= 2 and stopword_hits >= 4:
        return True
    return stopword_hits >= max(4, len(tokens) // 8) and (sentence_punctuation >= 1 or len(lines) >= 2)


def looks_like_mixed_code_prose_artifact_text(text: str) -> bool:
    raw_lines = _expanded_code_candidate_lines(text or "")
    if len(raw_lines) < 2:
        return False
    if not _looks_like_splitworthy_single_line_code_fragment(raw_lines[0]):
        return False
    trailing_lines = raw_lines[1:]
    return bool(trailing_lines) and _looks_like_prose_line_group(trailing_lines)


def looks_like_wrapped_prose_artifact_text(text: str) -> bool:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    if any(_looks_like_shell_command_line(line) for line in lines[:6]):
        return False
    if any(
        _CODE_BLOCK_KEYWORD_PATTERN.match(line)
        or _CODE_ASSIGNMENT_PATTERN.match(line)
        or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(line)
        or line.startswith(("#", "@"))
        for line in lines[:12]
    ):
        return False
    normalized = " ".join(lines)
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
    if len(tokens) < 24:
        return False
    stopword_hits = sum(1 for token in tokens if token in _PROSE_ARTIFACT_STOPWORDS)
    sentence_like_lines = sum(1 for line in lines if len(line.split()) >= 6)
    sentence_punctuation = len(re.findall(r"[.!?](?:[\"'\)\]\u201d\u2019])?(?:\s|$)", normalized))
    if stopword_hits < max(6, len(tokens) // 8):
        return False
    return sentence_punctuation >= 2 or sentence_like_lines >= 4


def looks_like_glossary_definition_line(text: str) -> bool:
    normalized = _normalize_render_text(text)
    if not normalized or len(normalized) > 260:
        return False
    if _looks_like_labeled_prose_line(normalized):
        return True
    if any(marker in normalized for marker in ("{", "}", "[", "]", "=>", "::", "->", "`")):
        return False
    if _looks_like_shell_command_line(normalized):
        return False
    if _CODE_ASSIGNMENT_PATTERN.match(normalized) or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(normalized):
        return False
    match = _GLOSSARY_DEFINITION_LINE_PATTERN.match(normalized)
    if match is None:
        return False
    label = re.sub(r"\s+", " ", match.group("label")).strip().casefold()
    if not label:
        return False
    if label.split(" ", 1)[0] in _GLOSSARY_CODEISH_LABEL_STARTERS:
        return False
    body = match.group("body").strip()
    if not body:
        return False
    body_tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", body.casefold())
    if len(body_tokens) < 4:
        return False
    stopword_hits = sum(1 for token in body_tokens if token in _PROSE_ARTIFACT_STOPWORDS)
    return stopword_hits >= max(1, len(body_tokens) // 5)


def looks_like_glossary_definition_text(text: str) -> bool:
    lines = [line.strip() for line in _expanded_code_candidate_lines(text or "") if line.strip()]
    if len(lines) < 2:
        return False
    if any(
        line.startswith(("#", "@"))
        or _looks_like_shell_command_line(line)
        or _CODE_ASSIGNMENT_PATTERN.match(line)
        or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(line)
        for line in lines[:12]
    ):
        return False
    label_indexes = [index for index, line in enumerate(lines[:12]) if looks_like_glossary_definition_line(line)]
    if not label_indexes:
        return False
    if label_indexes[0] != 0:
        return False
    for current_index, next_index in zip(label_indexes, [*label_indexes[1:], len(lines)]):
        segment_lines = lines[current_index:next_index]
        continuation_lines = segment_lines[1:]
        if continuation_lines:
            segment_body = re.sub(r"^[^:]+:\s*", "", " ".join(segment_lines), count=1)
            if not _looks_like_sentence_prose_line(segment_body):
                return False
        elif not looks_like_glossary_definition_line(segment_lines[0]):
            return False
    return True


def looks_like_codeish_line_for_artifact_rejection(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped or _LIST_MARKER_PATTERN.match(stripped) or _ORDERED_LIST_MARKER_PATTERN.match(stripped):
        return False
    if looks_like_strong_codeish_line_for_artifact_rejection(stripped):
        return True
    if re.search(r"\b(?:print|await|Runnable|ChatPromptTemplate|SystemMessage|HumanMessage)\s*\(", stripped):
        return True
    if stripped.endswith(("{", "[", "(")) and len(stripped.split()) <= 12:
        return True
    return False


def looks_like_strong_codeish_line_for_artifact_rejection(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped or _LIST_MARKER_PATTERN.match(stripped) or _ORDERED_LIST_MARKER_PATTERN.match(stripped):
        return False
    if _CODE_BLOCK_KEYWORD_PATTERN.match(stripped):
        return True
    if stripped.startswith(("#", "@")):
        return True
    if _CODE_ASSIGNMENT_PATTERN.match(stripped) or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(stripped):
        return True
    return False


def looks_like_prose_continuation_artifact_text(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    normalized = " ".join(lines)
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
    if len(tokens) < 10:
        return False
    lead = tokens[0]
    if lead not in _PROSE_CONTINUATION_START_WORDS and not normalized[:1].islower():
        return False
    return looks_like_prose_artifact_text(text)


def looks_like_reference_listing_text(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    lines = split_reference_listing_lines(normalized)
    if len(lines) < 2:
        return False
    numbered_entry_count = len(_REFERENCE_ENTRY_MARKER_PATTERN.findall(normalized))
    locator_count = len(_REFERENCE_LOCATOR_PATTERN.findall(normalized))
    if numbered_entry_count >= 2 and locator_count >= 1:
        return True
    numbered_lines = sum(1 for line in lines if _REFERENCE_ENTRY_MARKER_PATTERN.match(line))
    locator_lines = sum(
        1 for line in lines if _URL_ONLY_PATTERN.match(line) or _REFERENCE_LOCATOR_PATTERN.search(line)
    )
    if numbered_lines >= 1 and locator_lines >= 2:
        return True
    return locator_lines >= 1 and numbered_lines >= 1 and bool(
        lines and (_URL_ONLY_PATTERN.match(lines[0]) or _REFERENCE_LOCATOR_PATTERN.search(lines[0]))
    )


def should_reflow_code_artifact_text(text: str) -> bool:
    if not looks_like_code_artifact_text(text):
        return False
    raw_lines = [line.expandtabs(4).rstrip() for line in str(text or "").split("\n")]
    nonempty_lines = [line for line in raw_lines if line.strip()]
    if len(nonempty_lines) < 2:
        return False
    if any(line[:1] in {" ", "\t"} for line in nonempty_lines):
        return False
    if looks_like_stable_structured_code_layout(nonempty_lines):
        return False
    if any(len(line) >= 110 for line in nonempty_lines):
        return True
    if any(
        should_join_wrapped_code_line(nonempty_lines[index], nonempty_lines[index + 1])
        for index in range(len(nonempty_lines) - 1)
    ):
        return True
    for index, line in enumerate(nonempty_lines[:-1]):
        stripped = line.strip()
        if opens_python_block(stripped) and not nonempty_lines[index + 1].startswith((" ", "\t")):
            return True
    return False


def looks_like_stable_structured_code_layout(lines: list[str]) -> bool:
    stripped_lines = [line.strip() for line in lines if line.strip()]
    if len(stripped_lines) < 3:
        return False
    if any(
        _CODE_BLOCK_KEYWORD_PATTERN.match(line)
        or _CODE_ASSIGNMENT_PATTERN.match(line)
        or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(line)
        or line.startswith(("#", "@"))
        or re.match(r"^(?:async def |def |class |if |for |while |try:|with |except |finally:|return\b|await\b)", line)
        for line in stripped_lines
    ):
        return False
    structured_line_count = sum(
        1
        for line in stripped_lines
        if _looks_like_structured_data_line(line) or re.fullmatch(r"[\[\]\{\}][,]?", line)
    )
    brace_only_line_count = sum(1 for line in stripped_lines if re.fullmatch(r"[\[\]\{\}][,]?", line))
    if brace_only_line_count >= 2 and structured_line_count >= 3:
        return True
    return structured_line_count >= max(3, len(stripped_lines) - 1)


def looks_like_code_artifact_text(text: str, *, academic_paper: bool = False) -> bool:
    lines = [line.strip() for line in _expanded_code_candidate_lines(text) if line.strip()]
    if len(lines) < 2:
        return False
    if looks_like_reference_listing_text(text):
        return False
    if looks_like_glossary_definition_text(text):
        return False
    if looks_like_wrapped_prose_artifact_text(text):
        return False
    if academic_paper and (
        looks_like_academic_frontmatter_text(text)
        or looks_like_academic_prose_text(text)
    ):
        return False
    prose_sentence_lines = sum(
        1
        for line in lines[:24]
        if _looks_like_sentence_prose_line(line)
    )
    comment_lines = sum(1 for line in lines[:24] if line.startswith(("#", "//")))
    score = 0
    strong_cues = 0
    shell_command_active = False
    for line in lines[:24]:
        if _looks_like_shell_command_line(line):
            score += 3
            strong_cues += 1
            shell_command_active = True
            continue
        if shell_command_active and _looks_like_shell_command_continuation_line(line):
            score += 2
            strong_cues += 1
            continue
        shell_command_active = False
        if _looks_like_structured_data_line(line):
            score += 2
            strong_cues += 1
            continue
        if _CODE_BLOCK_KEYWORD_PATTERN.match(line):
            score += 3
            strong_cues += 1
            continue
        if line.startswith(("#", "@")):
            score += 2
            if line.startswith("#"):
                strong_cues += 1
        if _CODE_ASSIGNMENT_PATTERN.match(line):
            score += 2
            strong_cues += 1
        if any(token in line for token in ("print(", "->", "ChatPromptTemplate", "Runnable", "llm")):
            score += 1
            strong_cues += 1
        if any(token in line for token in ("(", ")", "[", "]", "{", "}")) and any(
            token in line for token in ("=", ":", ",")
        ):
            score += 1
    if comment_lines >= 2 and strong_cues >= 2:
        return True
    if prose_sentence_lines >= 2 and strong_cues == 0:
        return False
    if strong_cues == 0 and not any(line[:1] in {" ", "\t"} for line in lines if line.strip()):
        return False
    return score >= 4 and strong_cues >= 1


def looks_like_single_line_codeish_text(text: str) -> bool:
    normalized = _normalize_render_text(text)
    if not normalized or len(normalized) > 260:
        return False
    if looks_like_book_structural_heading_text(normalized):
        return False
    if looks_like_glossary_definition_line(normalized):
        return False
    if looks_like_structured_output_intro_prose_line(normalized):
        return False
    if _looks_like_shell_command_line(normalized):
        return True
    if _LIST_MARKER_PATTERN.match(normalized) or _ORDERED_LIST_MARKER_PATTERN.match(normalized):
        alpha_tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized)
        if len(alpha_tokens) >= 4:
            return False
    if looks_like_short_book_prose_line(normalized):
        return False
    if _SINGLE_LINE_CODEISH_PATTERN.search(normalized):
        return True
    if _CODE_ASSIGNMENT_PATTERN.match(normalized) or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(normalized):
        return True
    if any(token in normalized for token in ("(", ")", "{", "}", "[", "]")) and any(
        token in normalized for token in ("=", ":", "->")
    ):
        return len(normalized.split()) <= 28
    return False


def looks_like_structured_output_intro_prose_line(text: str) -> bool:
    normalized = _normalize_render_text(text)
    if not normalized:
        return False
    if not normalized.endswith((":", "：")):
        return False
    if any(token in normalized for token in ("{", "}", "[", "]", "=", "->", "=>", "::")):
        return False
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
    if len(tokens) < 8:
        return False
    stopword_hits = sum(1 for token in tokens if token in _PROSE_ARTIFACT_STOPWORDS)
    if stopword_hits < max(3, len(tokens) // 5):
        return False
    lowered = normalized.casefold()
    if not any(marker in lowered for marker in ("json", "xml", "yaml", "csv", "structured output")):
        return False
    return any(
        marker in lowered
        for marker in (
            "for example",
            "for instance",
            "formatted as",
            "represented as",
            "returned as",
            "output from",
            "output could",
            "could be",
        )
    )


def reflow_code_artifact_text(text: str) -> str:
    raw_lines = [line.expandtabs(4).rstrip() for line in text.split("\n")]
    expanded_lines = expand_inline_call_argument_lines(raw_lines)
    coalesced_lines = coalesce_wrapped_code_lines(expanded_lines)
    coalesced_lines = expand_inline_call_argument_lines(coalesced_lines)
    if any(line[:1] in {" ", "\t"} for line in coalesced_lines if line.strip()):
        return "\n".join(coalesced_lines).strip("\n")

    formatted_lines: list[str] = []
    indent_level = 0
    continuation_depth = 0
    pending_terminal_dedent = 0
    for raw_line in coalesced_lines:
        stripped = raw_line.strip()
        if not stripped:
            if formatted_lines and formatted_lines[-1] != "":
                formatted_lines.append("")
            continue
        if pending_terminal_dedent and not is_dedent_before_code_line(stripped):
            indent_level = max(indent_level - pending_terminal_dedent, 0)
            pending_terminal_dedent = 0
        if is_dedent_before_code_line(stripped):
            indent_level = max(indent_level - 1, 0)
        if is_top_level_code_reset(stripped):
            indent_level = 0
            continuation_depth = 0
        effective_indent = indent_level + min(continuation_depth, 2)
        if re.fullmatch(r"[\]\)\}][,]?", stripped):
            effective_indent = max(effective_indent - 1, 0)
        formatted_lines.append(f"{'    ' * max(effective_indent, 0)}{stripped}")
        continuation_depth = continuation_depth_after_code_line(stripped, continuation_depth)
        if opens_python_block(stripped):
            indent_level += 1
        if is_terminal_code_statement(stripped):
            pending_terminal_dedent = max(pending_terminal_dedent, 1)
    return "\n".join(formatted_lines).strip("\n")


def coalesce_wrapped_code_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for raw_line in lines:
        if not raw_line.strip():
            if merged and merged[-1] != "":
                merged.append("")
            continue
        quote_char, triple = code_string_state("\n".join(merged))
        if merged and should_join_wrapped_code_line(
            merged[-1],
            raw_line,
            quote_char=quote_char,
            triple_quoted=triple,
        ):
            merged[-1] = join_code_line_fragments(merged[-1], raw_line)
            continue
        merged.append(raw_line)
    return merged


def expand_inline_call_argument_lines(lines: list[str]) -> list[str]:
    expanded: list[str] = []
    for raw_line in lines:
        pending = [raw_line]
        while pending:
            current_line = pending.pop(0)
            stripped = current_line.strip()
            if stripped == "---" and expanded and expanded[-1].strip().startswith("# ---"):
                expanded[-1] = f"{expanded[-1].rstrip()} ---"
                continue
            split_lines = split_inline_call_argument_line(current_line)
            if split_lines is None:
                split_lines = split_inline_keyword_argument_tail_line(current_line)
            if split_lines is None:
                expanded.append(current_line)
                continue
            pending = list(split_lines) + pending
    return expanded


def split_inline_call_argument_line(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = re.match(
        r"^(?P<head>.*\()\s+(?P<tail>[A-Za-z_][A-Za-z0-9_]*\s*=.+)$",
        stripped,
    )
    if match is None:
        return None
    head = match.group("head").rstrip()
    tail = match.group("tail").lstrip()
    if not head or not tail:
        return None
    leading = line[: len(line) - len(line.lstrip())]
    return [f"{leading}{head}", f"{leading}{tail}"]


def split_inline_keyword_argument_tail_line(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = re.match(
        r"^(?P<head>.+?,)\s+(?P<tail>[A-Za-z_][A-Za-z0-9_]*\s*=.+)$",
        stripped,
    )
    if match is None:
        return None
    head = match.group("head").rstrip()
    tail = match.group("tail").lstrip()
    if "=" not in head and '"""' not in head and "'''" not in head:
        return None
    leading = line[: len(line) - len(line.lstrip())]
    return [f"{leading}{head}", f"{leading}{tail}"]


def looks_like_call_keyword_argument_line(stripped: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*.+,?$", stripped))


def should_join_wrapped_code_line(
    previous: str,
    current: str,
    *,
    quote_char: str | None = None,
    triple_quoted: bool = False,
) -> bool:
    prev = previous.rstrip()
    curr = current.lstrip()
    if not prev or not curr:
        return False
    if opens_python_block(prev.strip()):
        return False
    if quote_char is not None and should_join_open_string_line(
        prev.strip(),
        curr.strip(),
        quote_char=quote_char,
        triple_quoted=triple_quoted,
    ):
        return True
    if should_join_wrapped_comment_line(prev.strip(), curr.strip()):
        return True
    if should_join_wrapped_inline_comment_line(prev.strip(), curr.strip()):
        return True
    if curr.startswith("#"):
        return False
    if looks_like_call_keyword_argument_line(prev.strip()) and looks_like_call_keyword_argument_line(
        curr.strip()
    ):
        return False
    if curr.startswith((".", ",", ";")):
        return True
    if prev.endswith(("\\", ",", "+", "|", "=")):
        return True
    return False


def join_code_line_fragments(previous: str, current: str) -> str:
    prev = previous.rstrip()
    curr = current.lstrip()
    if not prev or not curr:
        return prev + curr
    if prev.endswith(("(", "[", "{")) or curr.startswith((".", ",", ";")):
        return f"{prev}{curr}"
    return f"{prev} {curr}"


def has_unterminated_quote(line: str) -> bool:
    quote_char, _triple_quoted = code_string_state(line)
    return quote_char is not None


def code_string_state(text: str) -> tuple[str | None, bool]:
    quote_char: str | None = None
    triple_quoted = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if not triple_quoted and quote_char is not None and char == "\\":
            escaped = True
            index += 1
            continue
        if quote_char is None:
            if text.startswith('"""', index) or text.startswith("'''", index):
                quote_char = text[index]
                triple_quoted = True
                index += 3
                continue
            if char in {'"', "'"}:
                if is_word_internal_apostrophe(text, index, char):
                    index += 1
                    continue
                quote_char = char
                triple_quoted = False
            index += 1
            continue
        if triple_quoted:
            if text.startswith(quote_char * 3, index):
                quote_char = None
                triple_quoted = False
                index += 3
                continue
            index += 1
            continue
        if char == quote_char:
            if is_word_internal_apostrophe(text, index, char):
                index += 1
                continue
            quote_char = None
        index += 1
    return quote_char, triple_quoted


def is_word_internal_apostrophe(text: str, index: int, quote_char: str) -> bool:
    if quote_char != "'":
        return False
    if index <= 0 or index >= len(text) - 1:
        return False
    return text[index - 1].isalnum() and text[index + 1].isalnum()


def should_join_wrapped_comment_line(previous: str, current: str) -> bool:
    if not previous.startswith("#"):
        return False
    if current.startswith("#"):
        return False
    if _looks_like_structured_data_line(current):
        return False
    if _looks_like_embedded_code_line(current):
        return False
    if _CODE_BLOCK_KEYWORD_PATTERN.match(current) or _CODE_ASSIGNMENT_PATTERN.match(current):
        return False
    return True


def should_join_wrapped_inline_comment_line(previous: str, current: str) -> bool:
    if "#" not in previous or previous.startswith("#"):
        return False
    if current.startswith("#"):
        return False
    if _looks_like_structured_data_line(current):
        return False
    if _looks_like_embedded_code_line(current):
        return False
    if _CODE_BLOCK_KEYWORD_PATTERN.match(current) or _CODE_ASSIGNMENT_PATTERN.match(current):
        return False
    return True


def should_join_open_string_line(
    previous: str,
    current: str,
    *,
    quote_char: str,
    triple_quoted: bool,
) -> bool:
    if not previous or not current:
        return False
    if not triple_quoted:
        return True
    if previous.endswith(quote_char * 3) and previous.strip() == quote_char * 3:
        return False
    if current.strip() == quote_char * 3:
        return False
    if _LIST_MARKER_PATTERN.match(current) or _ORDERED_LIST_MARKER_PATTERN.match(current):
        return False
    if _looks_like_code_docstring_line(current):
        return False
    if _looks_like_embedded_code_line(current) and not current.startswith(("(", "{", "[", '"', "'")):
        return False
    if previous.endswith(_TERMINAL_PUNCTUATION) and current[:1].isupper():
        return False
    if previous.endswith(",") or current[:1].islower():
        return True
    return len(previous) >= 72


def delimiter_balance(line: str) -> int:
    return sum(line.count(token) for token in "([{") - sum(line.count(token) for token in ")]}")


def is_dedent_before_code_line(stripped: str) -> bool:
    lowered = stripped.lower()
    # Python
    if lowered.startswith(("elif ", "else:", "except", "finally:")):
        return True
    # C-family / Java / JS / Go
    if stripped.startswith(("} else", "} catch", "} finally", "} elif")):
        return True
    # Standalone closing brace (dedent)
    if stripped == "}" or stripped == "},":
        return True
    # Ruby
    if lowered.startswith(("elsif ", "rescue ", "ensure ", "end")):
        return True
    # Rust
    if stripped.startswith("} else"):
        return True
    return False


def is_top_level_code_reset(stripped: str) -> bool:
    lowered = stripped.lower()
    # Python
    if lowered.startswith(("async def ", "def ", "class ", "from ", "import ", "@", "if __name__")):
        return True
    if stripped.startswith("# ---"):
        return True
    # Go
    if lowered.startswith(("func ", "type ", "var ", "const ", "package ")):
        return True
    # Rust
    if lowered.startswith(("pub fn ", "fn ", "pub struct ", "struct ", "impl ", "pub enum ", "enum ", "mod ", "use ")):
        return True
    # Java / C# / Kotlin
    if lowered.startswith(("public ", "private ", "protected ", "static ", "interface ", "abstract ")):
        return True
    # C/C++
    if re.match(r"^(?:int|void|char|double|float|bool|auto|unsigned)\s+\w+\s*\(", stripped):
        return True
    if lowered.startswith(("#include", "#define", "#ifndef", "#ifdef", "namespace ", "template ")):
        return True
    # JavaScript/TypeScript
    if lowered.startswith(("export ", "function ", "const ", "let ", "var ")):
        return True
    # Ruby
    if lowered.startswith(("module ", "require ")):
        return True
    return False


def opens_python_block(stripped: str) -> bool:
    lowered = stripped.lower()
    # Python: colon-terminated blocks
    if stripped.endswith(":") and lowered.startswith(
        ("async def ", "def ", "class ", "if ", "elif ", "else:", "for ", "while ", "try:", "except", "finally:", "with ")
    ):
        return True
    # C-family / Go / Rust / Java / JS: brace-terminated blocks
    if stripped.endswith("{"):
        return True
    # Ruby: do-blocks
    if stripped.endswith(("do", "do |")):
        return True
    return False


def continuation_depth_after_code_line(stripped: str, current_depth: int) -> int:
    next_depth = max(0, current_depth + delimiter_balance(stripped))
    if stripped.endswith("\\"):
        next_depth = max(next_depth, 1)
    return min(next_depth, 8)


def is_terminal_code_statement(stripped: str) -> bool:
    if delimiter_balance(stripped) > 0 or has_unterminated_quote(stripped):
        return False
    lowered = stripped.lower()
    # Python / Rust / Go / JS / Java / C / Ruby
    return bool(re.match(r"^(?:return|raise|break|continue|pass|throw|yield|panic!?)\b(?!\s*:)", lowered))


def looks_like_reference_literal(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    patterns = (
        r"^https?://\S+$",
        r"^www\.\S+$",
        r"^(?:/|\.{1,2}/)[^\s]+$",
        r"^[A-Z][A-Z0-9_]{1,}(?:=[^\s]+)?$",
        r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.\-/]+$",
    )
    return any(re.match(pattern, normalized) for pattern in patterns)


def looks_like_academic_frontmatter_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if not normalized:
        return False
    lower = normalized.casefold()
    if "abstract" in lower and _ACADEMIC_FRONTMATTER_MARKER_PATTERN.search(normalized):
        return True
    return "@" in normalized and bool(
        re.search(
            r"\b(?:university|institute|department|school|laboratory|center|centre|society|sciences?)\b",
            lower,
        )
    )


def looks_like_academic_prose_text(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    normalized = " ".join(lines)
    lower = normalized.casefold()
    if looks_like_academic_frontmatter_text(normalized):
        return False
    if any(marker in lower for marker in ("def ", "class ", "import ", "from ", "return ", "async def ")):
        return False
    citation_hits = len(_ACADEMIC_CITATION_PATTERN.findall(normalized))
    prose_hits = sum(
        1
        for token in (
            "in this section",
            "in this work",
            "in this context",
            "we propose",
            "we present",
            "we evaluate",
            "we demonstrate",
            "we show",
            "our approach",
            "for example",
            "recent years",
            "dataset",
            "results",
            "baselines",
            "human expert",
            "classifier",
        )
        if token in lower
    )
    sentence_punctuation = len(re.findall(r"[.!?](?:\s|$)", normalized))
    return citation_hits >= 1 or prose_hits >= 2 or sentence_punctuation >= 2


def looks_like_book_structural_heading_text(text: str | None) -> bool:
    normalized = _normalize_render_text(text)
    if not normalized:
        return False
    lowered = normalized.casefold()
    if lowered in _BOOK_STRUCTURAL_HEADING_TITLES:
        return True
    if _MAIN_CHAPTER_TITLE_PATTERN.match(normalized) or _APPENDIX_TITLE_PATTERN.match(normalized):
        return True
    if _CJK_MAIN_CHAPTER_TITLE_PATTERN.match(normalized) or _CJK_APPENDIX_TITLE_PATTERN.match(normalized):
        return True
    return False


def looks_like_figure_caption(text: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    return bool(normalized and _FIGURE_CAPTION_PATTERN.match(normalized))


def looks_like_short_book_prose_line(text: str | None) -> bool:
    normalized = _normalize_render_text(text)
    if not normalized:
        return False
    if looks_like_book_structural_heading_text(normalized):
        return False
    if any(token in normalized for token in ("`", "{", "}", "[", "]", "->", "=>", "::")):
        return False
    if _SINGLE_LINE_CODEISH_PATTERN.search(normalized):
        return False
    if _CODE_ASSIGNMENT_PATTERN.match(normalized) or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(normalized):
        return False
    alpha_tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized)
    if not 3 <= len(alpha_tokens) <= 10:
        return False
    if not re.search(r"[.!?](?:[\"'\)\]\u201d\u2019])?$", normalized):
        return False
    return True


def split_reference_listing_lines(text: str) -> list[str]:
    normalized = str(text or "")
    if not normalized.strip():
        return []
    prepared = re.sub(
        r"(?<!^)(?<!\n)\s*(?=(?:\d+[.)])[\s\u200b\ufeff]+)",
        "\n",
        normalized,
    )
    prepared = re.sub(
        r"(?<=\S)\s+(?=(?:https?://|doi\.org/|arxiv:))",
        "\n",
        prepared,
        flags=re.IGNORECASE,
    )
    prepared = re.sub(
        r"(?<![\s(])(?=(?:https?://|doi\.org/|arxiv:))",
        "\n",
        prepared,
        flags=re.IGNORECASE,
    )
    raw_lines = [line.strip() for line in prepared.splitlines() if line.strip()]
    merged_lines: list[str] = []
    for line in raw_lines:
        if merged_lines and should_merge_reference_locator_continuation(merged_lines[-1], line):
            merged_lines[-1] = merged_lines[-1].rstrip() + line.lstrip()
            continue
        if merged_lines and should_merge_reference_title_continuation(merged_lines[-1], line):
            separator = "" if merged_lines[-1].endswith("-") else " "
            merged_lines[-1] = merged_lines[-1].rstrip("-") + separator + line.lstrip()
            continue
        merged_lines.append(line)
    return merged_lines


def should_merge_reference_locator_continuation(previous_line: str, current_line: str) -> bool:
    previous = previous_line.strip()
    current = current_line.strip()
    if not previous or not current:
        return False
    if _REFERENCE_ENTRY_MARKER_PATTERN.match(current):
        return False
    if not (_URL_ONLY_PATTERN.match(previous) or _REFERENCE_LOCATOR_PATTERN.search(previous)):
        return False
    if _URL_ONLY_PATTERN.match(current):
        return True
    return bool(
        _REFERENCE_LOCATOR_CONTINUATION_PATTERN.fullmatch(current)
        and " " not in current
        and not current.endswith((":", "："))
    )


def should_merge_reference_title_continuation(previous_line: str, current_line: str) -> bool:
    previous = previous_line.strip()
    current = current_line.strip()
    if not previous or not current:
        return False
    if _REFERENCE_ENTRY_MARKER_PATTERN.match(current):
        return False
    if _URL_ONLY_PATTERN.match(current) or _REFERENCE_LOCATOR_PATTERN.search(current):
        return False
    if _URL_ONLY_PATTERN.match(previous) or _REFERENCE_LOCATOR_PATTERN.search(previous):
        return False
    return True
