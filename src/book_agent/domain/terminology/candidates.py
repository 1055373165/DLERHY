"""What is not a glossary term: code identifiers, code tokens and generic verbs.

The terminology agent locked "@tool", "step()", "Event" and "execute" in the Agent book. None of them
is a term: an identifier is kept as the code spells it everywhere (the translation never renders it),
and a generic verb is translated by its sentence. Locking them only makes review flag sentences that
use the plain word. ``not_a_term`` says why a candidate is none of these; the extraction drops such
candidates and the term tools refuse them.

Names that look like code but are names ("Node.js", "LLM-as-a-Judge", "ReAct", "GraphRAG") stay
terms: dots and hyphens between capitalised words are not code, and a capitalised word is judged by
where the book uses it (``mostly_in_code``), not by its shape alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# What only code writes: a leading decorator "@", calls, snake_case, generics, indexing, paths.
# "primary/backup", "Precision@k", "nDCG" and "liveBook" are words; a camelCase name is judged by use.
# A call is a name right before "(": "step()", "run(task)"; "human-in-the-loop (HITL)" is prose.
_CODE_CHARS = re.compile(r"^@|[_{}\[\]<>=`\\$#]|::|->|\w\(|^\.?/|\.py\b")
# "self.memory", "os.path", "agent.run": a lowercase name, a dot, a name.
_DOTTED = re.compile(r"^[a-z][a-z0-9]*\.[A-Za-z_][\w.]*$")
# "ExecutionContext", "AgentResult": two capitalised parts glued, ending in lowercase (not ChromaDB, GraphRAG).
_PASCAL_IDENTIFIER = re.compile(r"^(?:[A-Z][a-z0-9]+){2,}$")

# Verbs, not nouns: "query", "process", "search", "plan" and "compute" are terms in some books.
GENERIC_VERBS = frozenset(
    """
    add apply ask build call check create define delete do edit execute fetch generate get give handle initialize
    invoke load look make open parse perform print produce put receive reset respond retrieve return run save send show
    start stop submit take try update use validate wait write
    """.split()
)


def not_a_term(source_term: str) -> str | None:
    """Why a candidate is not a glossary term by its spelling alone ("code identifier", ...), else None."""
    term = " ".join(str(source_term or "").split())
    if not term:
        return "empty"
    if _CODE_CHARS.search(term) or term.endswith("()") or term.startswith("--"):
        return "code identifier"
    if " " not in term and _DOTTED.match(term):
        return "code identifier"
    if term.islower() and " " not in term and term in GENERIC_VERBS:
        return "generic verb"
    return None


def mostly_in_code(source_term: str, prose_texts: Iterable[str], code_texts: Iterable[str]) -> bool:
    """A single word the book writes mostly as code: in code blocks, in `backticks`, or called ``word(``.

    "Event" and "ExecutionContext" in the Agent book are class names; "harness" and "ReAct" are words
    of its prose. Case-sensitive: the identifier is spelt exactly so.
    """
    term = " ".join(str(source_term or "").split())
    if not term or " " in term:
        return False
    word = re.compile(rf"(?<![\w@.]){re.escape(term)}(?![\w])")
    as_code = re.compile(rf"`[^`]*(?<![\w@.]){re.escape(term)}(?![\w])[^`]*`|(?<![\w@.]){re.escape(term)}\(")
    in_code = sum(len(word.findall(text or "")) for text in code_texts)
    in_prose = 0
    for text in prose_texts:
        text = text or ""
        coded = len(as_code.findall(text))
        in_code += coded
        in_prose += max(0, len(word.findall(text)) - coded)
    if in_code == 0:
        return False
    # A class-shaped name used in code at all is code; any other word must be code more than prose.
    if _PASCAL_IDENTIFIER.match(term):
        return in_code >= in_prose
    return in_code > in_prose
