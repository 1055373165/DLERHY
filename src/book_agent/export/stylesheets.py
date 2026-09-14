"""Stylesheets embedded in rendered exports, kept as files under ``templates/``.

Each file holds one CSS fragment per line; the fragments are joined without
separators so the embedded CSS stays compact.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files


@cache
def load(name: str) -> str:
    text = (files("book_agent.export") / "templates" / name).read_text(encoding="utf-8")
    return "".join(text.splitlines())
