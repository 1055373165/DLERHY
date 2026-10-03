"""Rejoin code identifiers a typesetter split with a space.

Technical books set identifiers in running text ("the ``final_answer`` field")
and break them where a line may end: after an underscore ("max_" + "tokens")
or a dot ("client.chat." + "completions"). Line joining puts a space into the
break ("max_ tokens"), and some text layers carry the space inside a line
already ("final_ answer"). A reader sees ``final_answer`` on the page.

Whether "word_ word" is one identifier is decided from the book itself, the
way ``hyphenation`` decides line-end hyphens: the pair is joined only when the
joined identifier appears whole inside a line somewhere in the book (usually
in a listing). No identifier list is involved.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

# An identifier with an underscore or a dotted path in it ("final_answer",
# "_prepare_llm_request", "chat.completions.create"). Plain words are not counted.
_IDENTIFIER_RE = re.compile(r"(?<![\w.])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
# A split identifier: a part ending in "_" or "." (the break), a space, the rest.
# Only the part and the space are consumed, so the rest can start the next split.
_SPLIT_IDENTIFIER_RE = re.compile(r"(?<![\w.])([A-Za-z_][\w.]*?[_.])\s+(?=([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*))")


def _is_identifier(token: str) -> bool:
    return "_" in token.strip("_") or ("." in token and not token.endswith("."))


def inline_identifier_counts(lines: Iterable[str]) -> Counter[str]:
    """Counts of the identifiers that sit whole inside one line.

    ``lines`` is the book's text lines in reading order. A part cut off at the
    end of a line ("max_") and the rest at the start of the next ("tokens")
    are not whole identifiers and are not counted.
    """
    counts: Counter[str] = Counter()
    for line in lines:
        stripped = line.strip()
        matches = list(_IDENTIFIER_RE.finditer(stripped))
        for index, match in enumerate(matches):
            token = match.group(0)
            if index == len(matches) - 1 and match.end() == len(stripped) and token[-1:] in {"_", "."}:
                continue
            if token.endswith("_") and not token.startswith("_"):
                # "final_ answer" inside one line: the first part is not an identifier of the book.
                following = stripped[match.end() : match.end() + 1]
                if following.isspace():
                    continue
            token = token.rstrip(".")
            if _is_identifier(token):
                counts[token] += 1
            # "p.model_dump()" writes ``model_dump`` whole as well.
            parts = token.split(".")
            for start in range(1, len(parts)):
                suffix = ".".join(parts[start:])
                if _is_identifier(suffix):
                    counts[suffix] += 1
    return counts


def rejoin_split_identifiers(text: str, identifier_counts: Counter[str]) -> str:
    """``text`` with each space that splits an identifier the book writes whole removed."""
    if not re.search(r"[_.]\s", text):
        return text

    def resolve(match: re.Match[str]) -> str:
        head, tail = match.group(1), match.group(2)
        if head.endswith(".") and not tail[:1].islower():
            # "the root. _find_in_subtree works": a sentence ends and one starts with a name.
            return match.group(0)
        token = head + tail
        while len(token) > len(head):
            if identifier_counts.get(token, 0) > 0:
                return head
            if "." not in token[len(head) :]:
                break
            token = token.rsplit(".", 1)[0]
        return match.group(0)

    previous = None
    while previous != text:
        # "a_ b_ c" needs two passes: the first join makes the second pair visible.
        previous = text
        text = _SPLIT_IDENTIFIER_RE.sub(resolve, text)
    return text
